"""
Unit tests for diagnostics engine (tests/unit/test_diagnostics.py).

Tests diagnose_port(), filter_connections(), detect_conflicts(),
and run_doctor() subsystem behaviours.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kport.diagnostics import detect_conflicts, diagnose_port, filter_connections
from kport.docker_engine import DockerPortMapping
from kport.inspectors.base import PortBinding, ProcessInfo
from tests.conftest import FakeInspector, _binding, _conn

# ---------------------------------------------------------------------------
# filter_connections
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConnectionFiltering:
    """Tests for diagnostics.filter_connections() behaviour."""

    def test_returns_all_connections_by_default(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(5)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector)
        assert len(result) == 5

    def test_filter_by_pid(self):
        conns = [_conn(pid=1, lport=1001), _conn(pid=2, lport=1002)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, pid=1)
        assert len(result) == 1
        assert result[0]["pid"] == 1

    def test_filter_by_process_name_substring_case_insensitive(self):
        conns = [_conn(name="NODE", lport=1001), _conn(name="nginx", lport=1002)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, process="node")
        assert len(result) == 1
        assert result[0]["process_name"] == "NODE"

    def test_filter_by_local_port(self):
        conns = [_conn(lport=8080, rport=50000), _conn(lport=9090, rport=51000)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, port=8080)
        assert len(result) == 1
        assert result[0]["local_port"] == 8080

    def test_filter_by_remote_port(self):
        conns = [_conn(lport=8080, rport=9090), _conn(lport=8888, rport=7070)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, port=9090)
        assert len(result) == 1

    def test_filter_by_state_case_insensitive(self):
        conns = [
            _conn(state="ESTABLISHED", lport=1001),
            _conn(state="TIME_WAIT", lport=1002),
        ]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, state="established")
        assert len(result) == 1
        assert result[0]["state"] == "ESTABLISHED"

    def test_max_results_truncates(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(100)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, max_results=10)
        assert len(result) == 10

    def test_max_results_no_truncation_when_under_cap(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(5)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, max_results=50)
        assert len(result) == 5

    def test_empty_connections_returns_empty_list(self):
        inspector = FakeInspector(connections=[])
        result = filter_connections(inspector)
        assert result == []

    def test_result_schema_contains_required_keys(self):
        conns = [_conn(pid=42, name="python", lport=5000, rport=60000)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector)
        row = result[0]
        assert row["pid"] == 42
        assert row["process_name"] == "python"
        assert row["local_port"] == 5000
        assert row["remote_port"] == 60000
        assert "protocol" in row
        assert "state" in row

    def test_no_sentinel_metadata_in_result_when_capped(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(20)]
        inspector = FakeInspector(connections=conns)
        result = filter_connections(inspector, max_results=5)
        for row in result:
            assert "_truncated" not in row
            assert "pid" in row


# ---------------------------------------------------------------------------
# diagnose_port
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDiagnosePort:
    """Tests for diagnostics.diagnose_port() structure, enrichment, and safety."""

    def test_free_port_returns_blocked_false(self):
        inspector = FakeInspector()
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = diagnose_port(8080, inspector)
        assert result["blocked"] is False
        assert result["observations"]["type"] == "free"

    def test_occupied_port_returns_blocked_true(self):
        inspector = FakeInspector(
            pids_on_port={8080: [1234]},
            process_info={1234: ProcessInfo(pid=1234, name="node")},
            bindings_on_port={8080: [_binding(8080, pid=1234, name="node")]},
        )
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.diagnostics.detect_process_manager", return_value=None):
            result = diagnose_port(8080, inspector)
        assert result["blocked"] is True

    def test_observations_contains_connections_key(self):
        inspector = FakeInspector()
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = diagnose_port(8080, inspector)
        assert "connections" in result["observations"]
        assert isinstance(result["observations"]["connections"], list)

    def test_connections_in_observations_bounded_to_50(self):
        many_conns = [_conn(pid=i, lport=8080, rport=50000 + i) for i in range(80)]
        inspector = FakeInspector(connections=many_conns)
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = diagnose_port(8080, inspector)
        assert len(result["observations"]["connections"]) <= 50

    def test_process_enriched_with_parent_name_when_ppid_known(self):
        parent_pi = ProcessInfo(pid=1, name="systemd")
        child_pi = ProcessInfo(pid=1234, name="node", ppid=1)
        inspector = FakeInspector(
            pids_on_port={8080: [1234]},
            process_info={1: parent_pi, 1234: child_pi},
            bindings_on_port={
                8080: [PortBinding(
                    port=8080, family="inet", laddr="127.0.0.1:8080",
                    pid=1234, process_name="node", state="LISTEN",
                )]
            },
        )
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.diagnostics.detect_process_manager", return_value=None):
            result = diagnose_port(8080, inspector)
        proc = result["observations"]["processes"][0]
        assert proc["ppid"] == 1
        assert proc["parent_name"] == "systemd"

    def test_process_without_ppid_has_parent_name_none(self):
        pi = ProcessInfo(pid=500, name="nginx")  # ppid defaults to None
        inspector = FakeInspector(
            pids_on_port={8081: [500]},
            process_info={500: pi},
            bindings_on_port={
                8081: [PortBinding(
                    port=8081, family="inet", laddr="0.0.0.0:8081",
                    pid=500, process_name="nginx", state="LISTEN",
                )]
            },
        )
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.diagnostics.detect_process_manager", return_value=None):
            result = diagnose_port(8081, inspector)
        proc = result["observations"]["processes"][0]
        assert proc["parent_name"] is None

    def test_unknown_pid_dict_contains_parent_name_key(self):
        """When PID info is unavailable, the schema must still include parent_name."""
        inspector = FakeInspector(
            pids_on_port={9000: [9999]},
            process_info={},
            bindings_on_port={
                9000: [PortBinding(
                    port=9000, family="inet", laddr="0.0.0.0:9000",
                    pid=9999, process_name=None, state="LISTEN",
                )]
            },
        )
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = diagnose_port(9000, inspector)
        proc = result["observations"]["processes"][0]
        assert "parent_name" in proc
        assert proc["pid"] == 9999

    def test_result_contains_required_top_level_keys(self):
        inspector = FakeInspector()
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = diagnose_port(8080, inspector)
        for key in ("port", "blocked", "observations", "inferences", "risks", "recommendations"):
            assert key in result, f"Missing key: {key}"

    def test_protected_port_generates_abort_recommendation(self):
        pi = ProcessInfo(pid=1234, name="sshd")
        inspector = FakeInspector(
            pids_on_port={22: [1234]},
            process_info={1234: pi},
            bindings_on_port={22: [_binding(22, pid=1234, name="sshd")]},
        )
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = diagnose_port(22, inspector)
        recs = result["recommendations"]
        assert any(r["action"] == "abort" for r in recs)

    def test_public_wildcard_binding_generates_exposure_risk(self):
        pi = ProcessInfo(pid=1234, name="node")
        inspector = FakeInspector(
            pids_on_port={8080: [1234]},
            process_info={1234: pi},
            bindings_on_port={8080: [_binding(8080, pid=1234, laddr="0.0.0.0")]},
        )
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.diagnostics.detect_process_manager", return_value=None):
            result = diagnose_port(8080, inspector)
        assert any(r["type"] == "public_exposure" for r in result["risks"])

    def test_systemd_managed_generates_stop_service_recommendation(self):
        pi = ProcessInfo(pid=1234, name="nginx")
        inspector = FakeInspector(
            pids_on_port={8082: [1234]},
            process_info={1234: pi},
            bindings_on_port={8082: [_binding(8082, pid=1234, name="nginx")]},
        )
        pm_info = {
            "manager": "systemd",
            "name": "nginx.service",
            "managed_by": "systemd:nginx.service",
            "warning": "managed",
        }
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.diagnostics.detect_process_manager", return_value=pm_info):
            result = diagnose_port(8082, inspector)
        assert any(r["action"] == "stop_service" for r in result["recommendations"])


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetectConflicts:
    """Tests for diagnostics.detect_conflicts()."""

    def test_no_docker_no_local_returns_empty(self):
        inspector = FakeInspector()
        with patch("kport.diagnostics.list_docker_mappings", return_value=[]):
            result = detect_conflicts(inspector)
        assert result == []

    def test_no_docker_returns_empty_even_with_local_bindings(self):
        inspector = FakeInspector(
            listening=[_binding(8080)],
            pids_on_port={8080: [1234]},
        )
        with patch("kport.diagnostics.list_docker_mappings", return_value=[]):
            result = detect_conflicts(inspector)
        assert result == []

    def test_docker_port_conflict_detected(self):
        inspector = FakeInspector(
            pids_on_port={8080: [1234]},
            process_info={1234: ProcessInfo(pid=1234, name="node")},
            listening=[_binding(8080)],
        )
        mock_mapping = DockerPortMapping(
            container_id="abc123",
            container_name="web",
            image="nginx:latest",
            status="running",
            host_ip="0.0.0.0",
            host_port=8080,
            container_port=80,
            proto="tcp",
        )
        with patch("kport.diagnostics.list_docker_mappings", return_value=[mock_mapping]):
            result = detect_conflicts(inspector)
        assert len(result) >= 1
        conflict = result[0]
        assert "port" in conflict
        assert "docker" in conflict
        assert conflict["docker"]["container_name"] == "web"


@pytest.mark.unit
class TestDiagnosticsArchitecture:
    """Ensure diagnostics module maintains correct architectural dependency direction."""

    def test_diagnostics_imports_boundary_regression(self):
        """Regression test ensuring diagnostics.py does not import presentation/TUI/CLI/MCP layers."""
        import ast

        import kport.diagnostics

        diag_path = kport.diagnostics.__file__

        assert diag_path is not None

        with open(diag_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=diag_path)

        forbidden = {"interactive", "formatter", "cli", "cli_commands", "cli_utils", "mcp_server"}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name_parts = alias.name.split(".")
                    for part in name_parts:
                        assert part not in forbidden, f"Forbidden import found in diagnostics.py: import {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module_parts = node.module.split(".")
                    for part in module_parts:
                        assert part not in forbidden, f"Forbidden import found in diagnostics.py: from {node.module} import ..."
                if node.level > 0:
                    for alias in node.names:
                        assert alias.name not in forbidden, f"Forbidden relative import found in diagnostics.py: from . import {alias.name}"


@pytest.mark.unit
def test_suggest_next_free_port():
    from kport.diagnostics import suggest_next_free_port

    inspector = FakeInspector(listening=[_binding(3000)])
    with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
        res = suggest_next_free_port(start_port=3000, count=2, inspector=inspector)
        assert res == [3001, 3002]

