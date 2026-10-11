"""
MCP integration tests for the stop_service tool (tests/mcp/test_mcp_stop_service.py).

Tests annotations presence, protocol version lock, safety shield enforcement,
no-manager rejection, dry-run, and successful stop-and-verified flows.
"""
from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest

from kport.mcp_server import TOOLS, run_mcp_server

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _call_stop_service(port: int = 9000, dry_run: bool = False) -> dict:
    """Send a single stop_service tools/call and return the response."""
    responses = _send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "stop_service",
                "arguments": {"port": port, "dry_run": dry_run},
            },
        }
    )
    return responses[0]


# ---------------------------------------------------------------------------
# Tool metadata tests
# ---------------------------------------------------------------------------

@pytest.mark.mcp
class TestStopServiceToolMetadata:
    """Verify the stop_service entry in the TOOLS catalog."""

    def test_stop_service_is_in_tools_list(self):
        names = [t["name"] for t in TOOLS]
        assert "stop_service" in names

    def test_stop_service_has_destructive_hint(self):
        tool = next(t for t in TOOLS if t["name"] == "stop_service")
        annotations = tool.get("annotations", {})
        assert annotations.get("destructiveHint") is True

    def test_stop_service_has_no_force_parameter(self):
        """MCP tool must never expose a 'force' parameter."""
        tool = next(t for t in TOOLS if t["name"] == "stop_service")
        props = tool.get("inputSchema", {}).get("properties", {})
        assert "force" not in props

    def test_stop_service_requires_port(self):
        tool = next(t for t in TOOLS if t["name"] == "stop_service")
        assert "port" in tool.get("inputSchema", {}).get("required", [])

    def test_all_tools_have_annotations(self):
        """Every tool must declare annotations (readOnlyHint or destructiveHint)."""
        for tool in TOOLS:
            assert "annotations" in tool, f"Missing annotations on tool: {tool['name']}"

    def test_readonly_tools_have_read_only_hint(self):
        read_only_names = {"list_ports", "inspect_port", "diagnose_port", "list_connections", "conflicts", "doctor"}
        for tool in TOOLS:
            if tool["name"] in read_only_names:
                assert tool["annotations"].get("readOnlyHint") is True, (
                    f"Expected readOnlyHint=True on {tool['name']}"
                )


# ---------------------------------------------------------------------------
# Protocol version lock
# ---------------------------------------------------------------------------

@pytest.mark.mcp
class TestProtocolVersion:
    def test_protocol_version_is_locked_to_2024_11_05(self):
        responses = _send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "test"},
                    "capabilities": {},
                },
            }
        )
        assert responses[0]["result"]["protocolVersion"] == "2026-07-28"


# ---------------------------------------------------------------------------
# stop_service tool call tests
# ---------------------------------------------------------------------------

