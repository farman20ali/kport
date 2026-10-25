"""
Unit tests for MCP server features and security enforcement:
- inspect_process tool
- swap tool
- Dynamic MCP v2 safety rules integration (_check_mcp_safety)
- Multi-tenant agent_id enforcement
"""
from __future__ import annotations

from unittest.mock import patch

from kport.inspectors.base import BaseInspector, PortBinding, ProcessInfo
from kport.mcp_rules import McpRule, RuleSet
from kport.mcp_server import (
    _check_mcp_safety,
    handle_inspect_process,
    handle_kill_pid,
    handle_kill_port,
    handle_swap,
)


class MockMcpInspector(BaseInspector):
    def __init__(self):
        super().__init__()
        self.bindings = [
            PortBinding(port=3000, family="IPv4", proto="tcp", pid=101, process_name="node (server.js)", laddr="0.0.0.0:3000"),
            PortBinding(port=8080, family="IPv4", proto="tcp", pid=102, process_name="python.exe (kport)", laddr="127.0.0.1:8080"),
        ]
        self.pids = {3000: [101], 8080: [102]}

    def list_listening(self, proto: str = "both") -> list[PortBinding]:
        return self.bindings

    def find_pids_on_port(self, port: int, proto: str = "both") -> list[int]:
        return self.pids.get(port, [])

    def find_bindings_on_port(self, port: int, proto: str = "both") -> list[PortBinding]:
        return [b for b in self.bindings if b.port == port]

    def find_ports_by_process_name(self, name: str, exact: bool = False, proto: str = "both") -> list[PortBinding]:
        name_lower = name.lower()
        return [b for b in self.bindings if name_lower in (b.process_name or "").lower()]

    def find_pids_by_name(self, name: str, exact: bool = False) -> list[int]:
        if "node" in name.lower():
            return [101]
        if "kport" in name.lower():
            return [102]
        return []

    def get_process_info(self, pid: int) -> ProcessInfo | None:
        if pid == 101:
            return ProcessInfo(pid=101, name="node", cmdline=["node", "server.js"])
        if pid == 102:
            return ProcessInfo(pid=102, name="python.exe", cmdline=["python.exe", "kport.exe"])
        return None

    def kill_port(self, port: int, **kwargs) -> tuple[bool, str]:
        return True, f"Port {port} terminated"

    def kill_pid(self, pid: int, **kwargs) -> tuple[bool, str]:
        return True, f"PID {pid} terminated"

    def kill_process_tree(self, pid: int, **kwargs) -> tuple[bool, str]:
        return True, f"Process tree {pid} terminated"


def test_mcp_handle_inspect_process():
    """Verify handle_inspect_process finds port bindings and running PIDs."""
    inspector = MockMcpInspector()
    res = handle_inspect_process(inspector, "kport")
    assert res["count"] == 1
    assert res["process"] == "kport"
    assert res["bindings"][0]["port"] == 8080
    assert 102 in res["running_pids"]


def test_mcp_safety_enforces_ruleset():
    """Verify _check_mcp_safety blocks actions denied by MCP v2 ruleset."""
    inspector = MockMcpInspector()
    # Create ruleset denying port 8080
    ruleset = RuleSet(
        version=1,
        default_policy="allow",
        rules=[
            McpRule(id="deny-8080", effect="deny", port_ranges=[[8080, 8080]], description="Port 8080 is blocked"),
        ],
    )
    with patch("kport.mcp_rules.load_rules", return_value=ruleset):
        allowed, reason = _check_mcp_safety(inspector, action="kill_port", port=8080, pids=[102])
        assert not allowed
        assert "Blocked by MCP Safety Rules" in reason
        assert "deny-8080" in reason


def test_mcp_kill_port_respects_mcp_ruleset():
    """Verify handle_kill_port aborts when blocked by MCP v2 rule."""
    inspector = MockMcpInspector()
    ruleset = RuleSet(
        version=1,
        default_policy="allow",
        rules=[
            McpRule(id="no-kill-dev", effect="deny", port_ranges=[[8080, 8080]], description="Dev port is protected"),
        ],
    )
    with patch("kport.mcp_rules.load_rules", return_value=ruleset):
        res = handle_kill_port(inspector, 8080)
        assert res["success"] is False
        assert "no-kill-dev" in res["message"]


def test_mcp_enterprise_agent_id_enforcement():
    """Verify enterprise preset allows only authorized agent_ids."""
    inspector = MockMcpInspector()
    ruleset = RuleSet(
        version=1,
        default_policy="deny",
        rules=[
            McpRule(id="trusted-agent", effect="allow", agent_ids=["authorized-bot"], actions=["*"]),
        ],
    )
    with patch("kport.mcp_rules.load_rules", return_value=ruleset):
        # Untrusted agent should be denied
        res_denied = handle_kill_pid(inspector, 101, agent_id="untrusted-bot")
        assert res_denied["success"] is False
        assert "default policy" in res_denied["message"]

        # Authorized agent should be allowed
        res_allowed = handle_kill_pid(inspector, 101, agent_id="authorized-bot")
        assert res_allowed["success"] is True


def test_mcp_swap_dry_run():
    """Verify handle_swap handles dry-run without executing kills."""
    inspector = MockMcpInspector()
    res = handle_swap(inspector, 8080, dry_run=True)
    assert res["success"] is True
    assert res["dry_run"] is True
    assert "would free and hold" in res["action"]
