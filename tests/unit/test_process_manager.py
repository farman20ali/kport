"""
Unit tests for process manager detection (tests/unit/test_process_manager.py).

Covers: systemd cgroup detection, PM2 environment detection,
supervisord output parsing, Windows service detection via tasklist,
and invalid PID handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock, mock_open, patch

import pytest

from kport.process_manager import detect_process_manager


@pytest.mark.unit
class TestProcessManagerDetection:
    """Tests for detect_process_manager() across Linux and Windows managers."""

    def test_invalid_pid_zero_returns_none(self):
        assert detect_process_manager(0) is None

    def test_invalid_pid_negative_returns_none(self):
        assert detect_process_manager(-1) is None

    def test_systemd_service_detected_via_cgroup(self):
        cgroup_data = "0::/system.slice/nginx.service\n"
        m_open = mock_open(read_data=cgroup_data)
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", m_open):
            res = detect_process_manager(1234)
        assert res is not None
        assert res["manager"] == "systemd"
        assert res["name"] == "nginx.service"
        assert res["managed_by"] == "systemd:nginx.service"
        assert "systemctl stop nginx.service" in res["warning"]

    def test_supervisor_service_detected(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "worker_01   RUNNING   pid 4321, uptime 0:05:00\n"
        with patch("shutil.which", return_value="/usr/bin/supervisorctl"), \
             patch("subprocess.run", return_value=mock_proc), \
             patch("kport.process_manager._get_cgroup_systemd_unit", return_value=None):
            res = detect_process_manager(4321)
        assert res is not None
        assert res["manager"] == "supervisor"
        assert res["name"] == "worker_01"
        assert res["managed_by"] == "supervisor:worker_01"

    def test_pm2_app_detected_from_environment(self):
        from kport.process_manager import _detect_pm2_app
        env = {"PM2_HOME": "/home/user/.pm2", "name": "api-server"}
        result = _detect_pm2_app(5678, env)
        assert result == "api-server"


@pytest.mark.unit
class TestWindowsServiceDetection:
    """Windows-specific: tasklist /SVC service name resolution."""

    def test_windows_service_name_returned(self):
        from kport.process_manager import (
            _detect_windows_service as detect_windows_service,
        )
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '"svchost.exe","1234","Dnscache"\n'
        with patch("subprocess.run", return_value=mock_proc):
            result = detect_windows_service(1234)
        assert result == "Dnscache"

    def test_windows_service_name_none_when_not_found(self):
        from kport.process_manager import (
            _detect_windows_service as detect_windows_service,
        )
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '"node.exe","5678","N/A"\n'
        with patch("subprocess.run", return_value=mock_proc):
            result = detect_windows_service(5678)
        assert result is None