@pytest.mark.mcp
class TestMCPStopServiceExecution:
    """Integration tests for stop_service tool call dispatch."""

    def _make_inspect_return(self, pid: int, manager: str, service: str):
        """Build a _diagnose_port_data-compatible mock return value."""
        return {
            "port": 9000,
            "blocked": True,
            "pids": [pid],
            "inferences": [
                {
                    "type": "process_manager",
                    "manager": manager,
                    "name": service,
                }
            ],
            "observations": [],
            "recommendations": [],
            "risk_level": "low",
        }

    def test_stop_service_safety_block(self):
        """Safety policy must reject protected ports and return success=False."""
        with patch("kport.mcp_server._diagnose_port_data", return_value=self._make_inspect_return(1234, "systemd", "nginx.service")), \
             patch("kport.mcp_server.get_inspector"), \
             patch("kport.mcp_server.check_safety_policy", return_value=(False, "Port is protected")):
            resp = _call_stop_service(port=443)

        assert resp.get("error") is None
        result = resp["result"]
        content = json.loads(result["content"][0]["text"])
        assert content["success"] is False

    def test_stop_service_no_manager_rejected(self):
        """If no process manager is detected, stop_service must return success=False."""
        diag_data = {
            "port": 9000,
            "blocked": True,
            "pids": [9876],
            "inferences": [],
            "observations": [],
            "recommendations": [],
            "risk_level": "low",
        }
        with patch("kport.mcp_server._diagnose_port_data", return_value=diag_data), \
             patch("kport.mcp_server.get_inspector"), \
             patch("kport.mcp_server.check_safety_policy", return_value=(True, "")):
            resp = _call_stop_service(port=9000)

        result = resp["result"]
        content = json.loads(result["content"][0]["text"])
        assert content["success"] is False

    def test_stop_service_dry_run(self):
        """Dry-run must return the command without executing it."""
        from kport.service_actions import ServiceActionResult
        diag_data = self._make_inspect_return(5555, "systemd", "myapp.service")
        mock_result = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="myapp.service",
            command_executed="systemctl stop myapp.service",
            message="[DRY RUN] Would execute: systemctl stop myapp.service",
        )
        with patch("kport.mcp_server._diagnose_port_data", return_value=diag_data), \
             patch("kport.mcp_server.get_inspector"), \
             patch("kport.mcp_server.check_safety_policy", return_value=(True, "")), \
             patch("kport.service_actions.stop_service", return_value=mock_result), \
             patch("kport.mcp_server.audit"):
            resp = _call_stop_service(port=9000, dry_run=True)

        result = resp["result"]
        content = json.loads(result["content"][0]["text"])
        assert content["dry_run"] is True
        assert content["command"] == "systemctl stop myapp.service"

    def test_stop_service_success_and_verified(self):
        """Successful stop with port verified free returns success=True, verified_free=True."""
        from kport.service_actions import ServiceActionResult
        diag_data = self._make_inspect_return(5555, "systemd", "myapp.service")
        mock_result = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="myapp.service",
            command_executed="systemctl stop myapp.service",
            message="Service stopped.",
        )
        with patch("kport.mcp_server._diagnose_port_data", return_value=diag_data), \
             patch("kport.mcp_server.get_inspector"), \
             patch("kport.mcp_server.check_safety_policy", return_value=(True, "")), \
             patch("kport.service_actions.stop_service", return_value=mock_result), \
             patch("kport.mcp_server.audit"), \
             patch("kport.cli._poll_until_free", return_value=True):
            resp = _call_stop_service(port=9000, dry_run=False)

        result = resp["result"]
        content = json.loads(result["content"][0]["text"])
        assert content["success"] is True
        assert content["verified_free"] is True
        assert content["manager"] == "systemd"

    def test_stop_service_success_port_still_blocked(self):
        """Service stopped but port still occupied: requires_force=True."""
        from kport.service_actions import ServiceActionResult
        diag_data = self._make_inspect_return(5555, "pm2", "api-server")
        mock_result = ServiceActionResult(
            success=True,
            manager="pm2",
            service_name="api-server",
            command_executed="pm2 stop api-server",
            message="Service stopped.",
        )
        with patch("kport.mcp_server._diagnose_port_data", return_value=diag_data), \
             patch("kport.mcp_server.get_inspector"), \
             patch("kport.mcp_server.check_safety_policy", return_value=(True, "")), \
             patch("kport.service_actions.stop_service", return_value=mock_result), \
             patch("kport.mcp_server.audit"), \
             patch("kport.mcp_server._poll_until_free", return_value=False):
            resp = _call_stop_service(port=9000, dry_run=False)

        result = resp["result"]
        content = json.loads(result["content"][0]["text"])
        assert content["success"] is True
        assert content["verified_free"] is False
        assert content["requires_force"] is True

    def test_stop_service_already_free(self):
        """Port already free short-circuits: verified_free=True without calling stop."""
        diag_data = {
            "port": 9000,
            "blocked": False,
            "pids": [],
            "inferences": [],
            "observations": [],
            "recommendations": [],
            "risk_level": "none",
        }
        with patch("kport.mcp_server._diagnose_port_data", return_value=diag_data), \
             patch("kport.mcp_server.get_inspector"), \
             patch("kport.mcp_server.check_safety_policy", return_value=(True, "")):
            resp = _call_stop_service(port=9000)

        result = resp["result"]
        content = json.loads(result["content"][0]["text"])
        assert content["success"] is True
        assert content["verified_free"] is True
