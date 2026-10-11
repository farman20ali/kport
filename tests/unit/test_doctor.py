"""
tests/test_doctor.py — Focused regression tests for the kport doctor command (Phase 2E).

Tests use mocks/fixtures only — no dependency on the developer's real machine state.
Each test verifies a specific behaviour guarantee rather than testing for coverage.
"""
from __future__ import annotations

import argparse
import json
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from kport.cli import (
    EXIT_OK,
    handle_doctor,
    handle_product_command,
)
from kport.diagnostics import (
    _doctor_capabilities,
    _doctor_connection_summary,
    _doctor_docker_section,
    _doctor_listener_findings,
    _doctor_process_findings,
)
from kport.inspectors.base import ConnectionInfo, PortBinding, ProcessInfo
from tests.conftest import FakeInspector, _args

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _binding(port: int, pid: int = 1234, name: str = "python.exe",
             laddr: str = "127.0.0.1") -> PortBinding:
    return PortBinding(
        port=port,
        family="inet",
        laddr=f"{laddr}:{port}",
        pid=pid,
        process_name=name,
        state="LISTEN",
        proto="tcp",
    )


def _conn(state: str = "ESTABLISHED", pid: int = 1234) -> ConnectionInfo:
    return ConnectionInfo(
        pid=pid,
        process_name="python.exe",
        proto="tcp",
        local_address="127.0.0.1",
        local_port=8000,
        remote_address="127.0.0.1",
        remote_port=5432,
        state=state,
    )


def _doctor_args(**kw) -> argparse.Namespace:
    return _args(command="doctor", **kw)


class FakeInspectorWithConnections(FakeInspector):
    """FakeInspector that also returns connections and process info."""

    def __init__(self, listening=None, connections=None, process_info=None, **kw):
        super().__init__(listening=listening or [], **kw)
        self._connections = connections or []
        self._info = process_info or {}

    def list_connections(self):
        return self._connections

    def get_process_info(self, pid: int):
        return self._info.get(pid)


# ---------------------------------------------------------------------------
# 1. Command registration — doctor is a valid subcommand
# ---------------------------------------------------------------------------

def test_doctor_is_registered():
    """kport doctor must be a recognised subcommand and not raise."""
    from kport.cli import main
    # --help returns exit 0; just ensure the subcommand is parsed without crash
    try:
        main(["doctor", "--help"])
    except SystemExit as exc:
        assert exc.code == 0  # --help always exits 0


def test_doctor_command_dispatches_correctly(capsys):
    """handle_product_command with command='doctor' must call handle_doctor."""
    inspector = FakeInspectorWithConnections()
    args = _doctor_args(json=True)
    with patch("kport.diagnostics.list_docker_mappings", return_value=[]), \
         patch("kport.diagnostics.docker_available", return_value=False):
        rc = handle_product_command(args, inspector)
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["schema_version"] == 1
    assert envelope["command"] == "doctor"


# ---------------------------------------------------------------------------
# 2. Healthy / normal environment
# ---------------------------------------------------------------------------

def test_doctor_healthy_environment_json(capsys):
    """Normal environment: JSON schema is complete and well-formed."""
    inspector = FakeInspectorWithConnections(
        listening=[_binding(8000, laddr="127.0.0.1")],
        connections=[_conn("ESTABLISHED"), _conn("LISTEN")],
    )
    args = _doctor_args(json=True)
    with patch("kport.diagnostics.docker_available", return_value=False):
        rc = handle_doctor(args, inspector)
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    env = json.loads(out)
    data = env["data"]
    # Required top-level keys
    for key in ("platform", "capabilities", "listeners", "connection_summary",
                "processes", "docker", "findings"):
        assert key in data, f"Missing key: {key}"
    # Connection summary integrity
    cs = data["connection_summary"]
    assert cs["total"] == 2
    assert cs["ESTABLISHED"] == 1
    assert cs["LISTEN"] == 1


def test_doctor_healthy_environment_text(capsys):
    """Normal environment text output must include all section headers."""
    inspector = FakeInspectorWithConnections(
        listening=[_binding(8000, laddr="127.0.0.1")],
        connections=[],
    )
    args = _doctor_args(json=False)
    with patch("kport.diagnostics.docker_available", return_value=False):
        handle_doctor(args, inspector)
    out = capsys.readouterr().out
    for header in ("PLATFORM", "LISTENERS", "CONNECTION SUMMARY",
                   "PROCESS", "DOCKER", "FINDINGS"):
        assert header in out, f"Missing section header: {header}"


# ---------------------------------------------------------------------------
# 3. Listener findings — wildcard detection
# ---------------------------------------------------------------------------

