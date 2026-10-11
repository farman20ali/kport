"""
MCP server protocol tests (tests/mcp/test_mcp_server.py).

Tests the JSON-RPC transport layer: initialize, tools/list,
isError consistency, malformed requests, and unknown tool handling.
"""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from kport.mcp_server import TOOLS, run_mcp_server


def _send(*messages: dict) -> list[dict]:
    """Drive the MCP server with a sequence of JSON-RPC messages."""
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


@pytest.mark.mcp
class TestMCPProtocol:
    """JSON-RPC initialize / tools/list / framing tests."""

    def test_initialize_response_schema(self):
        responses = _send(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "test"},
                "capabilities": {},
            }},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        init = responses[0]
        assert init["jsonrpc"] == "2.0"
        assert init["id"] == 1
        assert "protocolVersion" in init["result"]
        assert "capabilities" in init["result"]

    def test_initialize_protocol_version_is_current(self):
        """Server must advertise the current MCP protocol version (2026-07-28)."""
        responses = _send(
            {"jsonrpc": "2.0", "id": 10, "method": "initialize", "params": {
                "protocolVersion": "2026-07-28",
                "clientInfo": {"name": "test"},
                "capabilities": {},
            }},
        )
        assert responses[0]["result"]["protocolVersion"] == "2026-07-28"

    def test_tools_list_returns_all_thirteen_tools(self):
        responses = _send(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        result = responses[0]["result"]
        names = {t["name"] for t in result["tools"]}
        expected = {
            "list_ports", "inspect_port", "kill_port",
            "diagnose_port", "list_connections", "conflicts", "doctor",
            "stop_service", "find_project", "suggest_resolution",
            "inspect_pid", "kill_pid", "kill_process",
            "find_alternative_port", "get_audit_history",
        }
        assert expected == names

    def test_tools_have_required_fields(self):
        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert tool["inputSchema"]["type"] == "object"

    def test_unknown_tool_returns_is_error_true(self):
        responses = _send(
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
             "params": {"name": "nonexistent_tool", "arguments": {}}},
        )
        assert responses[0]["result"]["isError"] is True

    def test_malformed_jsonrpc_ignored_gracefully(self):
        # Push one broken line then a valid call — server must not crash.
        lines = "NOT JSON\n" + json.dumps({
            "jsonrpc": "2.0", "id": 99, "method": "tools/list", "params": {}
        }) + "\n"
        captured = StringIO()
        with patch("sys.stdin", StringIO(lines)), \
             patch("sys.stdout", captured), \
             patch("sys.stderr", StringIO()):
            run_mcp_server()
        valid_responses = [
            json.loads(line)
            for line in captured.getvalue().strip().splitlines()
            if line.strip()
        ]
        assert any("result" in r for r in valid_responses)

    def test_stdout_contains_only_json_lines(self):
        """No debug noise must contaminate the MCP stdout stream."""
        mock_inspector = MagicMock()
        mock_inspector.list_connections.return_value = []
        responses = _send(
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "list_connections", "arguments": {}}},
        )
        for r in responses:
            # Every line must be parseable JSON with a jsonrpc key
            assert "jsonrpc" in r


@pytest.mark.mcp
class TestMCPIsErrorConsistency:
    """Verify isError semantics for all tool responses."""

    def test_list_connections_is_error_false_on_success(self):
        mock_inspector = MagicMock()
        mock_inspector.list_connections.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(
                {"jsonrpc": "2.0", "id": 40, "method": "tools/call",
                 "params": {"name": "list_connections", "arguments": {}}},
            )
        assert responses[0]["result"]["isError"] is False

    def test_conflicts_is_error_false_on_success(self):
        mock_inspector = MagicMock()
        mock_inspector.list_listening.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector), \
             patch("kport.mcp_server.list_docker_mappings", return_value=[]):
            responses = _send(
                {"jsonrpc": "2.0", "id": 41, "method": "tools/call",
                 "params": {"name": "conflicts", "arguments": {}}},
            )
        assert responses[0]["result"]["isError"] is False

    def test_doctor_is_error_false_on_success(self):
        mock_inspector = MagicMock()
        mock_inspector.list_listening.return_value = []
        mock_inspector.list_connections.return_value = []
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector), \
             patch("kport.diagnostics.docker_available", return_value=False):
            responses = _send(
                {"jsonrpc": "2.0", "id": 42, "method": "tools/call",
                 "params": {"name": "doctor", "arguments": {}}},
            )
        assert responses[0]["result"]["isError"] is False

    def test_handler_exception_produces_is_error_true(self):
        mock_inspector = MagicMock()
        mock_inspector.list_listening.side_effect = RuntimeError("boom")
        with patch("kport.mcp_server.get_inspector", return_value=mock_inspector):
            responses = _send(
                {"jsonrpc": "2.0", "id": 43, "method": "tools/call",
                 "params": {"name": "list_ports", "arguments": {}}},
            )
        assert responses[0]["result"]["isError"] is True
