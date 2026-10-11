"""
CLI integration tests for stop-service subcommand.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.cli

from kport.cli import (
    EXIT_GENERAL_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_PERMISSION,
    EXIT_PORT_FREE,
    handle_product_command,
)
from kport.inspectors.base import PortBinding, ProcessInfo
from kport.service_actions import ServiceActionResult
from tests.conftest import FakeInspector, _args


def _binding(port: int, pid: int = 1234, name: str = "node", laddr: str = "127.0.0.1") -> PortBinding:
    return PortBinding(
        port=port,
        family="inet",
        laddr=f"{laddr}:{port}",
        pid=pid,
        process_name=name,
        state="LISTEN",
    )


def test_stop_service_free_port(capsys):
    """If port is free, stop-service should exit with EXIT_PORT_FREE."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=8080)
    
    with patch("kport.cli_commands._diagnose_port_data") as mock_diag:
        mock_diag.return_value = {"blocked": False}
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_PORT_FREE
    out = capsys.readouterr().out
    assert "is already free" in out


def test_stop_service_free_port_json(capsys):
    """If port is free, stop-service in JSON mode should return successful state and EXIT_PORT_FREE."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=8080, json=True)
    
    with patch("kport.cli_commands._diagnose_port_data") as mock_diag:
        mock_diag.return_value = {"blocked": False}
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_PORT_FREE
    out = capsys.readouterr().out
    envelope = json.loads(out)
    assert envelope["command"] == "stop-service"
    assert envelope["data"]["success"] is True
    assert envelope["data"]["verified_free"] is True


def test_stop_service_no_manager_fails(capsys):
    """If port is occupied but no manager is resolved, stop-service should exit with EXIT_INVALID_INPUT."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=8080)
    
    diag_data = {
        "blocked": True,
        "inferences": []
    }
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data):
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_INVALID_INPUT
    err = capsys.readouterr().err
    assert "No supported process manager detected" in err
    assert "kport kill" in err


def test_stop_service_safety_policy_blocks(capsys):
    """If port is protected and bypass is False, stop-service should exit with EXIT_PERMISSION."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=22) # Port 22 is protected by default
    
    diag_data = {
        "blocked": True,
        "inferences": [{"type": "process_manager", "manager": "systemd", "name": "ssh"}]
    }
    
    # safety check blocks
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data):
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_PERMISSION
    err = capsys.readouterr().err
    assert "Security Shield Active" in err


def test_stop_service_cancelled_by_user(capsys):
    """If the user declines the confirmation prompt, stop-service should abort and return EXIT_GENERAL_ERROR."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=8080)
    
    diag_data = {
        "blocked": True,
        "inferences": [{"type": "process_manager", "manager": "systemd", "name": "my-service"}]
    }
    
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data), \
         patch("kport.cli_commands.check_safety_policy") as mock_safety, \
         patch("kport.cli_commands.confirm_prompt", return_value=False) as mock_prompt:
        mock_safety.return_value.allowed = True
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_GENERAL_ERROR
    out = capsys.readouterr().out
    assert "Operation cancelled" in out
    mock_prompt.assert_called_once()


def test_stop_service_dry_run_only_displays(capsys):
    """In dry-run mode, stop-service should display the action without executing and return EXIT_OK."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=8080, dry_run=True)
    
    diag_data = {
        "blocked": True,
        "inferences": [{"type": "process_manager", "manager": "systemd", "name": "my-service"}]
    }
    
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data), \
         patch("kport.cli_commands.check_safety_policy") as mock_safety, \
         patch("kport.cli_commands.stop_service") as mock_stop:
        mock_safety.return_value.allowed = True
        mock_stop.return_value = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="my-service",
            command_executed="systemctl stop my-service",
            message="Dry run command resolution",
            dry_run=True
        )
        
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "[DRY RUN]" in out
    assert "systemctl stop my-service" in out
    mock_stop.assert_called_once_with(
        manager="systemd",
        service_name="my-service",
        timeout=30.0,
        dry_run=True
    )


def test_stop_service_success_and_verified_free(capsys):
    """If stop_service succeeds and the port is verified free, return EXIT_OK."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=8080, yes=True)
    
    diag_data = {
        "blocked": True,
        "inferences": [{"type": "process_manager", "manager": "systemd", "name": "nginx.service"}]
    }
    
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data), \
         patch("kport.cli_commands.check_safety_policy") as mock_safety, \
         patch("kport.cli_commands.stop_service") as mock_stop, \
         patch("kport.cli_commands._poll_until_free", return_value=True) as mock_poll, \
         patch("kport.audit.log_service_stop") as mock_audit:
        mock_safety.return_value.allowed = True
        mock_stop.return_value = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="nginx.service",
            command_executed="systemctl stop nginx.service",
            message="Stopped cleanly"
        )
        
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "nginx.service" in out
    assert "now free" in out
    mock_poll.assert_called_once_with(8080, 3.0, inspector)
    mock_audit.assert_called_once()


