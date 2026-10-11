"""
cli_utils.py — CLI-specific shared utilities for kport.

Contains helpers used by both cli.py and cli_commands.py.

Architectural constraints:
  - cli_utils.py may import from domain modules.
  - Domain modules MUST NOT import from cli_utils.py, cli_commands.py,
    or cli.py.
  - cli_commands.py MAY import from cli_utils.py.
  - cli.py MAY import from cli_utils.py.
  - cli_utils.py MUST NOT import from cli_commands.py or cli.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .exceptions import InvalidPortError, KPortError
from .formatter import Colors, colorize
from .inspectors import BaseInspector
from .profile import load_profiles, resolve_profile
from .safety import (
    SafetyDecision,
)
from .safety import (
    check_safety_policy as _core_check_safety_policy,
)

# ---------------------------------------------------------------------------
# Exit codes — defined here so cli_commands.py can import them without
# creating a circular dependency on cli.py.
# cli.py re-exports these symbols so that `from kport.cli import EXIT_OK`
# continues to work for existing consumers and tests.
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_GENERAL_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_PERMISSION = 3
EXIT_PORT_DOCKER = 4
EXIT_PORT_FREE = 5

# JSON schema version for --json output envelope
JSON_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _json_out(command: str, data: dict) -> str:
    """Return a stable, versioned JSON envelope for --json output.

    Schema:  {"schema_version": 1, "command": "<subcommand>", "data": {...}}
    All --json outputs pass through here so downstream scripts can key on
    schema_version to detect breaking changes.
    """
    return json.dumps(
        {"schema_version": JSON_SCHEMA_VERSION, "command": command, "data": data},
        indent=2,
    )


def debug_log(enabled: bool, msg: str) -> None:
    if enabled:
        print(colorize(f"[debug] {msg}", Colors.BLUE), file=sys.stderr)


# ---------------------------------------------------------------------------
# Privilege detection & self-escalation
# ---------------------------------------------------------------------------


def _is_elevated() -> bool:
    """Detect if the current process is running with root/admin privileges."""
    if sys.platform == "win32":
        try:
            import ctypes

            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except (AttributeError, OSError):
            return False
    return (os.geteuid() == 0) if hasattr(os, "geteuid") else False


def warn_if_path_missing() -> None:
    """Warn once (to stderr) if ~/.local/bin is not on PATH.

    Only runs on Linux/macOS — Windows uses a different install layout and
    the check is not relevant there.  The warning is skipped when stdout/stderr
    are not a TTY (e.g. piped, scripted, or --json mode) so it never pollutes
    machine-readable output.
    """
    if sys.platform == "win32":
        return
    if not sys.stderr.isatty():
        return

    local_bin = os.path.expanduser("~/.local/bin")
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    if local_bin not in path_dirs:
        print(
            "\n\033[33m⚠️  ~/.local/bin is not on your PATH — 'kport' may not be "
            "found in new shells.\033[0m\n"
            "   Add it permanently with one of:\n"
            "     echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc && source ~/.bashrc\n"
            "     echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.zshrc  && source ~/.zshrc\n",
            file=sys.stderr,
        )


def get_elevation_hint() -> str:
    """Return a one-line actionable hint when a permission error occurs.

    Detects whether kport was installed via pip/pipx (i.e. not in a standard
    system PATH location) and tailors the advice accordingly.
    """
    import shutil

    exe = sys.executable  # e.g. /home/user/.venv/bin/python
    sudo = shutil.which("sudo")

    if sys.platform == "win32":
        return (
            "Run your terminal as Administrator: "
            "right-click the terminal icon → 'Run as administrator'."
        )

    if not sudo:
        return (
            "'sudo' not found in PATH.\n"
            "  • Run:  kport setup-sudo   — installs a /usr/local/bin/kport wrapper.\n"
            f"  • Or:   sudo -E env PATH=\"$PATH\" {exe} -m kport ..."
        )

    # Check if kport entry point is outside standard system paths
    kport_bin = shutil.which("kport")
    system_prefixes = ("/usr/bin", "/usr/local/bin", "/usr/sbin", "/bin", "/sbin")
    is_userland = kport_bin and not any(
        kport_bin.startswith(p) for p in system_prefixes
    )

    if is_userland:
        return (
            f"'kport' is installed in a user/virtual-env path ({kport_bin})\n"
            "  which sudo's secure_path does not include.  Options:\n"
            f"  • Run:  kport setup-sudo   — creates /usr/local/bin/kport (one-time).\n"
            f"  • Or:   sudo -E env PATH=\"$PATH\" {exe} -m kport ..."
        )

    return (
        "Insufficient privileges. Re-run with:\n"
        f"  sudo {' '.join(sys.argv)}"
    )


def re_exec_with_sudo(argv: list[str] | None = None, *, assume_yes: bool = False) -> int:
    """Re-execute the current kport command with elevated privileges.

    Preserves the active Python interpreter and virtualenv so the same
    kport installation runs under sudo, bypassing secure_path restrictions.

    Strategy:
        sudo -E env PATH="$PATH" <sys.executable> -m kport <original-args>

    Args:
        argv: Argument list to pass (defaults to sys.argv[1:]).
        assume_yes: If True, skip interactive confirmation prompt.

    Returns:
        The exit code of the elevated subprocess, or EXIT_PERMISSION if
        the user declines or sudo is unavailable.
    """
    import shutil
    import subprocess

    sudo = shutil.which("sudo")
    if not sudo:
        print(
            colorize(
                "⚠  Cannot escalate: 'sudo' not found in PATH.\n"
                "   Run:  kport setup-sudo   to install a system-wide launcher.",
                Colors.YELLOW,
            ),
            file=sys.stderr,
        )
        return EXIT_PERMISSION

    if not assume_yes:
        try:
            resp = input(
                colorize(
                    "\n🔐 Root privileges required. Re-run with sudo? [y/N]: ",
                    Colors.YELLOW,
                )
            ).strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            return EXIT_GENERAL_ERROR
        if resp not in ("y", "yes"):
            print(colorize("Escalation declined.", Colors.YELLOW))
            return EXIT_PERMISSION

    args_to_pass = argv if argv is not None else sys.argv[1:]
    cmd = [
        sudo,
        "-E",          # preserve environment (keeps VIRTUAL_ENV, PYTHONPATH, etc.)
        f"PATH={os.environ.get('PATH', '')}",
        sys.executable,  # exact same Python interpreter (venv/pipx/system)
        "-m",
        "kport",
    ] + args_to_pass

    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except (subprocess.SubprocessError, OSError) as e:
        print(colorize(f"Escalation failed: {e}", Colors.RED), file=sys.stderr)
        return EXIT_GENERAL_ERROR


def handle_setup_sudo(target: str = "/usr/local/bin/kport") -> int:
    """Install a /usr/local/bin/kport wrapper so 'sudo kport' always works.

    Creates a small shell wrapper that re-invokes the same Python interpreter
    (venv/pipx/system) as root.  Running this once fixes the sudo PATH gap
    permanently for the current installation.

    Args:
        target: Filesystem path for the wrapper (default: /usr/local/bin/kport).

    Returns:
        EXIT_OK on success, EXIT_PERMISSION or EXIT_GENERAL_ERROR on failure.
    """
    import shutil
    import stat

    if sys.platform == "win32":
        print(
            colorize(
                "setup-sudo is not supported on Windows.\n"
                "Run your terminal as Administrator instead.",
                Colors.YELLOW,
            ),
            file=sys.stderr,
        )
        return EXIT_GENERAL_ERROR

    exe = sys.executable
    wrapper = (
        "#!/bin/sh\n"
        f"# kport system launcher — generated by 'kport setup-sudo'\n"
        f"# Interpreter: {exe}\n"
        f'exec "{exe}" -m kport "$@"\n'
    )

    # Try writing directly first (works if already root or target is writable)
    try:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(wrapper)
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(
            colorize(f"✓ Installed kport launcher at {target}", Colors.GREEN)
        )
        print(
            colorize(
                f"  'sudo kport' will now use: {exe}",
                Colors.WHITE,
            )
        )
        return EXIT_OK
    except PermissionError:
        pass  # Fall through to sudo-assisted write

    # Need elevated write access — use sudo tee
    sudo = shutil.which("sudo")
    if not sudo:
        print(
            colorize(
                f"Cannot write to {target}: permission denied and 'sudo' not available.\n"
                f"Manually create the wrapper:\n\n{wrapper}",
                Colors.RED,
            ),
            file=sys.stderr,
        )
        return EXIT_PERMISSION

    print(
        colorize(
            f"Writing to {target} requires sudo (you may be prompted for your password):",
            Colors.YELLOW,
        )
    )
    try:
        import subprocess

        proc = subprocess.run(
            [sudo, "tee", target],
            input=wrapper,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            print(
                colorize(f"sudo tee failed: {proc.stderr.strip()}", Colors.RED),
                file=sys.stderr,
            )
            return EXIT_PERMISSION

        # Make executable via sudo chmod
        subprocess.run(
            [sudo, "chmod", "+x", target],
            check=False,
            capture_output=True,
        )
        print(colorize(f"✓ Installed kport launcher at {target}", Colors.GREEN))
        print(colorize(f"  'sudo kport' will now use: {exe}", Colors.WHITE))
        return EXIT_OK
    except (subprocess.SubprocessError, OSError) as e:
        print(colorize(f"Failed to install launcher: {e}", Colors.RED), file=sys.stderr)
        return EXIT_GENERAL_ERROR



# ---------------------------------------------------------------------------
# Port validation and range parsing
# ---------------------------------------------------------------------------


def validate_port(port: int) -> None:
    """Validate port constraints, raising InvalidPortError on failure."""
    if not (1 <= port <= 65535):
        raise InvalidPortError(f"Port {port} is not valid. Must be 1-65535.")


def parse_port_range(port_range: str, max_ports: int = 1000) -> list[int]:
    """Parse port range strings (e.g. 8080 or 3000-3010)."""
    try:
        if "-" in port_range:
            start_s, end_s = port_range.split("-", 1)
            start = int(start_s.strip())
            end = int(end_s.strip())
            if start > end:
                raise InvalidPortError(f"Invalid range {port_range}: start > end")
            total = end - start + 1
            if total > max_ports:
                raise InvalidPortError(
                    f"Range too large ({total} ports). Maximum {max_ports} allowed."
                )
            for p in (start, end):
                validate_port(p)
            return list(range(start, end + 1))
        else:
            port = int(port_range.strip())
            validate_port(port)
            return [port]
    except ValueError:
        raise InvalidPortError(f"Invalid port or range format: {port_range}")


# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------


def _default_config_paths() -> list[str]:
    home = os.path.expanduser("~")
    return [
        os.path.join(os.getcwd(), ".kport.json"),
        os.path.join(home, ".kport.json"),
        os.path.join(home, ".config", "kport", "config.json"),
    ]


def load_config(config_path: str | None, debug: bool = False) -> dict[str, Any]:
    """Load optional JSON configuration defaults."""
    candidate_paths = [config_path] if config_path else _default_config_paths()

    for path in candidate_paths:
        if not path:
            continue
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                debug_log(debug, f"Loaded config: {path}")
                return data
            debug_log(debug, f"Ignoring non-object config: {path}")
        except json.JSONDecodeError as e:
            print(
                colorize(f"Error: invalid JSON in config file {path}: {e}", Colors.RED),
                file=sys.stderr,
            )
            sys.exit(EXIT_INVALID_INPUT)
        except OSError as e:
            print(
                colorize(f"Error: failed to read config file {path}: {e}", Colors.RED),
                file=sys.stderr,
            )
            sys.exit(EXIT_INVALID_INPUT)
    return {}


def apply_config_defaults(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    """Apply configuration options as fallback defaults to argparse Namespace."""

    def _set_bool(name: str, key: str) -> None:
        # Only apply if the attribute exists AND is still at its default (False)
        if (
            hasattr(args, name)
            and getattr(args, name) is False
            and isinstance(cfg.get(key), bool)
        ):
            setattr(args, name, cfg[key])

    def _set_num(name: str, key: str) -> None:
        # FIX: graceful_timeout default is now None; apply config only when not explicitly set
        if hasattr(args, name) and cfg.get(key) is not None:
            try:
                current = getattr(args, name)
                if name == "graceful_timeout" and current is None:
                    setattr(args, name, float(cfg[key]))
            except (ValueError, TypeError):
                pass

    _set_bool("yes", "yes")
    _set_bool("dry_run", "dry_run")
    _set_bool("json", "json")
    _set_bool("debug", "debug")
    _set_bool("force", "force")
    _set_bool("bypass_safety", "bypass_safety")
    _set_num("graceful_timeout", "graceful_timeout")

    # Custom safety lists from config
    args.protected_ports = cfg.get("protected_ports")
    args.protected_processes = cfg.get("protected_processes")

    if hasattr(args, "docker_action") and getattr(args, "docker_action", None) is None:
        v = cfg.get("docker_action")
        if v in ("stop", "restart", "rm"):
            args.docker_action = v


def _resolve_timeout(args: argparse.Namespace) -> float:
    """Return graceful_timeout, falling back to 3.0 if not set."""
    t = getattr(args, "graceful_timeout", None)
    return float(t) if t is not None else 3.0


# ---------------------------------------------------------------------------
# Safety policy CLI wrapper
# ---------------------------------------------------------------------------


def check_safety_policy(
    port: int | None,
    pids: list[int],
    args: argparse.Namespace,
    inspector: BaseInspector,
) -> SafetyDecision:
    """
    Check if a port or any associated PIDs are protected by safety policies.

    Delegates to the centralized safety module (safety.py).  Reads
    bypass_safety, protected_ports, and protected_processes from *args* so
    that CLI configuration continues to work as before.

    Returns a SafetyDecision that supports (bool, str) tuple-unpacking for
    backward compatibility.
    """
    bypass = getattr(args, "bypass_safety", False)

    # Build a config-like dict from CLI args so the shared policy function
    # can apply the same additive override logic.
    config: dict = {}
    config_ports = getattr(args, "protected_ports", None)
    if isinstance(config_ports, list):
        config["protected_ports"] = config_ports
    config_procs = getattr(args, "protected_processes", None)
    if isinstance(config_procs, list):
        config["protected_processes"] = config_procs

    decision = _core_check_safety_policy(
        port=port,
        pids=pids,
        inspector=inspector,
        bypass_safety=bypass,
        config=config if config else None,
    )

    if bypass and not decision.allowed:
        # Should not normally happen, but guard anyway.
        pass
    elif bypass and decision.allowed and decision.policy_source == "bypass":
        debug_log(
            getattr(args, "debug", False),
            f"Safety shield bypassed (port={port}, pids={pids})",
        )

    return decision


# ---------------------------------------------------------------------------
# Docker confirmation gate
# ---------------------------------------------------------------------------


def confirm_docker_rm(
    container_name: str,
    container_id: str,
    assume_yes: bool,
    force: bool,
    image: str = "",
    host_port: int | None = None,
    container_port: int | None = None,
) -> bool:
    """
    Confirmation gate for docker rm.

    Shows a rich context card with container name, image, port mapping, and
    short ID, then asks a simple [y/N] prompt.  The user never has to type
    a container name — just 'y' to confirm.

    --yes --force together skips the prompt entirely (non-interactive mode).
    --yes alone still shows the prompt because rm is irreversible.
    """
    if assume_yes and force:
        return True
    if assume_yes and not force:
        print(
            colorize(
                "Error: Removing a Docker container is irreversible. "
                "Use --force in addition to --yes to bypass interactive confirmation.",
                Colors.RED,
            ),
            file=sys.stderr,
        )
        return False

    short_id = container_id[:12] if container_id else "unknown"
    port_info = (
        f"{host_port} → {container_port}"
        if host_port and container_port
        else str(host_port or "?")
    )

    print()
    print(
        colorize(
            "  ⚠️  DESTRUCTIVE ACTION — This cannot be undone",
            Colors.YELLOW + Colors.BOLD,
        )
    )
    print(colorize("  " + "─" * 46, Colors.YELLOW))
    print(colorize(f"  Container   : {container_name}", Colors.WHITE))
    if image:
        print(colorize(f"  Image       : {image}", Colors.WHITE))
    print(colorize(f"  Port        : {port_info}", Colors.WHITE))
    print(colorize(f"  Container ID: {short_id}", Colors.WHITE))
    print(colorize("  " + "─" * 46, Colors.YELLOW))
    print()
    try:
        user_input = (
            input(colorize("  Remove this container? [y/N]: ", Colors.MAGENTA))
            .strip()
            .lower()
        )
        if user_input in ("y", "yes"):
            return True
        print(colorize("Aborted.", Colors.YELLOW))
        return False
    except KeyboardInterrupt:
        print()
        raise


# ---------------------------------------------------------------------------
# Port polling
# ---------------------------------------------------------------------------


def _poll_until_free(
    port: int, timeout: float, inspector: BaseInspector, interval: float = 0.2
) -> bool:
    """Compatibility alias — delegates to :func:`kport.port_utils.poll_until_free`.

    The polling implementation has been relocated to ``port_utils`` so that it
    can be consumed by any layer (CLI, MCP, domain) without a presentation-layer
    dependency.  This alias preserves backward compatibility for code that still
    imports ``_poll_until_free`` from ``cli_utils``.

    .. deprecated::
        Import :func:`~kport.port_utils.poll_until_free` from
        ``kport.port_utils`` directly.
    """
    from .port_utils import poll_until_free

    return poll_until_free(port, timeout, inspector, interval)


# ---------------------------------------------------------------------------
# Profile-based port resolution
# ---------------------------------------------------------------------------


def _resolve_ports_for_args(args: argparse.Namespace) -> list[int]:
    """Helper to resolve a list of ports for the command, supporting --profile."""
    profile_name = getattr(args, "profile", None)
    if profile_name:
        cfg = load_config(
            getattr(args, "config", None), debug=getattr(args, "debug", False)
        )
        profiles = load_profiles(cfg)
        resolved = resolve_profile(profile_name, profiles)
        if resolved is None:
            raise KPortError(f"Profile '{profile_name}' not found in configuration")
        return resolved

    port = getattr(args, "port", None)
    if port is not None:
        return [port]
    return []
