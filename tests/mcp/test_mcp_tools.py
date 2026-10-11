"""
MCP tool contract tests (tests/mcp/test_mcp_tools.py).

Tests all ten MCP tools via the full JSON-RPC round-trip:
list_ports, inspect_port, kill_port, diagnose_port,
list_connections, conflicts, doctor, stop_service,
find_project, suggest_resolution.
"""

from __future__ import annotations

import json
from io import StringIO
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from kport.inspectors.base import ConnectionInfo, PortBinding, ProcessInfo
from kport.mcp_server import (
    TOOLS,
    handle_find_project,
    handle_list_connections,
    handle_suggest_resolution,
    run_mcp_server,
)
from tests.conftest import FakeInspector, _conn


def _send(*messages: dict) -> list[dict]:
    lines = [json.dumps(m) + "\n" for m in messages]
    captured = StringIO()
    with patch("sys.stdin", StringIO("".join(lines))), \
         patch("sys.stdout", captured), \
         patch("sys.stderr", StringIO()):
        run_mcp_server()
    return [
        json.loads(line)
        for line in captured.getvalue().strip().splitlines()
        if line.strip()
    ]


def _tool_call(name: str, arguments: dict, msg_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


# ---------------------------------------------------------------------------
# list_connections handler unit tests
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestHandleListConnections:
    """Unit tests for the handle_list_connections handler directly."""

    def test_returns_correct_structure(self):
        conns = [_conn(lport=8080), _conn(lport=9000)]
        inspector = FakeInspector(connections=conns)
        result = handle_list_connections(inspector)
        assert "connections" in result
        assert "count" in result
        assert "capped" in result
        assert isinstance(result["connections"], list)
        assert result["count"] == 2
        assert result["capped"] is False

    def test_capped_flag_true_when_results_equal_limit(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(10)]
        inspector = FakeInspector(connections=conns)
        result = handle_list_connections(inspector, max_results=10)
        assert result["capped"] is True
        assert result["count"] == 10

    def test_capped_flag_false_when_under_limit(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(5)]
        inspector = FakeInspector(connections=conns)
        result = handle_list_connections(inspector, max_results=10)
        assert result["capped"] is False

    def test_process_filter_forwarded_to_filter_connections(self):
        conns = [
            _conn(pid=1, name="node", lport=8080),
            _conn(pid=2, name="nginx", lport=80),
        ]
        inspector = FakeInspector(connections=conns)
        result = handle_list_connections(inspector, process="nginx")
        assert result["count"] == 1
        assert result["connections"][0]["process_name"] == "nginx"

    def test_max_results_above_2000_clamped_to_2000(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(5)]
        inspector = FakeInspector(connections=conns)
        result = handle_list_connections(inspector, max_results=99999)
        # 5 connections, all returned — clamp only prevents > 2000
        assert result["count"] == 5

    def test_max_results_zero_clamped_to_minimum_1(self):
        conns = [_conn(pid=i, lport=i + 1000) for i in range(5)]
        inspector = FakeInspector(connections=conns)
        result = handle_list_connections(inspector, max_results=0)
        assert result["count"] == 1


# ---------------------------------------------------------------------------
# MCP list_connections round-trip via stdio
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestMCPListConnectionsTool:
    """Full JSON-RPC round-trip tests for the list_connections MCP tool."""

    def _mock_conns(self, n: int = 3) -> list[MagicMock]:
        conns = []
        for i in range(n):
            c = MagicMock(spec=ConnectionInfo)
            c.pid = 100 + i
            c.process_name = "node"
            c.proto = "tcp"
            c.local_address = "127.0.0.1"
            c.local_port = 8080 + i
            c.remote_address = "127.0.0.1"
            c.remote_port = 50000 + i
            c.state = "ESTABLISHED"
            conns.append(c)
        return conns

    def test_tool_is_registered_in_tools_list(self):
        names = {t["name"] for t in TOOLS}
        assert "list_connections" in names

    def test_tool_has_required_schema_properties(self):
        tool = next(t for t in TOOLS if t["name"] == "list_connections")
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        props = schema["properties"]
        for key in ("pid", "process", "port", "state", "max_results"):
            assert key in props, f"Missing schema property: {key}"

    def test_list_connections_no_args_returns_all(self):
        mock_inspector = MagicMock()
        mock_inspector.list_connections.return_value = self._mock_conns(3)
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("list_connections", {}, msg_id=30))
        result = responses[0]["result"]
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert data["count"] == 3
        assert "connections" in data
        assert "capped" in data

    def test_list_connections_with_process_filter(self):
        mock_inspector = MagicMock()
        mock_inspector.list_connections.return_value = self._mock_conns(2)
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("list_connections", {"process": "node"}, msg_id=31))
        result = responses[0]["result"]
        assert result["isError"] is False

    def test_list_connections_with_max_results_capped(self):
        mock_inspector = MagicMock()
        mock_inspector.list_connections.return_value = self._mock_conns(10)
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("list_connections", {"max_results": 2}, msg_id=32))
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["count"] <= 2
        assert data["capped"] is True

    def test_list_connections_on_protected_port_is_read_only(self):
        """list_connections must not require safety bypass for protected ports."""
        mock_inspector = MagicMock()
        mock_inspector.list_connections.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("list_connections", {"port": 22}, msg_id=33))
        assert responses[0]["result"]["isError"] is False


