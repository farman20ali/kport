"""
MCP PID tool round-trip tests (tests/mcp/test_mcp_pid_tools.py).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from kport.inspectors.base import ProcessInfo
from kport.mcp_server import handle_inspect_pid, handle_kill_pid, handle_kill_process
from tests.conftest import FakeInspector, _binding


@pytest.mark.mcp
def test_handle_inspect_pid_success():
    pi = ProcessInfo(pid=8888, name="mcp-test", cmdline=["mcp-test"], user="mcpuser", ppid=1, cwd="/app")
    binding = _binding(9999, pid=8888, name="mcp-test", proto="tcp", laddr="127.0.0.1:9999")
    inspector = FakeInspector(
        process_info={8888: pi},
        listening=[binding]
    )

    res = handle_inspect_pid(inspector, 8888)
    assert res["pid"] == 8888
    assert res["process"]["name"] == "mcp-test"
    assert len(res["active_ports"]) == 1
    assert res["active_ports"][0]["port"] == 9999


@pytest.mark.mcp
def test_handle_inspect_pid_missing_raises():
    inspector = FakeInspector()
    with pytest.raises(ValueError, match="not found"):
        handle_inspect_pid(inspector, 8888)


@pytest.mark.mcp
def test_handle_kill_pid_success():
    pi = ProcessInfo(pid=8888, name="mcp-test")
    inspector = FakeInspector(process_info={8888: pi})

    with patch.object(inspector, "kill_pid", return_value=(True, "Terminated")):
        res = handle_kill_pid(inspector, 8888)
        assert res["success"] is True
        assert res["pid"] == 8888


@pytest.mark.mcp
def test_handle_kill_process_success():
    pi1 = ProcessInfo(pid=8881, name="python")
    pi2 = ProcessInfo(pid=8882, name="python")
    inspector = FakeInspector(
        process_info={8881: pi1, 8882: pi2}
    )

    with patch.object(inspector, "find_pids_by_name", return_value=[8881, 8882]), \
         patch.object(inspector, "kill_pid", return_value=(True, "Terminated")) as mock_kill:
        res = handle_kill_process(inspector, "python", exact=True)
        assert res["success"] is True
        assert len(res["killed"]) == 2
        assert mock_kill.call_count == 2