def test_listener_wildcard_finding_0000():
    """0.0.0.0 binding produces a WARNING/listener finding."""
    b = _binding(8080, laddr="0.0.0.0")
    findings: list[dict] = []
    listeners = _doctor_listener_findings([b], findings)
    assert listeners[0]["wildcard"] is True
    warning = [f for f in findings if f["severity"] == "WARNING" and f["category"] == "listener"]
    assert warning, "Expected WARNING finding for 0.0.0.0 listener"
    assert "8080" in warning[0]["message"]


def test_listener_wildcard_finding_ipv6():
    """:: binding produces a WARNING/listener finding."""
    b = _binding(8080, laddr="::")
    findings: list[dict] = []
    listeners = _doctor_listener_findings([b], findings)
    assert listeners[0]["wildcard"] is True
    warning = [f for f in findings if f["severity"] == "WARNING"]
    assert warning


def test_listener_localhost_no_warning():
    """127.0.0.1 binding must NOT generate a wildcard WARNING."""
    b = _binding(8080, laddr="127.0.0.1")
    findings: list[dict] = []
    listeners = _doctor_listener_findings([b], findings)
    assert listeners[0]["localhost_only"] is True
    assert listeners[0]["wildcard"] is False
    warnings = [f for f in findings if f["severity"] == "WARNING" and f["category"] == "listener"]
    assert not warnings, "Localhost listener should not produce a WARNING"


# ---------------------------------------------------------------------------
# 4. Wildcard binding detection (dual-stack)
# ---------------------------------------------------------------------------

def test_dual_stack_overlap_info_finding():
    """Same port with both 0.0.0.0 and :: produces an INFO/dual-stack finding."""
    b4 = _binding(8080, laddr="0.0.0.0")
    b6 = _binding(8080, laddr="::")
    findings: list[dict] = []
    _doctor_listener_findings([b4, b6], findings)
    dual = [f for f in findings if "dual-stack" in f["message"]]
    assert dual, "Expected dual-stack INFO finding"
    assert dual[0]["severity"] == "INFO"


def test_no_dual_stack_when_only_ipv4():
    """Single IPv4 wildcard must not produce a dual-stack finding."""
    b = _binding(8080, laddr="0.0.0.0")
    findings: list[dict] = []
    _doctor_listener_findings([b], findings)
    dual = [f for f in findings if "dual-stack" in f["message"]]
    assert not dual


# ---------------------------------------------------------------------------
# 5. Service-managed process finding
# ---------------------------------------------------------------------------

def test_service_managed_process_finding(capsys):
    """Service-managed process produces an INFO/service finding."""
    pi = ProcessInfo(pid=1234, name="nginx")
    inspector = FakeInspectorWithConnections(
        listening=[_binding(80, pid=1234, laddr="0.0.0.0")],
        process_info={1234: pi},
    )
    pm_info = {
        "manager": "windows-service",
        "name": "W3SVC",
        "managed_by": "windows-service:W3SVC",
        "warning": "...",
    }
    findings: list[dict] = []
    with patch("kport.diagnostics.detect_process_manager", return_value=pm_info):
        entries = _doctor_process_findings(inspector.list_listening(), inspector, findings)

    svc_findings = [f for f in findings if f["category"] == "service"]
    assert svc_findings, "Expected a service finding"
    assert "windows-service" in svc_findings[0]["message"]
    assert entries[0]["service_manager"]["manager"] == "windows-service"


def test_no_service_finding_for_plain_process():
    """Process without a service manager must not produce a service finding."""
    pi = ProcessInfo(pid=1234, name="python.exe")
    inspector = FakeInspectorWithConnections(
        listening=[_binding(8000, pid=1234, laddr="127.0.0.1")],
        process_info={1234: pi},
    )
    findings: list[dict] = []
    with patch("kport.diagnostics.detect_process_manager", return_value=None):
        entries = _doctor_process_findings(inspector.list_listening(), inspector, findings)
    svc_findings = [f for f in findings if f["category"] == "service"]
    assert not svc_findings
    assert entries[0]["service_manager"] is None


# ---------------------------------------------------------------------------
# 6. Project detection integration
# ---------------------------------------------------------------------------

def test_project_context_finding():
    """Process with a resolvable CWD produces an INFO/project finding."""
    from kport.project import ProjectInfo

    pi = ProcessInfo(pid=1234, name="python.exe", cwd="/home/user/myapp")
    inspector = FakeInspectorWithConnections(
        listening=[_binding(8000, pid=1234, laddr="127.0.0.1")],
        process_info={1234: pi},
    )
    fake_project = ProjectInfo(
        git_root="/home/user/myapp",
        project_name="myapp",
        branch="main",
        remote_origin="https://github.com/user/myapp",
    )
    findings: list[dict] = []
    with patch("kport.diagnostics.detect_process_manager", return_value=None), \
         patch("kport.diagnostics.resolve_project", return_value=fake_project):
        entries = _doctor_process_findings(inspector.list_listening(), inspector, findings)

    proj_findings = [f for f in findings if f["category"] == "project"]
    assert proj_findings
    assert "myapp" in proj_findings[0]["message"]
    assert "main" in proj_findings[0]["message"]
    assert entries[0]["project"]["project_name"] == "myapp"


