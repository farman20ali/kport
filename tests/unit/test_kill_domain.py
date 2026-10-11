"""
Unit tests for decoupled kill confirmation and neutral port polling helper.
"""

from __future__ import annotations

import signal

import pytest

from kport.inspectors.base import BaseInspector, ProcessInfo
from kport.port_utils import poll_until_free

# Fallback for Windows where signal.SIGKILL is not defined in standard library
SIGKILL = getattr(signal, "SIGKILL", 9)
SIGTERM = getattr(signal, "SIGTERM", 15)


class KillTestInspector(BaseInspector):
    """Minimal inspector subclass that inherits BaseInspector's real kill logic."""

    def __init__(self):
        self.pids_on_port = {}
        self.is_alive = {}
        self.signals_sent = []
        self.proc_info = {}
        self.child_pids = {}

    def find_pids_on_port(self, port: int, proto: str = "tcp"):
        return self.pids_on_port.get(port, [])

    def is_process_alive(self, pid: int) -> bool:
        return self.is_alive.get(pid, False)

    def send_signal(self, pid: int, sig: int) -> bool:
        self.signals_sent.append((pid, sig))
        if sig == SIGKILL:
            self.is_alive[pid] = False
        return True

    def get_process_info(self, pid: int):
        return self.proc_info.get(pid)

    def get_child_pids(self, pid: int) -> list[int]:
        return self.child_pids.get(pid, [])

    def list_listening(self, proto: str = "tcp"):
        return []


@pytest.mark.unit
class TestKillDomainConfirmation:
    """Verify decoupled confirmation semantics."""

    def test_kill_pid_dry_run_no_confirm_needed(self):
        inspector = KillTestInspector()
        inspector.is_alive[123] = True
        
        # dry_run=True should return True and not require confirm_fn
        ok, msg = inspector.kill_pid(
            123,
            dry_run=True,
            assume_yes=False,
            confirm_fn=None,
        )
        assert ok is True
        assert "Dry-run" in msg
        assert len(inspector.signals_sent) == 0

    def test_kill_pid_assume_yes_bypasses_confirmation(self):
        inspector = KillTestInspector()
        inspector.is_alive[123] = True
        
        # With assume_yes=True and confirm_fn=None, it should terminate (gracefully here since it does not run into timeout)
        # Let's set graceful_timeout to 0.05 so it times out quickly and tries force kill (SIGKILL).
        ok, msg = inspector.kill_pid(
            123,
            graceful_timeout=0.01,
            force=False,
            dry_run=False,
            assume_yes=True,
            confirm_fn=None,
        )
        # since assume_yes=True is passed, it automatically escalates to SIGKILL, which kills it.
        assert ok is True
        assert "Killed (force)" in msg
        assert (123, SIGTERM) in inspector.signals_sent
        assert (123, SIGKILL) in inspector.signals_sent

    def test_kill_pid_confirm_fn_true_allows_action(self):
        inspector = KillTestInspector()
        inspector.is_alive[123] = True
        inspector.proc_info[123] = ProcessInfo(pid=123, name="test-proc")
        
        confirm_called = []
        def confirm_fn(prompt):
            confirm_called.append(prompt)
            return True

        ok, msg = inspector.kill_pid(
            123,
            graceful_timeout=0.01,
            force=False,
            dry_run=False,
            assume_yes=False,
            confirm_fn=confirm_fn,
        )
        assert ok is True
        assert "Killed (force)" in msg
        assert len(confirm_called) == 1
        assert "Force kill?" in confirm_called[0]
        assert (123, SIGKILL) in inspector.signals_sent

    def test_kill_pid_confirm_fn_false_prevents_action(self):
        inspector = KillTestInspector()
        inspector.is_alive[123] = True
        inspector.proc_info[123] = ProcessInfo(pid=123, name="test-proc")
        
        confirm_called = []
        def confirm_fn(prompt):
            confirm_called.append(prompt)
            return False

        ok, msg = inspector.kill_pid(
            123,
            graceful_timeout=0.01,
            force=False,
            dry_run=False,
            assume_yes=False,
            confirm_fn=confirm_fn,
        )
        assert ok is False
        assert "Still running after graceful timeout" in msg
        assert len(confirm_called) == 1
        assert (123, SIGKILL) not in inspector.signals_sent

    def test_kill_pid_no_confirm_fn_and_no_assume_yes_safely_refuses(self):
        inspector = KillTestInspector()
        inspector.is_alive[123] = True
        inspector.proc_info[123] = ProcessInfo(pid=123, name="test-proc")
        
        # When not interactive (confirm_fn=None) and assume_yes=False, force kill is aborted.
        ok, msg = inspector.kill_pid(
            123,
            graceful_timeout=0.01,
            force=False,
            dry_run=False,
            assume_yes=False,
            confirm_fn=None,
        )
        assert ok is False
        assert "Still running after graceful timeout" in msg
        assert (123, SIGKILL) not in inspector.signals_sent

    def test_escalation_threads_confirm_fn_unix_windows(self):
        # Test that _try_escalate receives and uses confirm_fn.
        inspector = KillTestInspector()
        
        confirm_calls = []
        def mock_confirm(prompt):
            confirm_calls.append(prompt)
            return False
            
        # PermissionError is triggered when we get PermissionError on send_signal.
        # Let's override send_signal to raise PermissionError
        def send_sig_err(pid, sig):
            raise PermissionError("Access denied")
        inspector.send_signal = send_sig_err
        
        ok, msg = inspector.kill_pid(
            123,
            dry_run=False,
            assume_yes=False,
            confirm_fn=mock_confirm,
        )
        assert ok is False
        assert "Permission denied" in msg
        assert len(confirm_calls) == 1
        # Checks prompt contains expected text for privilege escalation
        assert "requires elevated privileges" in confirm_calls[0]


@pytest.mark.unit
class TestNeutralPolling:
    """Verify neutral port polling helper."""

    def test_poll_until_free_returns_true_when_port_becomes_free(self):
        inspector = KillTestInspector()
        # Simulate port bound at first, then becomes free on 3rd check
        call_count = 0
        def find_bindings_fake(port, proto="tcp"):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return ["some-binding"]
            return []
        inspector.find_bindings_on_port = find_bindings_fake

        res = poll_until_free(8080, timeout=1.0, inspector=inspector, interval=0.01)
        assert res is True
        assert call_count == 3

    def test_poll_until_free_returns_false_on_timeout(self):
        inspector = KillTestInspector()
        # Simulate port remaining bound indefinitely
        inspector.find_bindings_on_port = lambda port, proto="tcp": ["some-binding"]

        res = poll_until_free(8080, timeout=0.05, inspector=inspector, interval=0.01)
        assert res is False


@pytest.mark.unit
class TestNoPresentationImportInBase:
    """Enforce that base.py never imports from formatter.py or calls input."""

    def test_base_py_imports_clean(self):
        from kport.inspectors import base
        # Ensure confirm_prompt is not in base.py's global namespace
        assert not hasattr(base, "confirm_prompt")
        
        # Open base.py file and confirm no formatter imports are present textually
        base_path = base.__file__
        with open(base_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        assert "from kport.formatter" not in content
        assert "from .formatter" not in content
