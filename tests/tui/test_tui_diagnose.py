"""
Focused tests for Phase 10 TUI Diagnose Integration.
Verifies d key binding to trigger port diagnostics under curses and fallback modes.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kport.interactive import _fallback_numbered_menu, run_interactive_picker
from tests.conftest import FakeInspector, _args, _binding


@pytest.mark.tui
class TestTUIDiagnoseCurses:
    """Curses key handling tests for the diagnose key binding."""

    @patch("kport.interactive.diagnose_port")
    def test_d_key_opens_diagnose_and_closes_via_esc(self, mock_diag):
        pytest.importorskip("_curses")
        inspector = FakeInspector(
            listening=[_binding(8080, pid=123, name="node")]
        )
        args = _args(command="interactive", yes=True)
        mock_stdscr = MagicMock()
        mock_stdscr.getmaxyx.return_value = (24, 80)

        # Keys sequence:
        # 1. ord("d") -> opens diagnose overlay
        # 2. 27 (Esc) -> closes diagnose overlay
        # 3. 27 (Esc) -> quits main TUI loop
        keys = [ord("d"), 27, 27]
        mock_stdscr.getch.side_effect = keys

        mock_diag.return_value = {
            "port": 8080,
            "blocked": True,
            "observations": {
                "type": "local",
                "processes": [{"pid": 123, "name": "node", "cmdline": ["node", "app.js"]}],
                "docker_containers": [],
                "bindings": [],
            },
            "inferences": [],
            "risks": [],
            "recommendations": [{"action": "kill", "reason": "standard process", "safe": True}],
        }

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
        mock_diag.assert_called_once_with(
            port=8080,
            inspector=inspector,
            proto="tcp",
            config=None,
            bypass_safety=False,
        )

    @patch("kport.interactive.diagnose_port")
    def test_d_key_closes_via_q(self, mock_diag):
        pytest.importorskip("_curses")
        inspector = FakeInspector(
            listening=[_binding(8080, pid=123, name="node")]
        )
        args = _args(command="interactive", yes=True)
        mock_stdscr = MagicMock()
        mock_stdscr.getmaxyx.return_value = (24, 80)

        # Keys sequence:
        # 1. ord("d") -> opens diagnose overlay
        # 2. ord("q") -> closes diagnose overlay
        # 3. 27 (Esc) -> quits main TUI loop
        keys = [ord("d"), ord("q"), 27]
        mock_stdscr.getch.side_effect = keys

        mock_diag.return_value = {
            "port": 8080,
            "blocked": True,
            "observations": {
                "type": "local",
                "processes": [{"pid": 123, "name": "node", "cmdline": ["node", "app.js"]}],
                "docker_containers": [],
                "bindings": [],
            },
            "inferences": [],
            "risks": [],
            "recommendations": [{"action": "kill", "reason": "standard process", "safe": True}],
        }

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
        assert mock_diag.call_count == 1

    def test_d_key_with_empty_filtered_rows_does_not_crash(self):
        pytest.importorskip("_curses")
        inspector = FakeInspector(
            listening=[_binding(8080, pid=123, name="node")]
        )
        args = _args(command="interactive", yes=True)
        mock_stdscr = MagicMock()
        mock_stdscr.getmaxyx.return_value = (24, 80)

        # Keys sequence:
        # 1. ord("z") -> types z (search_query = "z"), filtering out the 8080 row
        # 2. ord("d") -> diagnostic attempt on empty filtered_rows -> triggers message
        # 3. 27 (Esc) -> clear search query
        # 4. 27 (Esc) -> quit TUI picker
        keys = [ord("z"), ord("d"), 27, 27]
        mock_stdscr.getch.side_effect = keys

        with patch("sys.stdin.isatty", return_value=True), \
             patch("sys.stdout.isatty", return_value=True), \
             patch("kport.interactive.list_docker_mappings", return_value=[]), \
             patch("curses.wrapper", side_effect=lambda func: func(mock_stdscr)), \
             patch("curses.curs_set"), \
             patch("curses.start_color"), \
             patch("curses.use_default_colors"), \
             patch("curses.init_pair"), \
             patch("curses.color_pair"), \
             patch("curses.napms") as mock_napms:
            rc = run_interactive_picker(inspector, args)

        assert rc == 0
        mock_napms.assert_called_once_with(1500)

    @patch("kport.interactive.diagnose_port")
    def test_diagnosis_failure_displays_error_modal(self, mock_diag):
        pytest.importorskip("_curses")
        inspector = FakeInspector(
            listening=[_binding(8080, pid=123, name="node")]
        )
        args = _args(command="interactive", yes=True)
        mock_stdscr = MagicMock()
        mock_stdscr.getmaxyx.return_value = (24, 80)

        # Keys sequence:
        # 1. ord("d") -> diagnose, which raises ValueError
        # 2. 27 (Esc) -> closes error overlay modal
        # 3. 27 (Esc) -> quits main TUI loop
        keys = [ord("d"), 27, 27]
        mock_stdscr.getch.side_effect = keys

        mock_diag.side_effect = ValueError("Process dead")

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
        assert mock_diag.call_count == 1

    @patch("kport.interactive.diagnose_port")
    def test_diagnosis_scrolling(self, mock_diag):
        pytest.importorskip("_curses")
        import curses
        inspector = FakeInspector(
            listening=[_binding(8080, pid=123, name="node")]
        )
        args = _args(command="interactive", yes=True)
        mock_stdscr = MagicMock()
        mock_stdscr.getmaxyx.return_value = (24, 80)

        # Keys sequence:
        # 1. ord("d") -> opens diagnose overlay
        # 2. curses.KEY_DOWN -> scroll down
        # 3. curses.KEY_UP -> scroll up
        # 4. 27 (Esc) -> closes diagnose overlay
        # 5. 27 (Esc) -> quits main TUI loop
        keys = [ord("d"), curses.KEY_DOWN, curses.KEY_UP, 27, 27]
        mock_stdscr.getch.side_effect = keys

        mock_diag.return_value = {
            "port": 8080,
            "blocked": True,
            "observations": {
                "type": "local",
                "processes": [{"pid": 123, "name": "node", "cmdline": ["node", "app.js"]}],
                "docker_containers": [],
                "bindings": [],
            },
            "inferences": [],
            "risks": [],
            "recommendations": [{"action": "kill", "reason": "standard process", "safe": True}],
        }

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
class TestTUIDiagnoseFallbackMenu:
    """Non-curses fallback menu diagnosis tests."""

    @patch("kport.interactive.diagnose_port")
    def test_fallback_menu_diagnose_flow(self, mock_diag):
        inspector = FakeInspector(
            listening=[_binding(3000, pid=55, name="vite")]
        )
        args = _args(command="interactive", yes=True)

        mock_diag.return_value = {
            "port": 3000,
            "blocked": True,
            "observations": {
                "type": "local",
                "processes": [{"pid": 55, "name": "vite", "cmdline": ["vite"]}],
                "docker_containers": [],
                "bindings": [],
            },
            "inferences": [],
            "risks": [],
            "recommendations": [{"action": "kill", "reason": "vite process", "safe": True}],
        }

        # Sequence of inputs for fallback menu:
        # 1. "d 1" -> runs diagnose and waits for enter
        # 2. "" (Press Enter to continue) -> loops back to menu
        # 3. "q" -> quits fallback menu
        inputs = ["d 1", "", "q"]

        with patch("builtins.input", side_effect=inputs), \
             patch("kport.interactive.list_docker_mappings", return_value=[]):
            rc = _fallback_numbered_menu(inspector, args)

        assert rc == 0
        mock_diag.assert_called_once_with(
            port=3000,
            inspector=inspector,
            proto="tcp",
            config=None,
            bypass_safety=False,
        )
