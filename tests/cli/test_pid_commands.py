"""
CLI process and PID command tests (tests/cli/test_pid_commands.py).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from kport.cli import EXIT_INVALID_INPUT, EXIT_OK, EXIT_PERMISSION, handle_legacy_command, handle_product_command
from kport.inspectors.base import ProcessInfo
from tests.conftest import FakeInspector, _args, _binding


@pytest.mark.cli
def test_inspect_pid_returns_details_and_ports(capsys):
    pi = ProcessInfo(pid=9999, name="my-daemon", cmdline=["my-daemon", "--run"], user="testuser", ppid=1, cwd="/app")
    binding = _binding(8082, pid=9999, name="my-daemon", proto="tcp", laddr="127.0.0.1:8082")
    inspector = FakeInspector(
        process_info={9999: pi},
        listening=[binding]
    )

    args = _args(command="inspect", pid=9999, json=True)
    rc = handle_product_command(args, inspector)
    assert rc == EXIT_OK

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["schema_version"] == 1
    data = envelope["data"]
    assert data["pid"] == 9999
    assert data["process"]["name"] == "my-daemon"
    assert len(data["active_ports"]) == 1
    assert data["active_ports"][0]["port"] == 8082


@pytest.mark.cli
def test_inspect_pid_missing_returns_invalid_input(capsys):
    inspector = FakeInspector()
    args = _args(command="inspect", pid=9999, json=True)
    rc = handle_product_command(args, inspector)
    assert rc == EXIT_INVALID_INPUT


@pytest.mark.cli
def test_inspect_pid_legacy_route(capsys):
    pi = ProcessInfo(pid=9999, name="my-daemon", cmdline=["my-daemon", "--run"], user="testuser", ppid=1, cwd="/app")
    inspector = FakeInspector(
        process_info={9999: pi},
        listening=[]
    )
    args = _args(pid=9999, json=True)
    rc = handle_legacy_command(args, inspector)
    assert rc == EXIT_OK


@pytest.mark.cli
def test_kill_pid_success(capsys):
    pi = ProcessInfo(pid=9999, name="my-daemon")
    inspector = FakeInspector(process_info={9999: pi})
    args = _args(command="kill", pid=9999, json=True, yes=True)

    with patch.object(inspector, "kill_pid", return_value=(True, "Terminated")) as mock_kill:
        rc = handle_product_command(args, inspector)
        assert rc == EXIT_OK
        mock_kill.assert_called_once()


@pytest.mark.cli
def test_kill_pid_protected_raises_permission(capsys):
    pi = ProcessInfo(pid=9999, name="systemd")
    inspector = FakeInspector(process_info={9999: pi})
    args = _args(command="kill", pid=9999, json=True, yes=True)

    with patch("kport.cli_commands.check_safety_policy", return_value=(False, "Protected")):
        rc = handle_product_command(args, inspector)
        assert rc == EXIT_PERMISSION


@pytest.mark.cli
@patch("sys.stdin.isatty", return_value=True)
@patch("sys.stdout.isatty", return_value=True)
@patch("builtins.input", return_value="1")
def test_kill_process_interactive_selection(mock_input, mock_stdout_tty, mock_stdin_tty, capsys):
    pi1 = ProcessInfo(pid=1001, name="python")
    pi2 = ProcessInfo(pid=1002, name="python")
    inspector = FakeInspector(
        process_info={1001: pi1, 1002: pi2},
        listening=[]
    )
    # Mock find_pids_by_name to return both PIDs
    with patch.object(inspector, "find_pids_by_name", return_value=[1001, 1002]):
        args = _args(command="kill-process", name="python", exact=True, json=False, yes=False)
        with patch.object(inspector, "kill_pid", return_value=(True, "Terminated")) as mock_kill:
            rc = handle_product_command(args, inspector)
            assert rc == EXIT_OK
            mock_kill.assert_called_once()
            # It should only kill PID 1001 (index 1 selected in mocked input)
            assert mock_kill.call_args[0][0] == 1001


@pytest.mark.cli
def test_kill_pid_legacy_flag_route(capsys):
    """Verify that 'kport -k --pid <PID>' routes to handle_kill_pid_cli."""
    pi = ProcessInfo(pid=9999, name="my-daemon")
    inspector = FakeInspector(process_info={9999: pi})
    args = _args(kill=-1, pid=9999, json=True, yes=True)

    with patch.object(inspector, "kill_pid", return_value=(True, "Terminated")) as mock_kill:
        rc = handle_legacy_command(args, inspector)
        assert rc == EXIT_OK
        mock_kill.assert_called_once()
        assert mock_kill.call_args[0][0] == 9999


@pytest.mark.cli
def test_kill_pid_legacy_standalone_flag_without_args_fails(capsys):
    """Verify standalone -k without port or PID returns EXIT_INVALID_INPUT."""
    inspector = FakeInspector()
    args = _args(kill=-1, pid=None)
    rc = handle_legacy_command(args, inspector)
    assert rc == EXIT_INVALID_INPUT
    assert "requires a PORT, or specify a PID" in capsys.readouterr().err


@pytest.mark.cli
def test_inspect_pid_legacy_standalone_flag_without_args_fails(capsys):
    """Verify standalone -i without port or PID returns EXIT_INVALID_INPUT."""
    inspector = FakeInspector()
    args = _args(inspect=-1, pid=None)
    rc = handle_legacy_command(args, inspector)
    assert rc == EXIT_INVALID_INPUT
    assert "requires a PORT, or specify a PID" in capsys.readouterr().err


@pytest.mark.cli
def test_main_cli_kport_dash_k_pid_integration(capsys):
    """Integration test: main(['-k', '--pid', '9999', '-y']) calls kill on PID 9999."""
    from kport.cli import main

    pi = ProcessInfo(pid=9999, name="my-daemon")
    fake = FakeInspector(process_info={9999: pi})
    with patch("kport.cli.get_inspector", return_value=fake), \
         patch.object(fake, "kill_pid", return_value=(True, "Terminated")) as mock_kill:
        rc = main(["-k", "--pid", "9999", "-y"])
        assert rc == EXIT_OK
        mock_kill.assert_called_once()
        assert mock_kill.call_args[0][0] == 9999

