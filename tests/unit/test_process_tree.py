"""
Unit tests for process tree traversal and termination (tests/unit/test_process_tree.py).

Tests get_child_pids() (Linux ps-based and psutil) and kill_process_tree()
depth-first ordering.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kport.inspectors.psutil_impl import PsutilInspector
from kport.inspectors.system_impl import FallbackInspector


@pytest.mark.unit
class TestPsutilChildPids:
    """PsutilInspector.get_child_pids traversal."""

    def test_returns_direct_and_recursive_children(self):
        child1 = MagicMock()
        child1.pid = 5678
        child2 = MagicMock()
        child2.pid = 9012
        mock_process = MagicMock()
        mock_process.children.return_value = [child1, child2]
        with patch("psutil.Process", return_value=mock_process):
            inspector = PsutilInspector()
            children = inspector.get_child_pids(1234)
        assert children == [5678, 9012]
        mock_process.children.assert_called_once_with(recursive=True)


@pytest.mark.unit
class TestFallbackChildPids:
    """FallbackInspector.get_child_pids via Linux ps output."""

    def test_recursive_child_pid_resolution_from_ps(self):
        inspector = FallbackInspector()
        inspector.system = "Linux"
        mock_ps_output = " PPID   PID\n    1   100\n  100   101\n  101   102\n  100   103\n    2   200\n"

        class FakeProc:
            returncode = 0
            stdout = mock_ps_output
            stderr = ""

        with patch.object(inspector, "_run_subprocess", return_value=FakeProc()):
            children = inspector.get_child_pids(100)
        assert sorted(children) == [101, 102, 103]


@pytest.mark.unit
class TestKillProcessTree:
    """kill_process_tree() depth-first ordering."""

    def test_children_killed_before_parent_depth_first(self):
        inspector = FallbackInspector()
        killed_pids = []

        def mock_kill_pid(pid, **kwargs):
            killed_pids.append(pid)
            return True, "killed"

        with patch.object(inspector, "get_child_pids", return_value=[101, 102]), \
             patch.object(inspector, "kill_pid", side_effect=mock_kill_pid):
            ok, _ = inspector.kill_process_tree(100)
        assert ok is True
        # Depth-first: children first, parent last
        assert killed_pids == [101, 102, 100]