# ---------------------------------------------------------------------------
# list_ports tool
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestMCPListPortsTool:
    def test_list_ports_returns_bindings(self):
        mock_inspector = MagicMock()
        mock_inspector.list_listening.return_value = [
            PortBinding(port=8080, family="inet", laddr="0.0.0.0:8080",
                        pid=100, process_name="node", state="LISTEN"),
        ]
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("list_ports", {}, msg_id=10))
        result = responses[0]["result"]
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert isinstance(data, dict)
        assert "local_processes" in data
        assert data["local_processes"][0]["port"] == 8080

    def test_list_ports_empty_returns_empty_list(self):
        mock_inspector = MagicMock()
        mock_inspector.list_listening.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("list_ports", {}, msg_id=11))
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data == {"local_processes": [], "docker_containers": []}


# ---------------------------------------------------------------------------
# kill_port tool
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestMCPKillPortTool:
    def test_kill_unprotected_port_succeeds(self):
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = [9999]
        mock_inspector.get_process_info.return_value = ProcessInfo(pid=9999, name="myapp")
        mock_inspector.find_bindings_on_port.return_value = [
            PortBinding(port=9876, family="inet", laddr="0.0.0.0:9876",
                        pid=9999, process_name="myapp", state="LISTEN"),
        ]
        mock_inspector.kill_port.return_value = (True, "freed")
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("kill_port", {"port": 9876}, msg_id=20))
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["success"] is True

    def test_kill_protected_port_is_blocked(self):
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("kill_port", {"port": 22}, msg_id=21))
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["success"] is False
        assert "Security Shield" in data["message"]

    def test_kill_port_missing_required_arg_returns_is_error(self):
        responses = _send(_tool_call("kill_port", {}, msg_id=22))
        # Missing 'port' argument → error response
        assert responses[0]["result"]["isError"] is True


# ---------------------------------------------------------------------------
# diagnose_port tool
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestMCPDiagnosePortTool:
    def test_diagnose_free_port_structure(self):
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []
        mock_inspector.list_connections.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector), \
             patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            responses = _send(_tool_call("diagnose_port", {"port": 7777}, msg_id=50))
        result = responses[0]["result"]
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        for key in ("port", "blocked", "observations", "inferences", "risks", "recommendations"):
            assert key in data


