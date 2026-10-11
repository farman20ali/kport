"""
MCP safety shield tests (tests/mcp/test_mcp_safety.py).

Verifies that the MCP server's safety enforcement cannot be bypassed
and that additive configuration is correctly applied.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from kport.mcp_server import handle_kill_port, run_mcp_server


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


@pytest.mark.mcp
class TestMCPSafetyShield:
    """MCP always evaluates centralized safety policy and cannot be bypassed."""

    def test_kill_default_protected_port_22_is_blocked(self):
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(_tool_call("kill_port", {"port": 22}, msg_id=1))
        data = json.loads(responses[0]["result"]["content"][0]["text"])
        assert data["success"] is False
        assert "Security Shield" in data["message"]

    def test_kill_port_schema_has_no_bypass_safety_field(self):
        """MCP kill_port must not expose a bypass_safety argument."""
        from kport.mcp_server import TOOLS
        kill_tool = next(t for t in TOOLS if t["name"] == "kill_port")
        props = kill_tool["inputSchema"].get("properties", {})
        assert "bypass_safety" not in props, (
            "MCP must not expose bypass_safety — safety enforcement is always on"
        )

    def test_additive_config_does_not_remove_default_protected_port(self):
        """Custom config adds ports but must not remove port 22."""
        config = {"protected_ports": [9999]}
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []
        with patch("kport.mcp_server.load_kport_config", return_value=config), \
             patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            result_22 = handle_kill_port(mock_inspector, 22)
            result_9999 = handle_kill_port(mock_inspector, 9999)
            result_8080 = handle_kill_port(mock_inspector, 8080)

        assert result_22["success"] is False, "Port 22 must still be blocked by default config"
        assert result_9999["success"] is False, "Custom port 9999 must be blocked"
        assert result_8080["success"] is True, "Unprotected port 8080 must be allowed"

    def test_custom_protected_process_is_blocked_alongside_defaults(self):
        """Custom process protection adds to defaults."""
        from kport.inspectors.base import PortBinding, ProcessInfo
        config = {"protected_processes": ["custom_daemon"]}
        mock_inspector = MagicMock()
        mock_inspector.find_pids_on_port.return_value = [555]
        mock_inspector.get_process_info.return_value = ProcessInfo(pid=555, name="custom_daemon")
        mock_inspector.find_bindings_on_port.return_value = [
            PortBinding(port=8080, family="inet", laddr="0.0.0.0:8080",
                        pid=555, process_name="custom_daemon", state="LISTEN"),
        ]
        with patch("kport.mcp_server.load_kport_config", return_value=config), \
             patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            result = handle_kill_port(mock_inspector, 8080)
        assert result["success"] is False
        assert "Security Shield" in result["message"]

    def test_read_only_tools_bypass_safety_without_errors(self):
        """list_ports, diagnose_port, list_connections, conflicts, doctor must work on any port."""
        mock_inspector = MagicMock()
        mock_inspector.list_listening.return_value = []
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []
        mock_inspector.list_connections.return_value = []

        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector), \
             patch("kport.diagnostics.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.diagnostics.docker_available", return_value=False):
            for tool, args, msg_id in [
                ("list_ports", {}, 60),
                ("diagnose_port", {"port": 22}, 61),
                ("list_connections", {"port": 22}, 62),
            ]:
                responses = _send(_tool_call(tool, args, msg_id=msg_id))
                assert responses[0]["result"]["isError"] is False, (
                    f"Read-only tool '{tool}' must not fail on protected port"
                )
