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
        prog="kport",
        description="kport - Cross-platform port inspector, manager, and automated killer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Inspect port or process:
  kport inspect 8080
  kport --pid 15768
  kport -ip node

  # Free a port or kill by PID:
  kport kill 8080 --yes
  kport -k 8080 -y
  kport -k --pid 15768 -y
  kport kill --pid 15768 -y
  kport -im 8001 8000
  kport -km 8001 8000

  # Diagnose & troubleshoot:
  kport diagnose 8080
  kport doctor

  # Real-time monitoring & CI/CD synchronization:
  kport watch 8080 --until free --timeout 30
  kport watch

  # Atomic port reservation & handover:
  kport hold 5000 --timeout 60
  kport swap 8080 --command "npm start"

  # AI Agent & MCP Server:
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
        "-i",
        "--inspect",
        nargs="?",
        type=int,
        const=-1,
        default=None,
        metavar="PORT",
        help="Inspect specified port (or combine with -p/--pid to inspect by PID)",
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
        "-k",
        "--kill",
        nargs="?",
        type=int,
        const=-1,
        default=None,
        metavar="PORT",
        help="Kill processes using port (or combine with -p/--pid to kill by PID)",
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
        default="both",
        help="Protocol type: tcp, udp, or both (default: both)",
    )
    parser.add_argument(
        "--compose-project",
        "--project",
        type=str,
        dest="compose_project",
        default=None,
        help="Filter containers by Docker/Podman Compose project name",
    )
    parser.add_argument(
        "--compose-service",
        "--service",
        type=str,
        dest="compose_service",
        default=None,
        help="Filter containers by Compose service name (e.g. web, redis)",
    )
    parser.add_argument(
        "--engine",
        choices=["docker", "podman", "all"],
        default="all",
        help="Container engine to query: docker, podman, or all (default: all)",
    )
    parser.add_argument(
        "--service-aware",
        action="store_true",
        help="Cleanly stop supervising process manager to avoid restart loops",
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
        default="both",
        help="Protocol type: tcp, udp, or both (default: both)",
    )
    parent_parser.add_argument(
        "--compose-project",
        "--project",
        type=str,
        dest="compose_project",
        default=None,
        help="Filter containers by Docker/Podman Compose project name",
    )
    parent_parser.add_argument(
        "--compose-service",
        "--service",
        type=str,
        dest="compose_service",
        default=None,
        help="Filter containers by Compose service name (e.g. web, redis)",
    )
    parent_parser.add_argument(
        "--engine",
        choices=["docker", "podman", "all"],
        default="all",
        help="Container engine to query: docker, podman, or all (default: all)",
    )
    parent_parser.add_argument(
        "--service-aware",
        action="store_true",
        help="Cleanly stop supervising process manager (systemd, pm2, supervisor, Windows Service) to avoid infinite restart loops",
    )

    sp_inspect = sub.add_parser(
        "inspect",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport inspect",
        description="Inspect network ports or processes, displaying protocol, process name, PID, user, CWD, and Docker status.",
        help="Inspect a port or process (docker-aware)",
        epilog="""
Examples:
  # Inspect port 8080:
  kport inspect 8080

  # Inspect process by PID:
  kport inspect --pid 15768

  # Output structured JSON:
  kport inspect 8080 --json
""",
    )
    sp_inspect.add_argument("port", type=int, nargs="?", help="Port number to inspect (optional if --pid or --profile is specified)")
    sp_inspect.add_argument(
        "--profile", type=str, help="Named port profile from config"
    )
    sp_inspect.add_argument(
        "-p",
        "--pid",
        type=int,
        metavar="PID",
        help="Process ID (PID) to inspect",
    )

    sp_explain = sub.add_parser(
        "explain",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport explain",
        description="Explain why a port is blocked, identifying managing supervisors (systemd, PM2, supervisord, Windows Service) and providing actionable stop commands.",
        help="Explain why a port is blocked",
        epilog="""
Examples:
  # Explain why port 8080 is unavailable:
  kport explain 8080

  # Output explanation as JSON:
  kport explain 8080 --json
""",
    )
    sp_explain.add_argument("port", type=int, help="Port number to explain")

    sp_stop_service = sub.add_parser(
        "stop-service",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport stop-service",
        description="Cleanly stop the service manager (systemd, pm2, supervisord, Windows Service) controlling a port to prevent infinite restart loops.",
        help="Cleanly stop the service manager controlling a port",
        epilog="""
Examples:
  # Stop service controlling port 3000:
  kport stop-service 3000

  # Escalate to process kill if service stop fails:
  kport stop-service 3000 --force

  # Specify custom timeout in seconds:
  kport stop-service 3000 --timeout 15
""",
    )
    sp_stop_service.add_argument("port", type=int, help="Port number controlled by service manager")
    sp_stop_service.add_argument(
        "--force", action="store_true", help="Escalate to process kill if service stop fails or port remains blocked"
    )
    sp_stop_service.add_argument(
        "--timeout", type=float, default=30.0, help="Command timeout in seconds (default: 30.0)"
    )

    sp_diagnose = sub.add_parser(
        "diagnose",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport diagnose",
        description="Structured semantic diagnostics for a port, providing observations, root-cause inferences, operational risks (loopback leaks, wildcard bindings), and actionable fix recommendations.",
        help="Structured analysis and fix recommendations for a port",
        epilog="""
Examples:
  # Run full diagnostic analysis on port 8080:
  kport diagnose 8080

  # Output diagnostic analysis in JSON:
  kport diagnose 8080 --json
""",
    )
    sp_diagnose.add_argument("port", type=int, help="Port number to diagnose")

    sp_kill = sub.add_parser(
        "kill",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport kill",
        description="Safely free an occupied port or terminate a process by PID (Docker and service aware).",
        help="Safely free a port or kill by PID (docker-aware)",
        epilog="""
Examples:
  # Kill process listening on port 8080 (interactive confirmation):
  kport kill 8080

  # Kill port 8080 without confirmation prompt:
  kport kill 8080 --yes

  # Kill a specific process by PID:
  kport kill --pid 15768 --yes

  # Force kill stubborn process and its descendants:
  kport kill 8080 --force --kill-tree

  # Stop service-managed port cleanly (systemd, pm2, Windows Service):
  kport kill 3000 --service-aware

  # Stop Docker container mapped to port 5432:
  kport kill 5432 --docker-action stop

  # Preview kill actions without executing:
  kport kill 8080 --dry-run
""",
    )
    sp_kill.add_argument("port", type=int, nargs="?", help="Port number to free (optional if --pid or --profile is specified)")
    sp_kill.add_argument("--profile", type=str, help="Named port profile from config")
    sp_kill.add_argument(
        "--docker-action",
        choices=["stop", "restart", "rm"],
        help="Action when port belongs to Docker (default: prompt user)",
    )
    sp_kill.add_argument(
        "--force",
        action="store_true",
        help="Force kill stubborn processes (SIGKILL / Windows TerminateProcess)",
    )
    sp_kill.add_argument(
        "--graceful-timeout",
        type=float,
        default=None,
        help="Seconds to wait before force kill (default: 3.0)",
    )
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
        help="Process ID (PID) to kill directly",
    )

    sp_kp = sub.add_parser(
        "kill-process",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport kill-process",
        description="Terminate all running processes matching an executable or application name.",
        help="Kill processes by name",
        epilog="""
Examples:
  # Kill all processes matching 'node':
  kport kill-process node

  # Kill exact binary name match:
  kport kill-process nginx --exact

  # Force kill and terminate descendant process tree:
  kport kill-process python --force --kill-tree --yes
""",
    )
    sp_kp.add_argument("name", type=str, help="Process name or runtime target to match")
    sp_kp.add_argument("--exact", action="store_true", help="Require exact binary name match")
    sp_kp.add_argument("--force", action="store_true", help="Force kill stubborn processes (SIGKILL / TerminateProcess)")
    sp_kp.add_argument("--graceful-timeout", type=float, default=None, help="Seconds to wait before force kill (default: 3.0)")
    sp_kp.add_argument(
        "--kill-tree",
        action="store_true",
        help="Kill the process and all of its descendants",
    )

    _sp_list = sub.add_parser(
        "list",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport list",
        description="List all active listening ports (local processes and Docker/Podman containers) with PID, process name, and binding address.",
        help="List active ports (local + docker)",
        epilog="""
Examples:
  # List all listening ports:
  kport list

  # Filter by Docker Compose project:
  kport list --compose-project my-app

  # Output in JSON format:
  kport list --json
""",
    )

    sp_docker = sub.add_parser(
        "docker",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport docker",
        description="List all published container port mappings from Docker and Podman.",
        help="List Docker-published ports",
        epilog="""
Examples:
  # List all published container ports:
  kport docker

  # Filter by Compose project:
  kport docker --compose-project my-app
""",
    )
    sp_docker.add_argument(
        "extra", nargs="*", help=argparse.SUPPRESS
    )  # absorb unknown args like 'list'

    _sp_conflicts = sub.add_parser(
        "conflicts",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport conflicts",
        description="Detect port collisions between local processes and Docker/Podman container mappings.",
        help="Detect docker/local port conflicts",
    )

    sub.add_parser(
        "doctor",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport doctor",
        description="Environment-wide read-only diagnostic report detailing platform capabilities, active listeners, wildcard exposures, connection summary, and container status.",
        help="Environment-wide read-only diagnostic report",
        epilog="""
Examples:
  # Run full system diagnostics report:
  kport doctor

  # Output doctor report in JSON:
  kport doctor --json
""",
    )

    sp_connections = sub.add_parser(
        "connections",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport connections",
        description="High-performance network connection enumeration across TCP/UDP with filtering.",
        help="List active network connections",
        epilog="""
Examples:
  # List all active connections:
  kport connections

  # Filter connections by port:
  kport connections --port 8080

  # Filter connections by process ID:
  kport connections --pid 15768

  # Filter by state (e.g. ESTABLISHED, TIME_WAIT):
  kport connections --state ESTABLISHED
""",
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
        "watch",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport watch",
        description="Live monitoring of port ownership, state changes, and process transitions.",
        help="Live monitoring of port ownership",
        epilog="""
Examples:
  # Monitor port 8080 live in terminal:
  kport watch 8080

  # Global event-driven monitor for all active ports (Phase 4):
  kport watch

  # Block CI/CD pipeline until port 8080 is freed (exit 0 when free, timeout 30s):
  kport watch 8080 --until free --timeout 30

  # Wait until a server starts listening on port 3000:
  kport watch 3000 --until occupied --timeout 60

  # Monitor multiple ports with OS desktop notifications:
  kport watch --ports 3000 8080 5432 --notify

  # Monitor a port range with a 0.5s polling interval:
  kport watch --range 8000-8010 --interval 0.5
""",
    )
    sp_watch.add_argument(
        "port",
        type=int,
        nargs="?",
        help="Target port to monitor. If omitted, starts global event-driven watcher for all ports",
    )
    sp_watch.add_argument(
        "--ports",
        type=int,
        nargs="+",
        metavar="PORT",
        help="Multiple ports to watch simultaneously (e.g. --ports 3000 8080 5432)",
    )
    sp_watch.add_argument(
        "--range",
        type=str,
        metavar="START-END",
        help="Range of ports to watch (e.g. --range 3000-3010)",
    )
    sp_watch.add_argument(
        "--interval",
        type=float,
        default=1.0,
        metavar="SECONDS",
        help="Polling interval in seconds (default: 1.0)",
    )
    sp_watch.add_argument(
        "--notify",
        action="store_true",
        help="Send OS desktop notification on port state change (connect/disconnect)",
    )
    sp_watch.add_argument(
        "--until",
        choices=["free", "occupied"],
        help="Block execution until port matches state ('free' or 'occupied') and exit 0",
    )
    sp_watch.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        help="Maximum seconds to wait when using --until, or total watch duration",
    )

    sub.add_parser(
        "watcher-info",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport watcher-info",
        description="Show platform-specific watcher acceleration details (Linux /proc or Windows GetExtendedTcpTable).",
        help="Show platform-specific watcher acceleration details (Phase 4)",
    )

    sub.add_parser(
        "mcp",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport mcp",
        description="Start the Model Context Protocol (MCP) JSON-RPC server on stdio for AI agent pairing.",
        help="Start the stdio Model Context Protocol (MCP) server",
    )
    sub.add_parser(
        "interactive",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport interactive",
        description="Launch visual interactive Text User Interface (TUI) port picker with search, filtering, and selection.",
        help="Launch interactive TUI port picker",
    )

    sp_completion = sub.add_parser(
        "completion",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport completion",
        description="Generate shell autocompletion scripts for bash, zsh, fish, or powershell.",
        help="Generate shell autocompletion scripts",
        epilog="""
Examples:
  # PowerShell completion:
  kport completion powershell | Out-String | Invoke-Expression

  # Bash completion:
  eval "$(kport completion bash)"
""",
    )
    sp_completion.add_argument(
        "shell", choices=["bash", "zsh", "fish", "powershell"], help="Target shell"
    )

    sp_setup_sudo = sub.add_parser(
        "setup-sudo",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport setup-sudo",
        description="Install a /usr/local/bin/kport launcher so 'sudo kport' works from pip/pipx installs.",
        help="Install a /usr/local/bin/kport launcher so 'sudo kport' works from pip/pipx installs",
        epilog="""
Examples:
  # Install standard sudo launcher:
  sudo kport setup-sudo

  # Install to custom path:
  sudo kport setup-sudo --target /usr/bin/kport
""",
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
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport hold",
        description="Temporarily bind and reserve a port to block other applications or daemons from taking it.",
        help="Temporarily bind and reserve a port to block other applications",
        epilog="""
Examples:
  # Hold port 5000 until Enter or Ctrl+C is pressed:
  kport hold 5000

  # Hold port 5000 for 30 seconds, then automatically release:
  kport hold 5000 --timeout 30
""",
    )
    sp_hold.set_defaults(proto="tcp")
    sp_hold.add_argument("port", type=int, help="Port number to hold/reserve")
    sp_hold.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Automatically release port after N seconds",
    )

    sp_swap = sub.add_parser(
        "swap",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport swap",
        description="Atomically free an occupied port, hold it with an exclusive socket to prevent race conditions, and optionally hand it over to a replacement command.",
        help="Atomically free a port and hold/hand over to prevent race conditions",
        epilog="""
Examples:
  # Free port 8080 and hold it until ready:
  kport swap 8080

  # Free port 8080 and atomically launch replacement service:
  kport swap 8080 --command "npm start"

  # Force kill existing process if needed:
  kport swap 8080 --force --command "python app.py"
""",
    )
    sp_swap.add_argument("port", type=int, help="Target port to swap and hold")
    sp_swap.add_argument(
        "--command",
        "-c",
        dest="exec_cmd",
        type=str,
        default=None,
        help="Command to launch after port is secured (atomic handover)",
    )
    sp_swap.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Maximum seconds to hold port before releasing (default: wait for Enter/Ctrl+C)",
    )
    sp_swap.add_argument(
        "--force",
        action="store_true",
        help="Force kill stubborn processes",
    )

    sp_audit = sub.add_parser(
        "audit",
        parents=[parent_parser],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        prog="kport audit",
        description="Inspect audit log history of destructive kport actions (kills, stops, swaps) with filtering and SIEM/CEF export.",
        help="Inspect audit log history of destructive kport actions",
        epilog="""
Examples:
  # View recent audit history:
  kport audit

  # Export audit log in SIEM Common Event Format (CEF):
  kport audit --format siem --export audit.cef

  # Output recent audit entries as JSON:
  kport audit --format json --limit 50
""",
    )
    sp_audit.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of recent entries to show (default: 20)",
    )
    sp_audit.add_argument(
        "--format",
        choices=["table", "json", "ndjson", "siem"],
        default="table",
        help="Output format: table, json, ndjson, or siem (default: table)",
    )
    sp_audit.add_argument(
        "--export",
        type=str,
        default=None,
        metavar="FILE",
        help="Export audit logs to specified file path",
    )
    sp_audit.add_argument(
        "--siem",
        action="store_true",
        help="Export logs in SIEM Common Event Format (alias for --format=siem)",
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
