"""
Unit tests for service-aware supervisor suppression, atomic port swap,
and SIEM audit telemetry.
"""
from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

from kport.audit import export_audit_events, format_siem_cef
from kport.cli_commands import handle_audit, handle_swap
from kport.cli_utils import EXIT_OK, EXIT_PERMISSION
from kport.inspectors.base import BaseInspector, PortBinding, ProcessInfo
from kport.service_actions import ServiceActionResult


def _mock_binding(port: int = 8080, pid: int = 42, proto: str = "tcp") -> PortBinding:
    return PortBinding(
        port=port,
        proto=proto,
        state="LISTEN",
        pid=pid,
        process_name="node",
        laddr=f"0.0.0.0:{port}",
        family="IPv4",
    )


class TestServiceAwareKill:
    @patch("kport.cli_commands.detect_process_manager")
    @patch("kport.cli_commands.stop_service")
    @patch("kport.cli_commands._poll_until_free", return_value=True)
    @patch("kport.cli_commands.check_safety_policy")
    def test_service_aware_stops_pm_before_raw_kill(
        self, mock_safety, mock_poll, mock_stop, mock_detect
    ):
        mock_safety.return_value = (True, "Allowed")
        mock_detect.return_value = {
            "manager": "systemd",
            "name": "api.service",
            "managed_by": "systemd (api.service)",
        }
        mock_stop.return_value = ServiceActionResult(
            success=True,
            manager="systemd",
            service_name="api.service",
            command_executed="systemctl stop api.service",
            message="Stopped api.service",
        )

        mock_inspector = MagicMock(spec=BaseInspector)
        mock_inspector.find_pids_on_port.return_value = [100]
        mock_inspector.find_bindings_on_port.return_value = [_mock_binding(port=8080, pid=100)]
        mock_inspector.get_process_info.return_value = ProcessInfo(pid=100, name="node")

        from kport.cli_commands import handle_product_command

        args = argparse.Namespace(
            command="kill",
            port=8080,
            ports=None,
            profile=None,
            proto="both",
            service_aware=True,
            graceful_timeout=3.0,
            force=False,
            dry_run=False,
            json=True,
            yes=True,
            debug=False,
        )

        with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]):
            rc = handle_product_command(args, mock_inspector)

        assert rc == EXIT_OK
        mock_stop.assert_called_once_with(
            manager="systemd",
            service_name="api.service",
            timeout=3.0,
            dry_run=False,
        )
        # Raw kill was NOT invoked because service stop succeeded
        mock_inspector.kill_port.assert_not_called()


class TestAtomicPortSwap:
    def test_swap_dry_run(self):
        mock_inspector = MagicMock(spec=BaseInspector)
        mock_inspector.kill_port.return_value = (True, "Freed")
        mock_inspector.find_pids_on_port.return_value = [50]
        mock_inspector.find_bindings_on_port.return_value = [_mock_binding(port=3000, pid=50)]

        args = argparse.Namespace(
            command="swap",
            port=3000,
            proto="tcp",
            dry_run=True,
            json=True,
            yes=True,
            force=False,
        )

        with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.cli_commands.check_safety_policy", return_value=(True, "Allowed")):
            rc = handle_swap(args, mock_inspector)

        assert rc == EXIT_OK

    def test_swap_safety_blocked(self):
        mock_inspector = MagicMock(spec=BaseInspector)
        mock_inspector.find_pids_on_port.return_value = [1]
        mock_inspector.find_bindings_on_port.return_value = [_mock_binding(port=22, pid=1)]

        args = argparse.Namespace(
            command="swap",
            port=22,
            proto="tcp",
            dry_run=False,
            json=True,
            yes=True,
            force=False,
        )

        with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.cli_commands.check_safety_policy", return_value=(False, "Port is protected")):
            rc = handle_swap(args, mock_inspector)

        assert rc == EXIT_PERMISSION

    @patch("subprocess.call", return_value=0)
    @patch("kport.cli_commands._poll_until_free", return_value=True)
    def test_swap_handover_command(self, mock_poll, mock_call):
        mock_inspector = MagicMock(spec=BaseInspector)
        mock_inspector.find_pids_on_port.return_value = []
        mock_inspector.find_bindings_on_port.return_value = []

        args = argparse.Namespace(
            command="swap",
            port=9999,
            proto="tcp",
            exec_cmd="python -m http.server 9999",
            dry_run=False,
            json=True,
            yes=True,
            force=False,
        )

        with patch("kport.cli_commands.docker_mappings_for_host_port", return_value=[]), \
             patch("kport.cli_commands.check_safety_policy", return_value=(True, "Allowed")):
            rc = handle_swap(args, mock_inspector)

        assert rc == 0
        mock_call.assert_called_once_with("python -m http.server 9999", shell=True)


class TestAuditTelemetry:
    def test_siem_cef_formatting(self):
        entry = {
            "ts": "2026-10-25T11:00:00Z",
            "version": "5.4.0",
            "user": "developer",
            "action": "kill_port",
            "target": {"port": 8080, "pids": [1234]},
            "dry_run": False,
            "success": True,
            "message": "Port freed successfully",
        }
        cef_line = format_siem_cef(entry)
        assert cef_line.startswith("CEF:0|kport|kport|")
        assert "kill_port" in cef_line
        assert "suser=developer" in cef_line
        assert "outcome=success" in cef_line
        assert "cs1={\"port\":8080,\"pids\":[1234]}" in cef_line

    @patch("kport.audit.read_recent_audit_events")
    def test_export_audit_events_formats(self, mock_read):
        entry = {
            "ts": "2026-10-25T11:00:00Z",
            "version": "5.4.0",
            "user": "admin",
            "action": "stop_service",
            "target": {"port": 80},
            "dry_run": False,
            "success": True,
            "message": "Stopped",
        }
        mock_read.return_value = [entry]

        # JSON format
        json_out = export_audit_events(format="json", limit=10)
        assert "\"action\": \"stop_service\"" in json_out

        # NDJSON format
        ndjson_out = export_audit_events(format="ndjson", limit=10)
        assert ndjson_out.startswith("{\"ts\":")

        # SIEM format
        siem_out = export_audit_events(format="siem", limit=10)
        assert siem_out.startswith("CEF:0|kport|kport|")

    @patch("kport.audit.read_recent_audit_events")
    def test_cli_handle_audit_export(self, mock_read, tmp_path):
        mock_read.return_value = [{
            "ts": "2026-10-25T11:00:00Z",
            "version": "5.4.0",
            "user": "ci-runner",
            "action": "kill_port",
            "target": {"port": 5000},
            "dry_run": False,
            "success": True,
            "message": "Freed",
        }]

        out_file = tmp_path / "ci_audit.log"
        args = argparse.Namespace(
            command="audit",
            limit=50,
            format="siem",
            export=str(out_file),
            siem=True,
            json=False,
        )

        rc = handle_audit(args)
        assert rc == EXIT_OK
        assert out_file.exists()
        content = out_file.read_text(encoding="utf-8")
        assert "CEF:0|kport|kport|" in content
