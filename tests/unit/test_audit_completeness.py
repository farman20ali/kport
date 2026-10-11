"""
tests/unit/test_audit_completeness.py — Phase 9 regression tests.

Verifies that audit.log_kill_port is called by:
  A. MCP handle_kill_port when a local process kill succeeds.
  B. TUI _execute_kills when a local kill_port call is made.

These tests cover the specific gap identified in Phase 8.5 audit findings F-2 and F-2b.
They do NOT duplicate existing audit format tests in test_audit.py.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# A. MCP kill_port emits audit record
# ---------------------------------------------------------------------------


class TestMCPKillPortAudit:
    """MCP handle_kill_port must emit audit.log_kill_port for local process kills."""

    def _make_inspector(self, pids, kill_result):
        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = pids
        inspector.find_bindings_on_port.return_value = []
        inspector.kill_port.return_value = kill_result
        return inspector

    def test_mcp_kill_port_success_emits_audit(self):
        """A successful MCP local kill must emit one audit record with correct fields."""
        from kport import audit, mcp_server

        inspector = self._make_inspector([1234], (True, "Killed 1 process(es)"))
        audit_calls = []

        with patch.object(mcp_server, "get_inspector", return_value=inspector), \
             patch.object(mcp_server, "check_safety_policy", return_value=(True, "ok")), \
             patch.object(mcp_server, "docker_mappings_for_host_port", return_value=[]), \
             patch.object(mcp_server, "load_mcp_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            result = mcp_server.handle_kill_port(inspector, 8080)

        assert result["success"] is True
        assert result["type"] == "local"
        assert len(audit_calls) == 1
        call = audit_calls[0]
        assert call["port"] == 8080
        assert call["pids"] == [1234]
        assert call["dry_run"] is False
        assert call["success"] is True
        assert "Killed" in call["message"]

    def test_mcp_kill_port_failure_still_emits_audit(self):
        """A failed MCP local kill must emit an audit record with success=False."""
        from kport import audit, mcp_server

        inspector = self._make_inspector([5678], (False, "Permission denied"))
        audit_calls = []

        with patch.object(mcp_server, "get_inspector", return_value=inspector), \
             patch.object(mcp_server, "check_safety_policy", return_value=(True, "ok")), \
             patch.object(mcp_server, "docker_mappings_for_host_port", return_value=[]), \
             patch.object(mcp_server, "load_mcp_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            result = mcp_server.handle_kill_port(inspector, 9090)

        assert result["success"] is False
        assert len(audit_calls) == 1
        call = audit_calls[0]
        assert call["port"] == 9090
        assert call["pids"] == [5678]
        assert call["success"] is False
        assert call["dry_run"] is False

    def test_mcp_kill_port_safety_blocked_does_not_emit_audit(self):
        """If safety policy blocks the kill, no audit record should be emitted."""
        from kport import audit, mcp_server

        inspector = self._make_inspector([22], (False, "blocked"))
        audit_calls = []

        with patch.object(mcp_server, "get_inspector", return_value=inspector), \
             patch.object(mcp_server, "check_safety_policy",
                          return_value=(False, "Protected port: 22 (SSH)")), \
             patch.object(mcp_server, "load_mcp_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            result = mcp_server.handle_kill_port(inspector, 22)

        assert result["success"] is False
        assert len(audit_calls) == 0

    def test_mcp_kill_port_response_structure_unchanged(self):
        """Adding audit logging must not alter the MCP response structure."""
        from kport import audit, mcp_server

        inspector = self._make_inspector([1234], (True, "Port freed"))

        with patch.object(mcp_server, "get_inspector", return_value=inspector), \
             patch.object(mcp_server, "check_safety_policy", return_value=(True, "ok")), \
             patch.object(mcp_server, "docker_mappings_for_host_port", return_value=[]), \
             patch.object(mcp_server, "load_mcp_config", return_value={}), \
             patch.object(audit, "log_kill_port", return_value=None):
            result = mcp_server.handle_kill_port(inspector, 3000)

        assert "success" in result
        assert "type" in result
        assert "pids_targeted" in result
        assert "message" in result
        assert result["type"] == "local"


# ---------------------------------------------------------------------------
# B. TUI _execute_kills emits audit record
# ---------------------------------------------------------------------------


class TestTUIKillAudit:
    """TUI _execute_kills must emit audit.log_kill_port for local port kills."""

    def _local_row(self, port, pid=123, process="node"):
        return {
            "type": "local",
            "port": port,
            "pid": pid,
            "process": process,
            "proto": "tcp",
            "state": "LISTEN",
            "managed_by": "",
        }

    def _make_args(self, *, yes=True, dry_run=False, force=False):
        import argparse
        return argparse.Namespace(
            yes=yes,
            dry_run=dry_run,
            force=force,
            kill_tree=False,
            bypass_safety=False,
            protected_ports=None,
            protected_processes=None,
        )

    def test_tui_local_kill_success_emits_audit(self):
        """A successful TUI kill must emit one audit record with correct fields."""
        from kport import audit
        from kport.interactive import _execute_kills

        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = [123]
        inspector.kill_port.return_value = (True, "Port 8080 freed")
        audit_calls = []

        with patch("kport.interactive.check_safety_policy", return_value=(True, "ok")), \
             patch("kport.interactive.load_kport_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            rc = _execute_kills(inspector, [self._local_row(8080, pid=123)], self._make_args())

        assert rc == 0
        assert len(audit_calls) == 1
        call = audit_calls[0]
        assert call["port"] == 8080
        assert call["pids"] == [123]
        assert call["dry_run"] is False
        assert call["success"] is True
        assert call["message"] == "Port 8080 freed"

    def test_tui_local_kill_failure_emits_audit_with_success_false(self):
        """A failed TUI kill must emit an audit record with success=False."""
        from kport import audit
        from kport.interactive import _execute_kills

        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = [999]
        inspector.kill_port.return_value = (False, "Still running after graceful timeout")
        audit_calls = []

        with patch("kport.interactive.check_safety_policy", return_value=(True, "ok")), \
             patch("kport.interactive.load_kport_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            rc = _execute_kills(inspector, [self._local_row(7070, pid=999)], self._make_args())

        assert rc == 1
        assert len(audit_calls) == 1
        call = audit_calls[0]
        assert call["port"] == 7070
        assert call["pids"] == [999]
        assert call["success"] is False
        assert call["dry_run"] is False

    def test_tui_dry_run_emits_audit_with_dry_run_true(self):
        """Dry-run TUI kill must emit an audit record with dry_run=True."""
        from kport import audit
        from kport.interactive import _execute_kills

        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = [555]
        inspector.kill_port.return_value = (True, "Dry-run: would terminate port 5000")
        audit_calls = []

        with patch("kport.interactive.check_safety_policy", return_value=(True, "ok")), \
             patch("kport.interactive.load_kport_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            rc = _execute_kills(
                inspector,
                [self._local_row(5000, pid=555)],
                self._make_args(dry_run=True),
            )

        assert rc == 0
        assert len(audit_calls) == 1
        call = audit_calls[0]
        assert call["dry_run"] is True
        assert call["port"] == 5000

    def test_tui_safety_blocked_does_not_emit_audit(self):
        """If safety policy blocks the port, no kill and no audit record."""
        from kport import audit
        from kport.interactive import _execute_kills

        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = [22]
        audit_calls = []

        with patch("kport.interactive.check_safety_policy",
                   return_value=(False, "Protected port: 22 (SSH)")), \
             patch("kport.interactive.load_kport_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            rc = _execute_kills(inspector, [self._local_row(22)], self._make_args())

        assert rc == 1
        assert len(audit_calls) == 0

    def test_tui_docker_row_does_not_emit_kill_port_audit(self):
        """Docker rows go through log_docker_action, not log_kill_port."""
        from kport import audit
        from kport.interactive import _execute_kills

        inspector = MagicMock()
        docker_row = {
            "type": "docker",
            "port": 8080,
            "pid": "abc123def456",
            "process": "my-container",
            "proto": "tcp",
            "state": "Docker (80)",
            "managed_by": "docker:nginx:latest",
        }
        kill_port_audit_calls = []

        with patch("kport.interactive.check_safety_policy", return_value=(True, "ok")), \
             patch("kport.interactive.load_kport_config", return_value={}), \
             patch("kport.interactive.docker_action_on_container",
                   return_value=(True, "Container stopped")), \
             patch.object(audit, "log_kill_port",
                          side_effect=lambda **kw: kill_port_audit_calls.append(kw)):
            rc = _execute_kills(inspector, [docker_row], self._make_args())

        assert rc == 0
        assert len(kill_port_audit_calls) == 0

    def test_tui_pid_none_row_emits_empty_pids_list(self):
        """If row pid is None (hidden process), audit pids list must be empty."""
        from kport import audit
        from kport.interactive import _execute_kills

        inspector = MagicMock()
        inspector.find_pids_on_port.return_value = []
        inspector.kill_port.return_value = (True, "Port freed")

        row = self._local_row(4040, pid=None)
        audit_calls = []

        with patch("kport.interactive.check_safety_policy", return_value=(True, "ok")), \
             patch("kport.interactive.load_kport_config", return_value={}), \
             patch.object(audit, "log_kill_port", side_effect=lambda **kw: audit_calls.append(kw)):
            rc = _execute_kills(inspector, [row], self._make_args())

        assert rc == 0
        assert len(audit_calls) == 1
        assert audit_calls[0]["pids"] == []