def test_stop_service_success_but_still_occupied_without_force(capsys):
    """If stop succeeds but the port is still occupied and force is False, return EXIT_GENERAL_ERROR."""
    inspector = FakeInspector()
    args = _args(command="stop-service", port=8080, yes=True, force=False)
    
    diag_data = {
        "blocked": True,
        "inferences": [{"type": "process_manager", "manager": "systemd", "name": "nginx.service"}]
    }
    
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data), \
         patch("kport.cli_commands.check_safety_policy") as mock_safety, \
         patch("kport.cli_commands.stop_service") as mock_stop, \
         patch("kport.cli_commands._poll_until_free", return_value=False) as mock_poll, \
         patch("kport.audit.log_service_stop") as mock_audit:
        mock_safety.return_value.allowed = True
        mock_stop.return_value = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="nginx.service",
            command_executed="systemctl stop nginx.service",
            message="Stopped cleanly"
        )
        
        rc = handle_product_command(args, inspector)
        
    assert rc == EXIT_GENERAL_ERROR
    err = capsys.readouterr().err
    assert "still blocked" in err
    assert "stop-service with --force" in err
    mock_poll.assert_called_once()
    mock_audit.assert_called_once()


def test_stop_service_escalation_force_success(capsys):
    """If stop fails/still occupied and --force is True, re-inspect, safety check again, kill, and verify."""
    pi = ProcessInfo(pid=5678, name="node")
    inspector = FakeInspector(pids_on_port={8080: [5678]}, process_info={5678: pi})
    args = _args(command="stop-service", port=8080, yes=True, force=True)
    
    diag_data = {
        "blocked": True,
        "inferences": [{"type": "process_manager", "manager": "systemd", "name": "nginx.service"}]
    }
    
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data), \
         patch("kport.cli_commands.check_safety_policy") as mock_safety, \
         patch("kport.cli_commands.stop_service") as mock_stop, \
         patch("kport.cli_commands._poll_until_free") as mock_poll, \
         patch("kport.audit.log_service_stop") as mock_audit_stop, \
         patch("kport.audit.log_kill_port") as mock_audit_kill:
         
        # safety check 1 allows
        mock_safety.side_effect = [
            MagicMock(allowed=True, reason=""),  # First safety check
            MagicMock(allowed=True, reason="")   # Second safety check during escalation
        ]
        
        mock_stop.return_value = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="nginx.service",
            command_executed="systemctl stop nginx.service",
            message="Stopped but port occupied"
        )
        
        # first poll returns False, second poll returns True (after kill)
        mock_poll.side_effect = [False, True]
        
        with patch.object(inspector, "kill_port", return_value=(True, "Killed successfully")) as mock_kill:
            rc = handle_product_command(args, inspector)
            mock_kill.assert_called_once_with(
                8080,
                graceful_timeout=3.0,
                force=True,
                dry_run=False,
                debug=False,
                assume_yes=True
            )
            
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "freed via process kill escalation" in out
    
    mock_audit_stop.assert_called_once()
    mock_audit_kill.assert_called_once()


def test_stop_service_escalation_force_blocked_by_safety(capsys):
    """If stop fails/still occupied and --force is True, but the new PID is protected, escalation must be blocked."""
    pi = ProcessInfo(pid=9999, name="winlogon.exe") # critical process
    inspector = FakeInspector(pids_on_port={8080: [9999]}, process_info={9999: pi})
    args = _args(command="stop-service", port=8080, yes=True, force=True)
    
    diag_data = {
        "blocked": True,
        "inferences": [{"type": "process_manager", "manager": "systemd", "name": "nginx.service"}]
    }
    
    with patch("kport.cli_commands._diagnose_port_data", return_value=diag_data), \
         patch("kport.cli_commands.check_safety_policy") as mock_safety, \
         patch("kport.cli_commands.stop_service") as mock_stop, \
         patch("kport.cli_commands._poll_until_free") as mock_poll:
         
        # safety check 1 allows, safety check 2 blocks
        mock_safety.side_effect = [
            MagicMock(allowed=True, reason=""),  # First safety check
            MagicMock(allowed=False, reason="Critical process protected")   # Second safety check blocks
        ]
        
        mock_stop.return_value = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="nginx.service",
            command_executed="systemctl stop nginx.service",
            message="Stopped but port occupied"
        )
        
        mock_poll.return_value = False
        
        with patch.object(inspector, "kill_port") as mock_kill:
            rc = handle_product_command(args, inspector)
            mock_kill.assert_not_called()
            
    assert rc == EXIT_GENERAL_ERROR
    err = capsys.readouterr().err
    assert "Escalation blocked by safety" in err
    assert "Critical process protected" in err
