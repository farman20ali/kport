"""
CLI command tests (tests/cli/test_cli_commands.py).

Tests handle_product_command() for: list, inspect, kill, explain,
docker, kill-process, conflicts, and related options (--proto, --kill-tree,
--wait-for-exit, --profile).  Also tests ProcessInfo name enrichment and
the safety config additivity contract.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from kport.cli import EXIT_OK, EXIT_PERMISSION, EXIT_PORT_FREE, handle_product_command
from kport.inspectors.base import ProcessInfo
from tests.conftest import FakeInspector, _args, _binding

# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_list_command_json_output_contains_local_and_docker(capsys):
    inspector = FakeInspector(listening=[_binding(8080)])
    args = _args(command="list", json=True)
    with patch("kport.cli_commands.list_docker_mappings", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema_version"] == 1
    assert envelope["command"] == "list"
    assert "local" in envelope["data"]
    assert "docker" in envelope["data"]


@pytest.mark.cli
def test_list_command_text_mode_succeeds(capsys):
    inspector = FakeInspector(listening=[])
    args = _args(command="list", json=False)
    with patch("kport.cli_commands.list_docker_mappings", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == EXIT_OK


@pytest.mark.cli
def test_list_command_proto_udp_forwarded_to_inspector(capsys):
    received_proto = []

    class ProtoCapture(FakeInspector):
        def list_listening(self, proto: str = "tcp"):
            received_proto.append(proto)
            return [_binding(5353, proto="udp")] if proto in ("udp", "both") else []

    args = _args(command="list", json=True, proto="udp")
    with patch("kport.cli_commands.list_docker_mappings", return_value=[]):
        handle_product_command(args, ProtoCapture())
    assert "udp" in received_proto


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_inspect_free_port_returns_free_type(capsys):
    inspector = FakeInspector()
    args = _args(command="inspect", port=19999, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["type"] == "free"
    assert rc == EXIT_PORT_FREE


@pytest.mark.cli
def test_inspect_local_process_returns_local_type(capsys):
    pi = ProcessInfo(pid=1234, name="node")
    inspector = FakeInspector(
        pids_on_port={8080: [1234]},
        process_info={1234: pi},
    )
    args = _args(command="inspect", port=8080, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["type"] == "local"
    assert rc == EXIT_OK


@pytest.mark.cli
def test_inspect_proto_both_forwarded_to_inspector(capsys):
    received = []

    class BothInspector(FakeInspector):
        def find_bindings_on_port(self, port, proto="tcp"):
            received.append(proto)
            return [_binding(port, pid=None, name=None, proto="tcp")]

        def find_pids_on_port(self, port, proto="tcp"):
            return []

    args = _args(command="inspect", port=8080, json=True, proto="both")
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        handle_product_command(args, BothInspector())
    assert "both" in received


# ---------------------------------------------------------------------------
# kill
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_kill_free_port_returns_port_free(capsys):
    inspector = FakeInspector()
    args = _args(command="kill", port=19997, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == EXIT_PORT_FREE


@pytest.mark.cli
def test_kill_protected_port_blocked_with_json_response(capsys):
    inspector = FakeInspector(
        pids_on_port={22: [111]},
        process_info={111: ProcessInfo(pid=111, name="sshd")},
    )
    args = _args(command="kill", port=22, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == EXIT_PERMISSION
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["success"] is False


@pytest.mark.cli
def test_kill_proto_udp_forwarded_to_inspector(capsys):
    killed = []

    class UDPKillInspector(FakeInspector):
        def find_pids_on_port(self, port, proto="tcp"):
            return [777] if proto == "udp" else []

        def find_bindings_on_port(self, port, proto="tcp"):
            if proto == "udp":
                return [_binding(5353, pid=777, name="dnsmasq", proto="udp")]
            return []

        def kill_port(self, port, **kwargs):
            killed.append(port)
            return True, "killed"

    args = _args(command="kill", port=5353, json=True, proto="udp")
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, UDPKillInspector())
    assert rc == EXIT_OK
    assert 5353 in killed


@pytest.mark.cli
def test_kill_tree_option_forwarded_to_inspector(capsys):
    called_with_kill_tree = []

    class TreeInspector(FakeInspector):
        def find_pids_on_port(self, port, proto="tcp"):
            return [500]

        def kill_port(self, port, **kwargs):
            if kwargs.get("kill_tree"):
                called_with_kill_tree.append(port)
            return True, "freed"

    args = _args(command="kill", port=8080, json=True, kill_tree=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, TreeInspector())
    assert rc == EXIT_OK
    assert 8080 in called_with_kill_tree


@pytest.mark.cli
def test_wait_for_exit_succeeds_when_port_clears(capsys):
    polled = [0]

    class ClearingInspector(FakeInspector):
        def find_pids_on_port(self, port, proto="tcp"):
            return [1234]

        def find_bindings_on_port(self, port, proto="tcp"):
            polled[0] += 1
            if polled[0] == 1:
                return [_binding(port, pid=1234)]
            return []

        def kill_port(self, port, **kwargs):
            return True, "freed"

    args = _args(command="kill", port=8080, json=True, wait_for_exit=1.0)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, ClearingInspector())
    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["wait_for_exit_ok"] is True


@pytest.mark.cli
def test_wait_for_exit_returns_error_on_timeout(capsys):
    class StuckInspector(FakeInspector):
        def find_pids_on_port(self, port, proto="tcp"):
            return [1234]

        def find_bindings_on_port(self, port, proto="tcp"):
            return [_binding(port, pid=1234)]

        def kill_port(self, port, **kwargs):
            return True, "freed"

    args = _args(command="kill", port=8080, json=True, wait_for_exit=0.1)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, StuckInspector())
    assert rc == 1  # EXIT_GENERAL_ERROR on timeout
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["wait_for_exit_ok"] is False


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_explain_free_port(capsys):
    inspector = FakeInspector()
    args = _args(command="explain", port=19999, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "explain"
    assert envelope["data"]["blocked"] is False
    assert rc == EXIT_PORT_FREE


@pytest.mark.cli
def test_explain_occupied_port(capsys):
    pi = ProcessInfo(pid=9999, name="myapp")
    inspector = FakeInspector(
        pids_on_port={7777: [9999]},
        process_info={9999: pi},
    )
    args = _args(command="explain", port=7777, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["blocked"] is True
    assert rc == EXIT_OK


@pytest.mark.cli
def test_explain_with_managed_by_in_json(capsys):
    class ManagedInspector(FakeInspector):
        def find_pids_on_port(self, port, proto="tcp"):
            return [999]

        def get_process_info(self, pid):
            return ProcessInfo(pid=999, name="nginx", exe="/usr/sbin/nginx")

    pm_res = {
        "manager": "systemd",
        "name": "nginx.service",
        "managed_by": "systemd:nginx.service",
        "warning": "Managed by systemd",
    }
    args = _args(command="explain", port=80, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]), \
         patch("kport.cli_commands.detect_process_manager", return_value=pm_res):
        _ = handle_product_command(args, ManagedInspector())
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["data"]["managed_by"] == "systemd:nginx.service"


# ---------------------------------------------------------------------------
# docker command
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_docker_command_json_output(capsys):
    args = _args(command="docker", json=True, extra=[])
    with patch("kport.cli_commands.list_docker_mappings", return_value=[]):
        rc = handle_product_command(args, FakeInspector())
    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "docker"
    assert isinstance(envelope["data"], list)


# ---------------------------------------------------------------------------
# kill-process
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_kill_process_not_found(capsys):
    inspector = FakeInspector()
    args = _args(command="kill-process", name="ghost", json=True, exact=False)
    rc = handle_product_command(args, inspector)
    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "kill-process"
    assert envelope["data"]["pids"] == []


# ---------------------------------------------------------------------------
# conflicts
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_conflicts_no_docker_returns_empty(capsys):
    inspector = FakeInspector()
    args = _args(command="conflicts", json=True)
    with patch("kport.cli_commands.list_docker_mappings", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == EXIT_OK
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["command"] == "conflicts"
    assert envelope["data"] == []


# ---------------------------------------------------------------------------
# Profile support
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_inspect_with_profile_returns_correct_schema(capsys):
    config = {"profiles": {"web": [8080]}}
    inspector = FakeInspector()
    args = _args(command="inspect", profile="web", json=True)
    with patch("kport.cli_utils.load_config", return_value=config), \
         patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
    assert rc == EXIT_PORT_FREE
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema_version"] == 1
    assert envelope["command"] == "inspect"


# ---------------------------------------------------------------------------
# ProcessInfo name enrichment (unit-level, via CLI dataclass)
# ---------------------------------------------------------------------------


@pytest.mark.cli
def test_process_info_enrichment_node_script():
    pi = ProcessInfo(pid=1, name="node", cmdline=["/usr/bin/node", "server.js"])
    assert pi.name == "node (server.js)"


@pytest.mark.cli
def test_process_info_enrichment_python_module():
    pi = ProcessInfo(pid=2, name="python3", cmdline=["python3", "-m", "http.server"])
    assert pi.name == "python3 (http.server)"


@pytest.mark.cli
def test_process_info_enrichment_java_jar():
    pi = ProcessInfo(pid=3, name="java", cmdline=["java", "-jar", "/opt/app.jar"])
    assert pi.name == "java (app.jar)"


@pytest.mark.cli
def test_process_info_no_enrichment_for_unknown_runtime():
    pi = ProcessInfo(pid=4, name="nginx", cmdline=["nginx", "-g", "daemon off;"])
    assert pi.name == "nginx"


@pytest.mark.cli
def test_process_info_enrichment_skipped_for_short_cmdline():
    pi = ProcessInfo(pid=5, name="node", cmdline=["node"])
    assert pi.name == "node"


@pytest.mark.cli
def test_setup_sudo_command_routing(tmp_path):
    target = str(tmp_path / "kport")
    args = _args(command="setup-sudo", target=target)
    inspector = FakeInspector()
    with patch("sys.platform", "linux"):
        rc = handle_product_command(args, inspector)
        assert rc == EXIT_OK
        assert (tmp_path / "kport").exists()


@pytest.mark.cli
def test_setup_sudo_windows_returns_error(capsys):
    args = _args(command="setup-sudo", target="/usr/local/bin/kport")
    inspector = FakeInspector()
    with patch("sys.platform", "win32"):
        rc = handle_product_command(args, inspector)
        assert rc != EXIT_OK
        err = capsys.readouterr().err
        assert "not supported on Windows" in err


@pytest.mark.cli
def test_audit_command_json_output(capsys):
    args = _args(command="audit", json=True, limit=5)
    with patch("kport.audit.read_recent_audit_events", return_value=[{"action": "kill_port"}]):
        rc = handle_product_command(args, FakeInspector())
        assert rc == EXIT_OK
        out = json.loads(capsys.readouterr().out)
        assert out["schema_version"] == 1
        assert out["command"] == "audit"
        assert len(out["data"]["entries"]) == 1


@pytest.mark.cli
def test_hold_command_occupied_returns_error(capsys):
    inspector = FakeInspector(listening=[_binding(9999)])
    args = _args(command="hold", port=9999, json=True)
    with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
        rc = handle_product_command(args, inspector)
        assert rc != EXIT_OK

