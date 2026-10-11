"""
service_actions.py — Execute managed service stop operations for kport.

Provides safe execution of stop commands for processes managed by:
  - systemd (Linux)
  - PM2 (cross-platform Node.js manager)
  - supervisord (Linux/macOS)
  - Windows Services (Windows)

Design constraints:
  - Never raises: all failures return a ServiceActionResult
  - Requires explicit manager and service_name (resolved by orchestration)
  - No direct terminal output (caller applies presentation)
  - Bounded subprocess timeout (default: 30s)
  - Graceful-first for all managers
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class ServiceActionResult:
    """Outcome of a managed service stop action."""
    success: bool
    manager: str           # "systemd" | "pm2" | "supervisor" | "windows-service" | "none"
    service_name: str      # e.g., "nginx.service", "api-server", "Dnscache"
    command_executed: str  # actual system command run
    message: str           # outcome explanation
    dry_run: bool = False


def stop_service(
    manager: str,
    service_name: str,
    *,
    timeout: float = 30.0,
    dry_run: bool = False,
) -> ServiceActionResult:
    """
    Executes the manager-specific stop command.

    This function is purely logic-driven and does not check safety or verify
    whether the port went free (that is orchestration responsibility).
    """
    if manager == "none" or not manager:
        return ServiceActionResult(
            success=False,
            manager="none",
            service_name=service_name,
            command_executed="",
            message="No supported service manager detected.",
            dry_run=dry_run,
        )

    # 1. Map manager to shell command
    cmd_args: list[str] = []
    if manager == "systemd":
        binary = "systemctl"
        cmd_args = [binary, "stop", service_name]
    elif manager == "pm2":
        binary = "pm2"
        cmd_args = [binary, "stop", service_name]
    elif manager == "supervisor":
        binary = "supervisorctl"
        cmd_args = [binary, "stop", service_name]
    elif manager == "windows-service":
        binary = "powershell.exe"
        services = [s.strip() for s in service_name.split(",") if s.strip()]
        if not services:
            return ServiceActionResult(
                success=False,
                manager=manager,
                service_name=service_name,
                command_executed="",
                message="No valid Windows Service names resolved.",
                dry_run=dry_run,
            )
        # Build combined PowerShell Stop-Service string
        ps_cmd = " ; ".join(f"Stop-Service -Name {s}" for s in services)
        cmd_args = [binary, "-NoProfile", "-Command", ps_cmd]
    else:
        return ServiceActionResult(
            success=False,
            manager=manager,
            service_name=service_name,
            command_executed="",
            message=f"Unsupported service manager: {manager}",
            dry_run=dry_run,
        )

    command_str = " ".join(cmd_args)

    # 2. Check dry-run
    if dry_run:
        return ServiceActionResult(
            success=True,
            manager=manager,
            service_name=service_name,
            command_executed=command_str,
            message="Dry run: service stop command would be executed.",
            dry_run=True,
        )

    # 3. Check if execution binary exists (for systemctl, pm2, supervisorctl)
    # Note: on Windows, powershell.exe is checked via which, fallback to generic error.
    exec_check = cmd_args[0]
    if not shutil.which(exec_check):
        return ServiceActionResult(
            success=False,
            manager=manager,
            service_name=service_name,
            command_executed=command_str,
            message=f"Command executable '{exec_check}' not found on PATH.",
            dry_run=False,
        )

    # 4. Execute the command
    try:
        res = subprocess.run(
            cmd_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0:
            return ServiceActionResult(
                success=True,
                manager=manager,
                service_name=service_name,
                command_executed=command_str,
                message=f"Service '{service_name}' stopped successfully.",
                dry_run=False,
            )
        else:
            err_msg = (res.stderr or res.stdout or "").strip()
            return ServiceActionResult(
                success=False,
                manager=manager,
                service_name=service_name,
                command_executed=command_str,
                message=f"Stop command failed (code {res.returncode}): {err_msg}",
                dry_run=False,
            )
    except subprocess.TimeoutExpired:
        return ServiceActionResult(
            success=False,
            manager=manager,
            service_name=service_name,
            command_executed=command_str,
            message=f"Stop command timed out after {timeout} seconds.",
            dry_run=False,
        )
    except OSError as e:
        return ServiceActionResult(
            success=False,
            manager=manager,
            service_name=service_name,
            command_executed=command_str,
            message=f"Subprocess execution failed: {e}",
            dry_run=False,
        )