def test_no_project_finding_when_no_cwd():
    """Process with no CWD must not produce a project finding."""
    pi = ProcessInfo(pid=1234, name="python.exe", cwd=None)
    inspector = FakeInspectorWithConnections(
        listening=[_binding(8000, pid=1234, laddr="127.0.0.1")],
        process_info={1234: pi},
    )
    findings: list[dict] = []
    with patch("kport.diagnostics.detect_process_manager", return_value=None), \
         patch("kport.diagnostics.resolve_project", return_value=None):
        entries = _doctor_process_findings(inspector.list_listening(), inspector, findings)
    proj_findings = [f for f in findings if f["category"] == "project"]
    assert not proj_findings
    assert entries[0]["project"] is None


# ---------------------------------------------------------------------------
# 7. Docker port context integration
# ---------------------------------------------------------------------------

def test_docker_port_conflict_finding():
    """Container whose host_port matches a listener produces an INFO/docker finding."""
    from kport.docker_engine import DockerPortMapping

    b = _binding(8080, laddr="0.0.0.0")
    mock_mapping = DockerPortMapping(
        container_id="abc123containerid",
        container_name="myapp",
        image="myapp:latest",
        status="Up",
        host_ip="0.0.0.0",
        host_port=8080,
        container_port=80,
        proto="tcp",
    )
    findings: list[dict] = []
    with patch("kport.diagnostics.docker_available", return_value=True), \
         patch("kport.diagnostics.list_docker_mappings", return_value=[mock_mapping]):
        section = _doctor_docker_section([b], findings)
    assert section["available"] is True
    assert len(section["containers"]) == 1
    docker_findings = [f for f in findings if f["category"] == "docker"]
    assert docker_findings
    assert "8080" in docker_findings[0]["message"]
    assert "myapp" in docker_findings[0]["message"]


def test_docker_unavailable_does_not_crash():
    """Docker not on PATH must produce an INFO finding, not crash."""
    findings: list[dict] = []
    with patch("kport.diagnostics.docker_available", return_value=False):
        section = _doctor_docker_section([], findings)
    assert section["available"] is False
    info = [f for f in findings if f["category"] == "docker" and f["severity"] == "INFO"]
    assert info
    assert "not found" in info[0]["message"].lower()


def test_docker_daemon_not_running():
    """Docker CLI present but daemon unreachable must produce an INFO finding."""
    findings: list[dict] = []
    with patch("kport.diagnostics.docker_available", return_value=True), \
         patch("kport.diagnostics.list_docker_mappings", side_effect=Exception("connection refused")):
        section = _doctor_docker_section([], findings)
    assert section.get("available") is True
    assert section.get("daemon_accessible") is False
    info = [f for f in findings if f["category"] == "docker"]
    assert info


# ---------------------------------------------------------------------------
# 8. Connection summary
# ---------------------------------------------------------------------------

def test_connection_summary_counts():
    """Connection summary correctly tallies each state."""
    conns = [
        _conn("ESTABLISHED"), _conn("ESTABLISHED"),
        _conn("LISTEN"),
        _conn("TIME_WAIT"),
        _conn("CLOSE_WAIT"),
        _conn("SYN_SENT"),  # → other
    ]
    cs = _doctor_connection_summary(conns)
    assert cs["total"] == 6
    assert cs["ESTABLISHED"] == 2
    assert cs["LISTEN"] == 1
    assert cs["TIME_WAIT"] == 1
    assert cs["CLOSE_WAIT"] == 1
    assert cs["other"] == 1


def test_connection_summary_empty():
    """Empty connections list must produce all-zero summary."""
    cs = _doctor_connection_summary([])
    assert cs["total"] == 0
    assert cs["ESTABLISHED"] == 0
    assert cs["LISTEN"] == 0


# ---------------------------------------------------------------------------
# 9. Privilege / capability reporting
# ---------------------------------------------------------------------------

def test_capability_psutil_not_available():
    """When psutil is absent, capabilities must report a limitation."""
    caps = _doctor_capabilities({
        "os": "Linux", "psutil_available": False, "psutil_accessible": False
    })
    assert caps["limitations"]
    assert any("fallback" in lim for lim in caps["limitations"])


def test_capability_psutil_accessible():
    """When psutil is accessible, no limitations should appear."""
    caps = _doctor_capabilities({
        "os": "Linux", "psutil_available": True, "psutil_accessible": True
    })
    assert caps["limitations"] == []