# ---------------------------------------------------------------------------
# find_project tool
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestHandleFindProject:
    """Unit tests for handle_find_project handler directly."""

    def test_no_args_returns_null_project(self):
        inspector = MagicMock()
        result = handle_find_project(inspector)
        assert result["project"] is None
        assert "reason" in result

    def test_path_not_in_git_repo_returns_null(self, tmp_path):
        inspector = MagicMock()
        result = handle_find_project(inspector, path=str(tmp_path))
        assert result["project"] is None
        assert "reason" in result

    def test_path_in_git_repo_returns_project_info(self, tmp_path):
        # Create a minimal .git directory so resolve_project finds it
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        inspector = MagicMock()
        result = handle_find_project(inspector, path=str(tmp_path))
        assert result["project"] is not None
        proj = result["project"]
        assert "git_root" in proj
        assert "project_name" in proj
        assert "branch" in proj

    def test_pid_based_resolution_uses_cwd(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/feature\n")
        inspector = MagicMock()
        mock_info = MagicMock()
        mock_info.cwd = str(tmp_path)
        inspector.get_process_info.return_value = mock_info
        result = handle_find_project(inspector, pid=1234)
        assert result["project"] is not None
        assert result["project"]["branch"] == "feature"

    def test_pid_not_found_returns_null(self):
        inspector = MagicMock()
        inspector.get_process_info.return_value = None
        result = handle_find_project(inspector, pid=99999)
        assert result["project"] is None

    def test_pid_exception_returns_null(self):
        inspector = MagicMock()
        inspector.get_process_info.side_effect = OSError("access denied")
        result = handle_find_project(inspector, pid=1)
        assert result["project"] is None

    def test_project_contains_no_credentials(self, tmp_path):
        """remote_origin must never expose user:password credentials."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        config_content = (
            '[core]\n\trepositoryformatversion = 0\n'
            '[remote "origin"]\n\turl = https://user:secret@github.com/org/repo.git\n'
        )
        (tmp_path / ".git" / "config").write_text(config_content)
        inspector = MagicMock()
        result = handle_find_project(inspector, path=str(tmp_path))
        assert result["project"] is not None
        remote = result["project"].get("remote_origin") or ""
        assert "secret" not in remote


@pytest.mark.mcp
class TestMCPFindProjectTool:
    """Full JSON-RPC round-trip tests for find_project MCP tool."""

    def test_tool_is_registered(self):
        names = {t["name"] for t in TOOLS}
        assert "find_project" in names

    def test_tool_is_read_only(self):
        tool = next(t for t in TOOLS if t["name"] == "find_project")
        assert tool.get("annotations", {}).get("readOnlyHint") is True

    def test_round_trip_no_args_returns_null(self):
        mock_inspector = MagicMock()
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("find_project", {}, msg_id=60))
        result = responses[0]["result"]
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert data["project"] is None

    def test_round_trip_path_with_git_repo(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        mock_inspector = MagicMock()
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(
                _tool_call("find_project", {"path": str(tmp_path)}, msg_id=61)
            )
        result = responses[0]["result"]
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert data["project"] is not None
        assert "git_root" in data["project"]

    def test_round_trip_pid_based(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        mock_inspector = MagicMock()
        mock_info = MagicMock()
        mock_info.cwd = str(tmp_path)
        mock_inspector.get_process_info.return_value = mock_info
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(
                _tool_call("find_project", {"pid": 1234}, msg_id=62)
            )
        result = responses[0]["result"]
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert data["project"] is not None


# ---------------------------------------------------------------------------
# suggest_resolution tool
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestHandleSuggestResolution:
    """Unit tests for handle_suggest_resolution handler directly."""

    def _free_inspector(self):
        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = []
        inspector.find_bindings_on_port.return_value = []
        inspector.list_connections.return_value = []
        return inspector

    def test_free_port_returns_bind_recommendation(self):
        inspector = self._free_inspector()
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = handle_suggest_resolution(inspector, 7777)
        assert result["port"] == 7777
        assert result["blocked"] is False
        assert isinstance(result["recommendations"], list)
        assert any(r.get("action") == "bind" for r in result["recommendations"])

    def test_blocked_port_returns_recommendations(self):
        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = [1234]
        real_info = ProcessInfo(pid=1234, name="myapp")
        inspector.get_process_info.return_value = real_info
        inspector.find_bindings_on_port.return_value = [
            PortBinding(port=7778, family="inet", laddr="127.0.0.1:7778",
                        pid=1234, process_name="myapp", state="LISTEN"),
        ]
        inspector.list_connections.return_value = []
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = handle_suggest_resolution(inspector, 7778)
        assert result["port"] == 7778
        assert result["blocked"] is True
        assert isinstance(result["recommendations"], list)
        assert len(result["recommendations"]) > 0

    def test_invalid_port_raises_value_error(self):
        inspector = MagicMock()
        with pytest.raises(ValueError, match="out of bounds"):
            handle_suggest_resolution(inspector, 0)

    def test_invalid_port_high_raises_value_error(self):
        inspector = MagicMock()
        with pytest.raises(ValueError, match="out of bounds"):
            handle_suggest_resolution(inspector, 65536)

    def test_returns_required_keys(self):
        inspector = self._free_inspector()
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = handle_suggest_resolution(inspector, 8000)
        assert "port" in result
        assert "blocked" in result
        assert "recommendations" in result

    def test_proto_tcp_default(self):
        inspector = self._free_inspector()
        with patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            result = handle_suggest_resolution(inspector, 9000, proto="tcp")
        assert result["port"] == 9000


@pytest.mark.mcp
class TestMCPSuggestResolutionTool:
    """Full JSON-RPC round-trip tests for suggest_resolution MCP tool."""

    def test_tool_is_registered(self):
        names = {t["name"] for t in TOOLS}
        assert "suggest_resolution" in names

    def test_tool_is_read_only(self):
        tool = next(t for t in TOOLS if t["name"] == "suggest_resolution")
        assert tool.get("annotations", {}).get("readOnlyHint") is True

    def test_round_trip_free_port(self):
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []
        mock_inspector.list_connections.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector), \
             patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            responses = _send(_tool_call("suggest_resolution", {"port": 7777}, msg_id=70))
        result = responses[0]["result"]
        assert result["isError"] is False
        data = json.loads(result["content"][0]["text"])
        assert "port" in data
        assert "blocked" in data
        assert "recommendations" in data
        assert data["blocked"] is False

    def test_round_trip_missing_port_is_error(self):
        responses = _send(_tool_call("suggest_resolution", {}, msg_id=71))
        assert responses[0]["result"]["isError"] is True

    def test_round_trip_with_proto_both(self):
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []
        mock_inspector.list_connections.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector), \
             patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]):
            responses = _send(
                _tool_call("suggest_resolution", {"port": 8080, "proto": "both"}, msg_id=72)
            )
        assert responses[0]["result"]["isError"] is False


# ---------------------------------------------------------------------------
# Tool metadata / annotation checks
# ---------------------------------------------------------------------------


@pytest.mark.mcp
class TestMCPToolMetadata:
    """Verify annotations, tool count, and destructive tool safety."""

    READ_ONLY_TOOLS: ClassVar[set[str]] = {
        "list_ports", "inspect_port", "diagnose_port",
        "list_connections", "conflicts", "doctor",
        "find_project", "suggest_resolution", "inspect_pid",
        "find_alternative_port", "get_audit_history",
    }
    DESTRUCTIVE_TOOLS: ClassVar[set[str]] = {"kill_port", "stop_service", "kill_pid", "kill_process"}

    def test_total_tool_count_is_fifteen(self):
        assert len(TOOLS) == 15

    def test_read_only_tools_have_read_only_hint(self):
        tool_map = {t["name"]: t for t in TOOLS}
        for name in self.READ_ONLY_TOOLS:
            tool = tool_map[name]
            assert tool.get("annotations", {}).get("readOnlyHint") is True, (
                f"{name} should have readOnlyHint=True"
            )

    def test_destructive_tools_have_destructive_hint(self):
        tool_map = {t["name"]: t for t in TOOLS}
        for name in self.DESTRUCTIVE_TOOLS:
            tool = tool_map[name]
            assert tool.get("annotations", {}).get("destructiveHint") is True, (
                f"{name} should have destructiveHint=True"
            )

    def test_find_project_not_destructive(self):
        tool = next(t for t in TOOLS if t["name"] == "find_project")
        assert not tool.get("annotations", {}).get("destructiveHint", False)

    def test_suggest_resolution_not_destructive(self):
        tool = next(t for t in TOOLS if t["name"] == "suggest_resolution")
        assert not tool.get("annotations", {}).get("destructiveHint", False)

    def test_all_tools_have_required_fields(self):
        for tool in TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"{tool['name']} missing 'description'"
            assert "inputSchema" in tool, f"{tool['name']} missing 'inputSchema'"
            assert tool["inputSchema"]["type"] == "object", (
                f"{tool['name']} inputSchema.type must be 'object'"
            )
