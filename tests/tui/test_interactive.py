"""
TUI and interactive picker tests (tests/tui/test_interactive.py).

Tests row gathering, numbered menu fallback, confirmation prompts,
safety blocking, kill execution, and curses key-handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kport.interactive import (
    _execute_kills,
    _fallback_numbered_menu,
    _fetch_interactive_rows,
    run_interactive_picker,
)
from tests.conftest import FakeInspector, _args, _binding


@pytest.mark.tui
class TestFetchInteractiveRows:
    """Tests for gathering display rows from the inspector and docker."""

    def test_local_binding_produces_local_row(self):
        inspector = FakeInspector(
            listening=[_binding(8080, pid=123, name="python")]
        )
        with patch("kport.interactive.list_docker_mappings", return_value=[]):
            rows = _fetch_interactive_rows(inspector)
        assert len(rows) == 1
        assert rows[0]["port"] == 8080
        assert rows[0]["process"] == "python"
        assert rows[0]["type"] == "local"

    def test_empty_listening_and_no_docker_returns_empty(self):
        inspector = FakeInspector()
        with patch("kport.interactive.list_docker_mappings", return_value=[]):
            rows = _fetch_interactive_rows(inspector)
        assert rows == []


@pytest.mark.tui
class TestFallbackNumberedMenu:
    """Tests for the non-curses numbered menu fallback mode."""

    def test_selection_kills_chosen_port(self):
        killed_ports = []

        class SelectInspector(FakeInspector):
            def list_listening(self, proto: str = "tcp"):
                return [_binding(3000, pid=55, name="vite")]

            def kill_port(self, port, **kwargs):
                killed_ports.append(port)
                return True, "killed"

        inspector = SelectInspector()
        args = _args(command="interactive", yes=True)
        with patch("builtins.input", return_value="1"), \
             patch("kport.interactive.list_docker_mappings", return_value=[]):
            rc = _fallback_numbered_menu(inspector, args)
        assert rc == 0
        assert killed_ports == [3000]

    def test_non_tty_mode_exits_cleanly(self):
        inspector = FakeInspector()
        args = _args(command="interactive")
        with patch("sys.stdin.isatty", return_value=False), \
             patch("kport.interactive.list_docker_mappings", return_value=[]):
            rc = run_interactive_picker(inspector, args)
        assert rc == 0


@pytest.mark.tui
class TestExecuteKills:
    """Tests for _execute_kills confirmation, safety, and execution."""

    def _local_row(self, port: int, pid: int = 123, process: str = "node") -> dict:
        return {
            "type": "local",
            "port": port,
            "pid": pid,
            "process": process,
            "proto": "tcp",
            "state": "LISTEN",
            "managed_by": "",
        }

    def test_confirmation_yes_kills_port(self):
        inspector = FakeInspector()
        killed_ports = []
        inspector.kill_port = lambda port, **kw: (killed_ports.append(port) or True, "killed")
        args = _args(command="interactive", yes=False)
        with patch("kport.interactive.confirm_prompt", return_value=True):
            rc = _execute_kills(inspector, [self._local_row(8080)], args)
        assert rc == 0
        assert killed_ports == [8080]

    def test_confirmation_no_skips_kill(self):
        inspector = FakeInspector()
        killed_ports = []
        inspector.kill_port = lambda port, **kw: (killed_ports.append(port) or True, "killed")
        args = _args(command="interactive", yes=False)
        with patch("kport.interactive.confirm_prompt", return_value=False):
            rc = _execute_kills(inspector, [self._local_row(8080)], args)
        assert rc == 0
        assert killed_ports == []

    def test_protected_port_blocked_without_killing(self):
        """Interactive picker must not kill protected port 22."""
        inspector = FakeInspector()
        killed_ports = []
        inspector.kill_port = lambda port, **kw: (killed_ports.append(port) or True, "killed")
        args = _args(command="interactive", yes=True)
        rc = _execute_kills(inspector, [self._local_row(22, process="sshd")], args)
        assert rc == 1
        assert killed_ports == []

    def test_protected_process_name_blocked(self):
        """Interactive picker must not kill a process named systemd."""
        from kport.inspectors.base import ProcessInfo
        inspector = FakeInspector(
            pids_on_port={8080: [123]},
            process_info={123: ProcessInfo(pid=123, name="systemd")},
            bindings_on_port={8080: [_binding(8080, pid=123, name="systemd")]},
        )
        killed_ports = []
        inspector.kill_port = lambda port, **kw: (killed_ports.append(port) or True, "killed")
        args = _args(command="interactive", yes=True)
        # Port 8080 is not in protected ports but process "systemd" is protected
        rc = _execute_kills(inspector, [self._local_row(8080, process="systemd")], args)
        assert rc == 1
        assert killed_ports == []


@pytest.mark.tui
class TestCursesKeyHandling:
    """Tests for interactive curses key events (skipped where curses unavailable)."""

    def test_curses_quit_via_slash_q(self):
        pytest.importorskip("_curses")
        inspector = FakeInspector(
            listening=[_binding(8080, pid=123, name="node")]
        )
        args = _args(command="interactive", yes=True)
        mock_stdscr = MagicMock()
        mock_stdscr.getmaxyx.return_value = (24, 80)
        keys = [ord("n"), 27, ord("/"), ord("q")]
        mock_stdscr.getch.side_effect = keys
        with patch("sys.stdin.isatty", return_value=True), \
             patch("sys.stdout.isatty", return_value=True), \
             patch("kport.interactive.list_docker_mappings", return_value=[]), \
             patch("curses.wrapper", side_effect=lambda func: func(mock_stdscr)), \
             patch("curses.curs_set"), \
             patch("curses.start_color"), \
             patch("curses.use_default_colors"), \
             patch("curses.init_pair"), \
             patch("curses.color_pair"):
            rc = run_interactive_picker(inspector, args)
        assert rc == 0

    def test_curses_reload_via_ctrl_r(self):
        pytest.importorskip("_curses")
        inspector = FakeInspector()
        args = _args(command="interactive", yes=True)
        mock_stdscr = MagicMock()
        mock_stdscr.getmaxyx.return_value = (24, 80)
        keys = [18, ord("/"), ord("r"), 27]
        mock_stdscr.getch.side_effect = keys
        with patch("sys.stdin.isatty", return_value=True), \
             patch("sys.stdout.isatty", return_value=True), \
             patch("kport.interactive.list_docker_mappings", return_value=[]), \
             patch("curses.wrapper", side_effect=lambda func: func(mock_stdscr)), \
             patch("curses.curs_set"), \
             patch("curses.start_color"), \
             patch("curses.use_default_colors"), \
             patch("curses.init_pair"), \
             patch("curses.color_pair"):
            rc = run_interactive_picker(inspector, args)
        assert rc == 0


@pytest.mark.tui
class TestCursesWrapperExceptionHandling:
    """Tests for curses.wrapper exception and interruption boundaries."""

    def test_keyboard_interrupt_exits_gracefully_with_zero(self):
        pytest.importorskip("_curses")
        inspector = FakeInspector()
        args = _args(command="interactive")

        with patch("sys.stdin.isatty", return_value=True), \
             patch("sys.stdout.isatty", return_value=True), \
             patch("curses.wrapper", side_effect=KeyboardInterrupt), \
             patch("kport.interactive._fallback_numbered_menu") as mock_fallback, \
             patch("builtins.print") as mock_print:
            rc = run_interactive_picker(inspector, args)

        assert rc == 0
        mock_print.assert_any_call("\nCancelled.")
        mock_fallback.assert_not_called()

    def test_generic_exception_triggers_fallback_menu(self):
        pytest.importorskip("_curses")
        inspector = FakeInspector()
        args = _args(command="interactive")

        with patch("sys.stdin.isatty", return_value=True), \
             patch("sys.stdout.isatty", return_value=True), \
             patch("curses.wrapper", side_effect=RuntimeError("Terminal broken")), \
             patch("kport.interactive._fallback_numbered_menu", return_value=42) as mock_fallback:
            rc = run_interactive_picker(inspector, args)

        assert rc == 42
        mock_fallback.assert_called_once_with(inspector, args)