def test_capability_windows_note():
    """Windows must produce a note about ctypes resolution."""
    caps = _doctor_capabilities({
        "os": "Windows", "psutil_available": True, "psutil_accessible": True
    })
    assert caps["notes"]
    assert any("tasklist" in n or "ctypes" in n for n in caps["notes"])


# ---------------------------------------------------------------------------
# 10. JSON schema / output
# ---------------------------------------------------------------------------

def test_json_schema_complete(capsys):
    """JSON output must contain all required top-level data keys."""
    inspector = FakeInspectorWithConnections()
    args = _doctor_args(json=True)
    with patch("kport.cli_commands.docker_available", return_value=False):
        handle_doctor(args, inspector)
    out = capsys.readouterr().out
    env = json.loads(out)
    assert env["schema_version"] == 1
    assert env["command"] == "doctor"
    data = env["data"]
    required = ["platform", "capabilities", "listeners", "connection_summary",
                "processes", "docker", "findings"]
    for key in required:
        assert key in data


def test_json_connection_summary_keys(capsys):
    """connection_summary must contain all documented state keys."""
    inspector = FakeInspectorWithConnections(connections=[_conn("ESTABLISHED")])
    args = _doctor_args(json=True)
    with patch("kport.cli_commands.docker_available", return_value=False):
        handle_doctor(args, inspector)
    out = capsys.readouterr().out
    cs = json.loads(out)["data"]["connection_summary"]
    for key in ("total", "LISTEN", "ESTABLISHED", "TIME_WAIT", "CLOSE_WAIT", "other"):
        assert key in cs


def test_json_platform_keys(capsys):
    """platform dict must contain os and inspector_backend."""
    inspector = FakeInspectorWithConnections()
    args = _doctor_args(json=True)
    with patch("kport.cli_commands.docker_available", return_value=False):
        handle_doctor(args, inspector)
    out = capsys.readouterr().out
    platform = json.loads(out)["data"]["platform"]
    assert "os" in platform
    assert "inspector_backend" in platform


# ---------------------------------------------------------------------------
# 11. Graceful failure isolation — one failed subsystem must not crash doctor
# ---------------------------------------------------------------------------

def test_listener_failure_does_not_crash(capsys):
    """If list_listening() raises, doctor still produces a report."""
    inspector = FakeInspectorWithConnections()
    inspector.list_listening = lambda proto="tcp": (_ for _ in ()).throw(
        RuntimeError("Permission denied")
    )
    args = _doctor_args(json=True)
    with patch("kport.cli_commands.docker_available", return_value=False):
        rc = handle_doctor(args, inspector)
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    data = json.loads(out)["data"]
    # capability finding should contain the error
    cap_findings = [f for f in data["findings"] if f["category"] == "capability"]
    assert cap_findings


def test_connection_failure_does_not_crash(capsys):
    """If list_connections() raises, doctor still produces a report."""
    inspector = FakeInspectorWithConnections()
    inspector.list_connections = lambda: (_ for _ in ()).throw(
        RuntimeError("Access denied")
    )
    args = _doctor_args(json=True)
    with patch("kport.cli_commands.docker_available", return_value=False):
        rc = handle_doctor(args, inspector)
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    data = json.loads(out)["data"]
    cap_findings = [f for f in data["findings"] if f["category"] == "capability"]
    assert cap_findings


def test_project_failure_does_not_crash(capsys):
    """If resolve_project() raises, doctor still completes."""
    pi = ProcessInfo(pid=1234, name="python.exe", cwd="/some/path")
    inspector = FakeInspectorWithConnections(
        listening=[_binding(8000, pid=1234, laddr="127.0.0.1")],
        process_info={1234: pi},
    )
    args = _doctor_args(json=True)
    with patch("kport.cli_commands.docker_available", return_value=False), \
         patch("kport.cli_commands.detect_process_manager", return_value=None), \
         patch("kport.cli_commands.resolve_project", side_effect=OSError("disk error")):
        rc = handle_doctor(args, inspector)
    assert rc == EXIT_OK


def test_process_info_failure_does_not_crash(capsys):
    """If get_process_info() raises, doctor still completes and includes pid."""
    inspector = FakeInspectorWithConnections(
        listening=[_binding(9999, pid=9999, laddr="127.0.0.1")],
    )
    inspector.get_process_info = lambda pid: (_ for _ in ()).throw(
        PermissionError("access denied")
    )
    args = _doctor_args(json=True)
    with patch("kport.cli_commands.docker_available", return_value=False), \
         patch("kport.cli_commands.detect_process_manager", return_value=None), \
         patch("kport.cli_commands.resolve_project", return_value=None):
        rc = handle_doctor(args, inspector)
    assert rc == EXIT_OK
    # process entry must still exist with pid
    out = capsys.readouterr().out
    data = json.loads(out)["data"]
    assert any(p["pid"] == 9999 for p in data["processes"])
