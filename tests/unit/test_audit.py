import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kport import audit


@pytest.fixture
def temp_audit_log(tmp_path):
    """Redirect audit log file and directory to a temporary path during tests."""
    log_dir = tmp_path / ".kport"
    log_file = log_dir / "audit.log"
    with patch("kport.audit._LOG_DIR", log_dir), \
         patch("kport.audit._LOG_FILE", log_file):
        yield log_file


def test_log_kill_port_writes_correct_shape(temp_audit_log):
    """log_kill_port should correctly write a single NDJSON record with correct shape."""
    audit.log_kill_port(
        port=8080,
        pids=[1234, 5678],
        dry_run=False,
        success=True,
        message="Port 8080 successfully freed",
    )

    assert temp_audit_log.exists()
    lines = temp_audit_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["action"] == "kill_port"
    assert record["target"] == {"port": 8080, "pids": [1234, 5678]}
    assert record["dry_run"] is False
    assert record["success"] is True
    assert record["message"] == "Port 8080 successfully freed"
    assert "ts" in record
    assert "version" in record
    assert "user" in record


def test_log_kill_pid_writes_correct_shape(temp_audit_log):
    """log_kill_pid should correctly write a record when a process is killed."""
    audit.log_kill_pid(
        pid=9999,
        process_name="node",
        dry_run=True,
        success=True,
        message="Dry-run: would terminate process",
    )

    assert temp_audit_log.exists()
    lines = temp_audit_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["action"] == "kill_pid"
    assert record["target"] == {"pid": 9999, "name": "node"}
    assert record["dry_run"] is True
    assert record["success"] is True
    assert record["message"] == "Dry-run: would terminate process"
    assert "ts" in record
    assert "version" in record
    assert "user" in record


def test_log_docker_action_writes_correct_shape(temp_audit_log):
    """log_docker_action should correctly log container interactions."""
    audit.log_docker_action(
        container_id="abc123xyz",
        container_name="web-server",
        action="stop",
        dry_run=False,
        success=False,
        message="Failed to stop container: timeout",
    )

    assert temp_audit_log.exists()
    lines = temp_audit_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["action"] == "docker_action"
    assert record["target"] == {
        "container_id": "abc123xyz",
        "container_name": "web-server",
        "docker_action": "stop",
    }
    assert record["dry_run"] is False
    assert record["success"] is False
    assert record["message"] == "Failed to stop container: timeout"


def test_audit_rotation_triggers_on_size(temp_audit_log):
    """Log should rotate from audit.log to audit.log.1 if size threshold is exceeded."""
    # Create the directory first
    temp_audit_log.parent.mkdir(parents=True, exist_ok=True)
    temp_audit_log.write_text("dummy old log content", encoding="utf-8")

    # Mock the file size of the existing log to exceed _MAX_LOG_BYTES (10 MiB)
    original_stat = Path.stat
    def side_effect(self, *args, **kwargs):
        if self.name == "audit.log":
            return MagicMock(st_size=11 * 1024 * 1024)
        return original_stat(self, *args, **kwargs)

    with patch("pathlib.Path.stat", side_effect=side_effect, autospec=True):

        audit.log_kill_port(
            port=80,
            pids=[],
            dry_run=False,
            success=True,
            message="freed",
        )

    # The old log should have been rotated to audit.log.1
    rotated_file = temp_audit_log.with_suffix(".log.1")
    assert rotated_file.exists()
    assert rotated_file.read_text(encoding="utf-8") == "dummy old log content"

    # A new audit.log should have been created with the new record
    assert temp_audit_log.exists()
    lines = temp_audit_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["action"] == "kill_port"


def test_audit_write_failure_is_nonfatal(temp_audit_log):
    """If directory creation or writing fails (e.g. PermissionError), it must fail silently."""
    with patch.object(Path, "mkdir", side_effect=OSError("Read-only file system")):
        # This call should not raise any exceptions
        audit.log_kill_port(
            port=80,
            pids=[],
            dry_run=False,
            success=True,
            message="freed",
        )

    assert not temp_audit_log.exists()


def test_log_service_stop_writes_correct_shape(temp_audit_log):
    """log_service_stop should correctly log managed service stop attempts."""
    audit.log_service_stop(
        port=8080,
        manager="systemd",
        service_name="nginx.service",
        command="systemctl stop nginx.service",
        dry_run=False,
        success=True,
        verified_free=True,
        message="Service stopped and port verified free",
    )

    assert temp_audit_log.exists()
    lines = temp_audit_log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert record["action"] == "stop_service"
    assert record["target"] == {
        "port": 8080,
        "manager": "systemd",
        "service_name": "nginx.service",
        "command": "systemctl stop nginx.service",
        "verified_free": True,
    }
    assert record["dry_run"] is False
    assert record["success"] is True
    assert record["message"] == "Service stopped and port verified free"

