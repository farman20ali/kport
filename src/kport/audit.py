"""
audit.py -- Local audit log for destructive kport operations.

All kill actions (port kill, process kill, docker actions) are appended to
``~/.kport/audit.log`` in NDJSON format so operators can trace what was killed
when and by whom.

Each log line is a JSON object with keys:
  ts          ISO-8601 UTC timestamp
  version     kport version string
  user        OS username (best-effort, falls back to UID/None)
  action      "kill_port" | "kill_pid" | "docker_action"
  target      {"port": 3000} | {"pid": 1234} | {"container_id": "abc...", "action": "stop"}
  dry_run     bool
  success     bool
  message     human-readable result from the inspector/docker layer

Format is intentionally stable; do not remove fields (add new ones instead).
"""

from __future__ import annotations

import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__

_LOG_DIR = Path.home() / ".kport"
_LOG_FILE = _LOG_DIR / "audit.log"

# Maximum audit log size before rotation (10 MiB)
_MAX_LOG_BYTES = 10 * 1024 * 1024


def _get_user() -> str:
    """Return the current OS username, falling back gracefully."""
    try:
        return getpass.getuser()
    except (ImportError, KeyError, OSError):
        try:
            uid = os.getuid()  # type: ignore[attr-defined]  # Unix only
            return f"uid:{uid}"
        except AttributeError:
            return "unknown"


def _rotate_if_needed() -> None:
    """Rotate audit.log -> audit.log.1 if it exceeds _MAX_LOG_BYTES."""
    try:
        if _LOG_FILE.exists() and _LOG_FILE.stat().st_size >= _MAX_LOG_BYTES:
            rotated = _LOG_FILE.with_suffix(".log.1")
            _LOG_FILE.rename(rotated)
    except OSError:
        pass  # Rotation failure is non-fatal


def _write(record: dict[str, Any]) -> None:
    """Append a single JSON record to the audit log. Non-fatal on error."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        line = json.dumps(record, default=str, separators=(",", ":"))
        with open(_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # Audit failure must NEVER crash the main operation.
        pass


def _base(dry_run: bool, success: bool, message: str) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "version": __version__,
        "user": _get_user(),
        "dry_run": dry_run,
        "success": success,
        "message": message,
    }


# ---------------------------------------------------------------------------
# Public audit helpers — called from cli.py / inspector
# ---------------------------------------------------------------------------


def log_kill_port(
    port: int,
    pids: list[int],
    *,
    dry_run: bool,
    success: bool,
    message: str,
) -> None:
    """Record a port-kill attempt."""
    record = _base(dry_run=dry_run, success=success, message=message)
    record["action"] = "kill_port"
    record["target"] = {"port": port, "pids": pids}
    _write(record)


def log_kill_pid(
    pid: int,
    process_name: str | None,
    *,
    dry_run: bool,
    success: bool,
    message: str,
) -> None:
    """Record a single PID kill attempt."""
    record = _base(dry_run=dry_run, success=success, message=message)
    record["action"] = "kill_pid"
    record["target"] = {"pid": pid, "name": process_name}
    _write(record)


def log_docker_action(
    container_id: str,
    container_name: str,
    action: str,
    *,
    dry_run: bool,
    success: bool,
    message: str,
) -> None:
    """Record a Docker container action (stop / restart / rm)."""
    record = _base(dry_run=dry_run, success=success, message=message)
    record["action"] = "docker_action"
    record["target"] = {
        "container_id": container_id,
        "container_name": container_name,
        "docker_action": action,
    }
    _write(record)


def log_service_stop(
    port: int,
    manager: str,
    service_name: str,
    command: str,
    *,
    dry_run: bool,
    success: bool,
    verified_free: bool,
    message: str,
) -> None:
    """Record a managed service stop attempt."""
    record = _base(dry_run=dry_run, success=success, message=message)
    record["action"] = "stop_service"
    record["target"] = {
        "port": port,
        "manager": manager,
        "service_name": service_name,
        "command": command,
        "verified_free": verified_free,
    }
    _write(record)


def read_recent_audit_events(limit: int = 20) -> list[dict[str, Any]]:
    """Return up to limit most recent audit entries from ~/.kport/audit.log (newest first)."""
    if not _LOG_FILE.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        with open(_LOG_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries[-limit:][::-1]

