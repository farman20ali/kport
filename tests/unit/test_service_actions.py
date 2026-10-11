"""
Unit tests for service stop actions (tests/unit/test_service_actions.py).
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from kport.service_actions import stop_service


@pytest.mark.unit
class TestServiceActions:
    """Tests stop_service logic, mock commands execution, timeout, and dry runs."""

    def test_stop_service_none_manager_returns_failure(self):
        res = stop_service("none", "my-service")
        assert not res.success
        assert res.manager == "none"
        assert res.command_executed == ""

    def test_stop_service_invalid_manager_returns_failure(self):
        res = stop_service("kubernetes", "my-service")
        assert not res.success
        assert res.command_executed == ""

    def test_stop_service_dry_run_does_not_execute_command(self):
        with patch("shutil.which") as mock_which:
            res = stop_service("systemd", "nginx.service", dry_run=True)
            assert res.success
            assert res.dry_run
            assert "systemctl stop nginx.service" in res.command_executed
            mock_which.assert_not_called()

    def test_stop_service_binary_not_found_on_path(self):
        with patch("shutil.which", return_value=None):
            res = stop_service("systemd", "nginx.service")
            assert not res.success
            assert "not found on PATH" in res.message

    def test_stop_service_systemd_success(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/systemctl"), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = stop_service("systemd", "nginx.service")
            assert res.success
            assert not res.dry_run
            assert res.manager == "systemd"
            mock_run.assert_called_once_with(
                ["systemctl", "stop", "nginx.service"],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )

    def test_stop_service_pm2_success(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/pm2"), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = stop_service("pm2", "app-name")
            assert res.success
            assert res.manager == "pm2"
            mock_run.assert_called_once_with(
                ["pm2", "stop", "app-name"],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )

    def test_stop_service_supervisor_success(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch("shutil.which", return_value="/usr/bin/supervisorctl"), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = stop_service("supervisor", "worker-proc")
            assert res.success
            assert res.manager == "supervisor"
            mock_run.assert_called_once_with(
                ["supervisorctl", "stop", "worker-proc"],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )

    def test_stop_service_windows_service_multiple_names_success(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0

        with patch("shutil.which", return_value="powershell.exe"), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            res = stop_service("windows-service", "Dnscache, Spooler")
            assert res.success
            assert res.manager == "windows-service"
            mock_run.assert_called_once_with(
                ["powershell.exe", "-NoProfile", "-Command", "Stop-Service -Name Dnscache ; Stop-Service -Name Spooler"],
                capture_output=True,
                text=True,
                timeout=30.0,
                check=False,
            )

    def test_stop_service_windows_service_no_names_failure(self):
        res = stop_service("windows-service", "  ,  ")
        assert not res.success
        assert "No valid Windows Service names" in res.message

    def test_stop_service_command_failure_returns_explanation(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 5
        mock_proc.stderr = "Access Denied"
        mock_proc.stdout = ""

        with patch("shutil.which", return_value="/usr/bin/systemctl"), \
             patch("subprocess.run", return_value=mock_proc):
            res = stop_service("systemd", "nginx.service")
            assert not res.success
            assert "Stop command failed (code 5)" in res.message
            assert "Access Denied" in res.message

    def test_stop_service_command_timeout_returns_failure(self):
        with patch("shutil.which", return_value="/usr/bin/systemctl"), \
             patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["systemctl"], 30.0)):
            res = stop_service("systemd", "nginx.service")
            assert not res.success
            assert "timed out after 30.0 seconds" in res.message

    def test_stop_service_oserror_returns_failure(self):
        with patch("shutil.which", return_value="/usr/bin/systemctl"), \
             patch("subprocess.run", side_effect=OSError("Exec format error")):
            res = stop_service("systemd", "nginx.service")
            assert not res.success
            assert "Subprocess execution failed" in res.message
