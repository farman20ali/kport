"""
Command-line interface (CLI) entry point and router for kport.
Parses options, handles configs, and delegates logic to backend components.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .cli_commands import (
    DEFAULT_PROTECTED_PORTS,
    DEFAULT_PROTECTED_PROCESS_NAMES,
    handle_connections,
    handle_diagnose,
    handle_doctor,
    handle_legacy_command,
    handle_product_command,
    handle_stop_service,
)
from .cli_utils import (
    EXIT_GENERAL_ERROR,
    EXIT_INTERRUPTED,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_PERMISSION,
    EXIT_PORT_DOCKER,
    EXIT_PORT_FREE,
    _poll_until_free,
    apply_config_defaults,
    check_safety_policy,
    confirm_docker_rm,
    get_elevation_hint,
    load_config,
    parse_port_range,
    re_exec_with_sudo,
    validate_port,
    warn_if_path_missing,
)
from .exceptions import InvalidPortError, KPortError, PermissionDeniedError
from .formatter import Colors, colorize
from .inspectors import get_inspector

# Re-export compatibility symbols for external callers and tests
__all__ = [
    "DEFAULT_PROTECTED_PORTS",
    "DEFAULT_PROTECTED_PROCESS_NAMES",
    "EXIT_GENERAL_ERROR",
    "EXIT_INTERRUPTED",
    "EXIT_INVALID_INPUT",
    "EXIT_OK",
    "EXIT_PERMISSION",
    "EXIT_PORT_DOCKER",
    "EXIT_PORT_FREE",
    "_poll_until_free",
    "apply_config_defaults",
    "check_safety_policy",
    "confirm_docker_rm",
    "handle_connections",
    "handle_diagnose",
    "handle_doctor",
    "handle_product_command",
    "handle_stop_service",
    "load_config",
    "main",
    "parse_port_range",
    "validate_port",
    "warn_if_path_missing",
]


class _QuietParser(argparse.ArgumentParser):
    _raw_argv: list[str] | None = None

    def error(self, message: str) -> None:
        raw = self._raw_argv if self._raw_argv is not None else sys.argv[1:]
        if "--json" in raw:
            print(
                json.dumps({
                    "schema_version": 1,
                    "command": None,
                    "error": {
                        "code": "invalid_arguments",
                        "message": message,
                    },
                })
            )
        else:
            print(colorize(f"Error: {message}", Colors.RED), file=sys.stderr)
            print("Run 'kport --help' for usage.", file=sys.stderr)
        sys.exit(EXIT_INVALID_INPUT)


def _configure_stdio() -> None:
    """Use UTF-8 on Windows so emoji/symbols in CLI output do not crash."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    warn_if_path_missing()
    parser = _QuietParser(
        description="kport - Cross-platform port inspector and killer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  kport -i 8080
  kport -im 3000 3001 3002
  kport -ir 3000-3010
  kport -k 8080 --yes
  kport inspect 8080
  kport kill 8080 --force
  kport mcp
""",
    )

    # Global options
    parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show actions without executing"
    )
    parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompts"
    )
    parser.add_argument("--debug", action="store_true", help="Verbose internal logs")
    parser.add_argument(
        "--config", type=str, default=None, help="Path to JSON config file"
    )
    parser.add_argument(
        "--bypass-safety",
        action="store_true",
        help="Bypass safety shields on protected ports/processes",
    )

    # Legacy flags
    parser.add_argument(
        "-i", "--inspect", type=int, metavar="PORT", help="Inspect specified port"
    )
    parser.add_argument(
        "-im",
        "--inspect-multiple",
        type=int,
        nargs="+",
        metavar="PORT",
        help="Inspect multiple ports",
    )
    parser.add_argument(
        "-ir", "--inspect-range", type=str, metavar="RANGE", help="Inspect port range"
    )
    parser.add_argument(
        "-ip",
        "--inspect-process",
        type=str,
        metavar="NAME",
        help="Inspect processes by name",
    )
    parser.add_argument(
        "-k", "--kill", type=int, metavar="PORT", help="Kill processes using port"
    )
    parser.add_argument(
        "-kp", "--kill-process", type=str, metavar="NAME", help="Kill processes by name"
    )
    parser.add_argument(
        "-ka",
        "--kill-all",
        type=int,
        nargs="+",
        metavar="PORT",
        help="Kill multiple ports",
    )
    parser.add_argument(
        "-kr", "--kill-range", type=str, metavar="RANGE", help="Kill processes on range"
    )
    parser.add_argument(
        "-l", "--list", action="store_true", help="List all listening ports"
    )
    parser.add_argument(
        "--exact", action="store_true", help="Exact process name matching"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force kill stubborn processes (SIGKILL / fuser)",
    )
    # FIX: default=None so config override only triggers when user didn't pass a value explicitly
    parser.add_argument(
        "--graceful-timeout",
        type=float,
        default=None,
        help="Seconds to wait before force kill (default: 3.0)",
    )
    parser.add_argument(
        "--wait-for-exit",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Wait for port to be free after killing (up to N seconds)",
    )
    parser.add_argument(
        "--kill-tree",
        action="store_true",
        help="Kill the process and all of its descendants",
    )
    parser.add_argument(
        "--proto",
        choices=["tcp", "udp", "both"],
        default="tcp",
        help="Protocol type: tcp, udp, or both (default: tcp)",
    )
    parser.add_argument(
        "-I",
        "--interactive",
        action="store_true",
        help="Launch interactive TUI port picker",
    )

    parser.add_argument(
        "-v", "--version", action="version", version=f"kport {__version__}"
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Start the MCP JSON-RPC server on stdio (alias for 'kport mcp')",
    )
    parser.add_argument(
        "-p",
        "--pid",
        type=int,
        metavar="PID",
        help="Specify process ID (PID) for inspection or killing",
    )

    # FIX: pass parser_class=_QuietParser so ALL subparsers inherit quiet error formatting
    sub = parser.add_subparsers(dest="command", parser_class=_QuietParser)

    # Common arguments parser to share among all subparsers
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--json", action="store_true", help="Output machine-readable JSON"
    )
    parent_parser.add_argument(
        "--dry-run", action="store_true", help="Show actions without executing"
    )
    parent_parser.add_argument(
        "-y", "--yes", action="store_true", help="Skip confirmation prompts"
    )
    parent_parser.add_argument(
        "--debug", action="store_true", help="Verbose internal logs"
    )
    parent_parser.add_argument(
        "--config", type=str, default=None, help="Path to JSON config file"
    )
    parent_parser.add_argument(
        "--bypass-safety",
        action="store_true",
        help="Bypass safety shields on protected ports/processes",
    )
    parent_parser.add_argument(
        "--proto",
        choices=["tcp", "udp", "both"],
        default="tcp",
        help="Protocol type: tcp, udp, or both (default: tcp)",
    )

    sp_inspect = sub.add_parser(
        "inspect", parents=[parent_parser], help="Inspect a port (docker-aware)"
    )
    sp_inspect.add_argument("port", type=int, nargs="?")
    sp_inspect.add_argument(
        "--profile", type=str, help="Named port profile from config"
    )
    sp_inspect.add_argument(
        "-p",
        "--pid",
        type=int,
        metavar="PID",
        help="Process ID to inspect",
    )

    sp_explain = sub.add_parser(
        "explain", parents=[parent_parser], help="Explain why a port is blocked"
    )
    sp_explain.add_argument("port", type=int)

    sp_stop_service = sub.add_parser(
        "stop-service", parents=[parent_parser], help="Cleanly stop the service manager controlling a port"
    )
    sp_stop_service.add_argument("port", type=int)
    sp_stop_service.add_argument(
        "--force", action="store_true", help="Escalate to process kill if service stop fails or port remains blocked"
    )
    sp_stop_service.add_argument(
        "--timeout", type=float, default=30.0, help="Command timeout in seconds"
    )

    sp_diagnose = sub.add_parser(
        "diagnose", parents=[parent_parser], help="Structured analysis and fix recommendations for a port"
    )
    sp_diagnose.add_argument("port", type=int)

    sp_kill = sub.add_parser(
        "kill", parents=[parent_parser], help="Safely free a port (docker-aware)"
    )
    sp_kill.add_argument("port", type=int, nargs="?")
    sp_kill.add_argument("--profile", type=str, help="Named port profile from config")
    sp_kill.add_argument(
        "--docker-action",
        choices=["stop", "restart", "rm"],
        help="Action when port belongs to Docker",
    )
    sp_kill.add_argument("--force", action="store_true")
    # FIX: default=None (consistent with top-level parser)
    sp_kill.add_argument("--graceful-timeout", type=float, default=None)
    sp_kill.add_argument(
        "--wait-for-exit",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Wait for port to be free after killing (up to N seconds)",
    )
    sp_kill.add_argument(
        "--kill-tree",
        action="store_true",
        help="Kill the process and all of its descendants",
    )
    sp_kill.add_argument(
        "-p",
        "--pid",
        type=int,
        metavar="PID",
        help="Process ID to kill",
    )

    sp_kp = sub.add_parser(
        "kill-process", parents=[parent_parser], help="Kill processes by name"
    )
    sp_kp.add_argument("name", type=str)
    sp_kp.add_argument("--exact", action="store_true")
    sp_kp.add_argument("--force", action="store_true")
    # FIX: default=None (consistent with top-level parser)
    sp_kp.add_argument("--graceful-timeout", type=float, default=None)
    sp_kp.add_argument(
        "--kill-tree",
        action="store_true",
        help="Kill the process and all of its descendants",
    )

    _sp_list = sub.add_parser(
        "list", parents=[parent_parser], help="List active ports (local + docker)"
    )

    sp_docker = sub.add_parser(
        "docker", parents=[parent_parser], help="List Docker-published ports"
    )
    sp_docker.add_argument(
        "extra", nargs="*", help=argparse.SUPPRESS
    )  # absorb unknown args like 'list'

    _sp_conflicts = sub.add_parser(
        "conflicts", parents=[parent_parser], help="Detect docker/local port conflicts"
    )

    sub.add_parser(
        "doctor",
        parents=[parent_parser],
        help="Environment-wide read-only diagnostic report",
    )

    sp_connections = sub.add_parser(
        "connections", parents=[parent_parser], help="List active network connections"
    )
    sp_connections.add_argument(
        "--pid", type=int, help="Filter connections by process ID"
    )
    sp_connections.add_argument(
        "--process", type=str, help="Filter connections by process name"
    )
    sp_connections.add_argument(
        "--port", type=int, help="Filter connections by local or remote port"
    )
    sp_connections.add_argument(
        "--state", type=str, help="Filter connections by connection state (e.g. ESTABLISHED)"
    )

    sp_watch = sub.add_parser(
        "watch", parents=[parent_parser], help="Live monitoring of port ownership"
    )
    sp_watch.add_argument("port", type=int, nargs="?")
    sp_watch.add_argument(
        "--ports", type=int, nargs="+", help="Multiple ports to watch"
    )
    sp_watch.add_argument(
        "--range", type=str, help="Range of ports to watch (e.g. 3000-3010)"
    )
    sp_watch.add_argument(
        "--interval", type=float, default=1.0, help="Polling interval in seconds"
    )
    sp_watch.add_argument(
        "--notify",
        action="store_true",
        help="Send OS desktop notification on state change",
    )
    sp_watch.add_argument(
        "--until",
        choices=["free", "occupied"],
        help="Block until port matches state ('free' or 'occupied') and exit 0",
    )
    sp_watch.add_argument(
        "--timeout",
        type=float,
        help="Maximum time in seconds to wait when using --until",
    )

    sub.add_parser(
        "mcp",
        parents=[parent_parser],
        help="Start the stdio Model Context Protocol (MCP) server",
    )
    sub.add_parser(
        "interactive",
        parents=[parent_parser],
        help="Launch interactive TUI port picker",
    )

    sp_completion = sub.add_parser(
        "completion",
        parents=[parent_parser],
        help="Generate shell autocompletion scripts",
    )
    sp_completion.add_argument(
        "shell", choices=["bash", "zsh", "fish", "powershell"], help="Target shell"
    )

    sp_setup_sudo = sub.add_parser(
        "setup-sudo",
        parents=[parent_parser],
        help="Install a /usr/local/bin/kport launcher so 'sudo kport' works from pip/pipx installs",
    )
    sp_setup_sudo.add_argument(
        "--target",
        type=str,
        default="/usr/local/bin/kport",
        metavar="PATH",
        help="Destination path for the wrapper script (default: /usr/local/bin/kport)",
    )

    sp_hold = sub.add_parser(
        "hold",
        parents=[parent_parser],
        help="Temporarily bind and reserve a port to block other applications",
    )
    sp_hold.add_argument("port", type=int, help="Port number to hold/reserve")
    sp_hold.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Automatically release port after N seconds",
    )

    sp_audit = sub.add_parser(
        "audit",
        parents=[parent_parser],
        help="Inspect audit log history of destructive kport actions",
    )
    sp_audit.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of recent entries to show (default: 20)",
    )

    parser._raw_argv = argv
    args: argparse.Namespace | None = None
    raw_args = argv if argv is not None else sys.argv[1:]
    is_json = "--json" in raw_args

    try:
        args = parser.parse_args(argv)
        is_json = getattr(args, "json", False)

        if getattr(args, "mcp", False):
            try:
                from .mcp_server import run_mcp_server

                run_mcp_server()
                return EXIT_OK
            except ImportError:
                print(
                    colorize("Error: MCP server module not available.", Colors.RED),
                    file=sys.stderr,
                )
                return EXIT_GENERAL_ERROR
            except Exception as e:  # noqa: BLE001 - top-level MCP server error handler
                print(colorize(f"MCP server error: {e}", Colors.RED), file=sys.stderr)
                return EXIT_GENERAL_ERROR

        # Load configuration
        cfg = load_config(
            getattr(args, "config", None), debug=getattr(args, "debug", False)
        )
        apply_config_defaults(args, cfg)

        inspector = get_inspector()

        # 0. Interactive TUI — kept inside the unified try/except so Ctrl+C is handled cleanly
        if (
            getattr(args, "interactive", False)
            or getattr(args, "command", None) == "interactive"
        ):
            from .interactive import run_interactive_picker

            return run_interactive_picker(inspector, args)

        # 1. Product subcommands routing
        if getattr(args, "command", None):
            return handle_product_command(args, inspector)

        # Show help if no action requested
        if not any(
            [
                args.inspect is not None,
                args.inspect_multiple,
                args.inspect_range,
                args.inspect_process,
                args.kill is not None,
                args.list,
                args.kill_process,
                args.kill_all,
                args.kill_range,
                args.pid is not None,
            ]
        ):
            parser.print_help()
            return EXIT_OK

        # 2. Legacy flag execution routing
        return handle_legacy_command(args, inspector)

    # Catch custom domain exceptions cleanly without leaking internal code
    except InvalidPortError as e:
        if is_json:
            print(
                json.dumps({
                    "schema_version": 1,
                    "command": getattr(args, "command", None) if args else None,
                    "error": {"code": "invalid_port", "message": str(e)},
                })
            )
        else:
            print(colorize(f"Error: {e}", Colors.RED), file=sys.stderr)
        return EXIT_INVALID_INPUT
    except PermissionDeniedError as e:
        hint = get_elevation_hint()
        if is_json:
            print(
                json.dumps({
                    "schema_version": 1,
                    "command": getattr(args, "command", None) if args else None,
                    "error": {"code": "permission_denied", "message": str(e)},
                })
            )
        else:
            print(colorize(f"Permission denied: {e}\n\n{hint}", Colors.RED), file=sys.stderr)
        if not is_json and sys.platform != "win32" and sys.stdin.isatty():
            return re_exec_with_sudo()
        return EXIT_PERMISSION
    except KPortError as e:
        if is_json:
            print(
                json.dumps({
                    "schema_version": 1,
                    "command": getattr(args, "command", None) if args else None,
                    "error": {"code": "kport_error", "message": str(e)},
                })
            )
        else:
            print(colorize(f"kport error: {e}", Colors.RED), file=sys.stderr)
        return EXIT_GENERAL_ERROR
    except PermissionError as e:
        hint = get_elevation_hint()
        if is_json:
            print(
                json.dumps({
                    "schema_version": 1,
                    "command": getattr(args, "command", None) if args else None,
                    "error": {
                        "code": "permission_denied",
                        "message": str(e) or "Insufficient privileges",
                    },
                })
            )
        else:
            print(colorize(f"Permission denied.\n\n{hint}", Colors.RED), file=sys.stderr)
        if not is_json and sys.platform != "win32" and sys.stdin.isatty():
            return re_exec_with_sudo()
        return EXIT_PERMISSION
    except KeyboardInterrupt:
        if is_json:
            print(
                json.dumps({
                    "schema_version": 1,
                    "command": getattr(args, "command", None) if args else None,
                    "error": {
                        "code": "interrupted",
                        "message": "Operation cancelled by user",
                    },
                })
            )
        else:
            print(colorize("\nInterrupted.", Colors.YELLOW), file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as e:  # noqa: BLE001 - main entry point catch-all
        if is_json:
            print(
                json.dumps({
                    "schema_version": 1,
                    "command": getattr(args, "command", None) if args else None,
                    "error": {"code": "unexpected_error", "message": str(e)},
                })
            )
        else:
            print(colorize(f"Unexpected error: {e}", Colors.RED), file=sys.stderr)
            if getattr(args, "debug", False) or "--debug" in raw_args:
                import traceback

                traceback.print_exc()
        return EXIT_GENERAL_ERROR


if __name__ == "__main__":
    rc = main()
    sys.exit(rc)
