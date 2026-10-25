"""
Terminal formatting and output representation module for kport.
Decouples UI and CLI layout generation from networking core logic.
"""
from __future__ import annotations

import json
import platform
from dataclasses import asdict
from typing import Any


class Colors:
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    WHITE = "\033[97m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


# R13 fix: set up ANSI once at import time, not on every colorize() call.
_ansi_enabled = False


def _enable_ansi_once() -> None:
    """Enable ANSI escape processing on Windows consoles (runs once at import)."""
    global _ansi_enabled
    if _ansi_enabled or platform.system() != "Windows":
        _ansi_enabled = True
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:  # noqa: BLE001, S110 - ignore Windows console configuration errors
        pass
    _ansi_enabled = True


_enable_ansi_once()


def colorize(text: str, color: str) -> str:
    """Colorize text for terminal output."""
    return f"{color}{text}{Colors.RESET}"


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------


def _trunc(s: str, n: int) -> str:
    """Truncate string to n chars with ellipsis if needed."""
    return s if len(s) <= n else s[: n - 1] + "…"


def _col_widths(
    rows: list[list[str]], headers: list[str], max_width: int = 40
) -> list[int]:
    """Compute column widths as max(header, data) capped at max_width."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = min(max_width, max(widths[i], len(cell)))
    return widths


def print_table_listen(bindings: list[Any]) -> None:
    """Print standard tabular listing of local listening ports."""
    if not bindings:
        print(colorize("No listening ports found.", Colors.YELLOW))
        return

    headers = ["Port", "Proto", "PID", "Process", "State", "Address"]
    rows = [
        [
            str(b.port),
            (b.proto or "tcp").upper(),
            str(b.pid) if b.pid is not None else "-",
            b.process_name or "-",
            b.state or "-",
            b.laddr or "-",
        ]
        for b in bindings
    ]
    # P4 fix: compute dynamic column widths
    widths = _col_widths(rows, headers)
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(colorize(header_line, Colors.BOLD))
    print("─" * (sum(widths) + 2 * len(widths)))
    for row, _b in zip(rows, bindings):
        cells = [_trunc(row[j], widths[j]).ljust(widths[j]) for j in range(len(widths))]
        # Apply padding BEFORE colorize so ANSI bytes don't skew column width
        cells[0] = colorize(_trunc(row[0], widths[0]).ljust(widths[0]).strip(), Colors.CYAN)
        # Colour the proto badge: TCP=blue, UDP=magenta
        proto_val = row[1]
        proto_color = Colors.BLUE if proto_val == "TCP" else Colors.MAGENTA
        cells[1] = colorize(_trunc(row[1], widths[1]).ljust(widths[1]).strip(), proto_color)
        print("  ".join(cells))


def jsonify_bindings(bindings: list[Any]) -> str:
    """Render list of PortBindings as JSON string."""
    return json.dumps([asdict(b) for b in bindings], indent=2)


def confirm_prompt(prompt: str, assume_yes: bool = False) -> bool:
    """Show an interactive yes/no confirmation prompt to the user."""
    if assume_yes:
        return True
    try:
        resp = input(colorize(prompt + " (y/N): ", Colors.MAGENTA))
        return resp.strip().lower() in ("y", "yes")
    except KeyboardInterrupt:
        # R14 fix: propagate KeyboardInterrupt instead of calling sys.exit().
        # The main() exception handler will cleanly return EXIT_GENERAL_ERROR.
        print()
        raise


def choose_docker_action(assume_yes: bool) -> str | None:
    """Interactive Docker action selector."""
    if assume_yes:
        return "stop"
    print(
        colorize(
            "\nChoose action:\n1) Stop container\n2) Restart container\n3) Remove container (irreversible!)\n4) Cancel",
            Colors.CYAN,
        )
    )
    try:
        resp = input(colorize("Select (1-4): ", Colors.MAGENTA)).strip()
    except KeyboardInterrupt:
        print()
        raise
    mapping = {"1": "stop", "2": "restart", "3": "rm", "4": None}
    return mapping.get(resp)


def print_table_docker(mappings: list[Any]) -> None:
    """Print tabular details of Docker mapped ports."""
    if not mappings:
        print(colorize("No Docker-published ports found.", Colors.YELLOW))
        return

    headers = ["PORT", "CONTAINER", "IMAGE", "STATUS"]
    rows = [[str(m.host_port), m.container_name, m.image, m.status] for m in mappings]
    # P5 fix: dynamic column widths for long image / container names
    widths = _col_widths(rows, headers)
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(colorize(header_line, Colors.BOLD))
    print("─" * (sum(widths) + 2 * len(widths)))
    for row in rows:
        cells = [_trunc(row[j], widths[j]).ljust(widths[j]) for j in range(len(widths))]
        # Apply padding BEFORE colorize so ANSI bytes don't skew column width
        cells[0] = colorize(_trunc(row[0], widths[0]).ljust(widths[0]).strip(), Colors.CYAN)
        print("  ".join(cells))


def print_table_list_product(local_bindings: list[Any], docker_maps: list[Any]) -> None:
    """Print unified list output representing PORT, PROTO, TYPE, and OWNER."""
    rows: dict[int, dict[str, Any]] = {}
    for b in local_bindings:
        rows.setdefault(b.port, {})
        rows[b.port].setdefault("locals", []).append(b)
    for d in docker_maps:
        rows.setdefault(d.host_port, {})
        rows[d.host_port]["docker"] = d

    if not rows:
        print(colorize("No active ports found.", Colors.YELLOW))
        return

    # Build data rows for dynamic width computation
    data_rows = []
    proto_vals: list[str] = []
    for port in sorted(rows.keys()):
        if "docker" in rows[port] and "locals" not in rows[port]:
            owner = rows[port]["docker"].container_name
            data_rows.append([str(port), "TCP", "docker", owner])
            proto_vals.append("TCP")
        elif "docker" in rows[port] and "locals" in rows[port]:
            owner = rows[port]["docker"].container_name
            data_rows.append([str(port), "TCP", "conflict", owner])
            proto_vals.append("TCP")
        else:
            for b in rows[port]["locals"]:
                proto = (b.proto or "tcp").upper()
                owner = b.process_name or "-"
                data_rows.append([str(port), proto, "local", owner])
                proto_vals.append(proto)

    headers = ["PORT", "PROTO", "TYPE", "OWNER"]
    widths = _col_widths(data_rows, headers)
    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(colorize(header_line, Colors.BOLD))
    print("─" * (sum(widths) + 2 * len(widths)))
    for row, pv in zip(data_rows, proto_vals):
        cells = [_trunc(row[j], widths[j]).ljust(widths[j]) for j in range(len(widths))]
        cells[0] = colorize(_trunc(row[0], widths[0]).ljust(widths[0]).strip(), Colors.CYAN)
        proto_color = Colors.BLUE if pv == "TCP" else Colors.MAGENTA
        cells[1] = colorize(_trunc(row[1], widths[1]).ljust(widths[1]).strip(), proto_color)
        print("  ".join(cells))


def print_port_bindings_detail(
    port: int,
    bindings: list[Any],
    inspector: Any,
    header: str | None = None,
) -> None:
    """Print a rich per-binding breakdown for a port.

    Shows each binding grouped by protocol, with PID, process name, command,
    and user.  Used by inspect and kill flows to clearly distinguish TCP from
    UDP listeners on the same port number.

    Parameters
    ----------
    port:
        The port number being described.
    bindings:
        List of PortBinding objects for this port (may mix TCP and UDP).
    inspector:
        Active inspector used to enrich process info.
    header:
        Optional bold header line.  Defaults to ``"Port <port> bindings:"``.
    """
    if header is None:
        header = f"Port {port} bindings:"
    print(colorize(header, Colors.CYAN + Colors.BOLD))
    if not bindings:
        print(colorize("  (none)", Colors.YELLOW))
        return
    # Group by proto so TCP and UDP sections are visually separated
    for proto_key in ("tcp", "udp"):
        proto_bindings = [b for b in bindings if (b.proto or "tcp").lower() == proto_key]
        if not proto_bindings:
            continue
        badge_color = Colors.BLUE if proto_key == "tcp" else Colors.MAGENTA
        badge = colorize(proto_key.upper(), badge_color + Colors.BOLD)
        print(f"  [{badge}]")
        by_pid: dict[int | None, list[Any]] = {}
        for b in proto_bindings:
            by_pid.setdefault(b.pid, []).append(b)

        for pid, pbindings in by_pid.items():
            pname = pbindings[0].process_name or "?"
            raw_addrs = [b.laddr or f"*:{port}" for b in pbindings]
            addrs = ", ".join(dict.fromkeys(raw_addrs))
            pid_str = colorize(str(pid), Colors.YELLOW) if pid else colorize("(no PID)", Colors.RED)
            print(f"    PID {pid_str}  {colorize(pname, Colors.WHITE)}  @ {addrs}")
            if pid:
                try:
                    info = inspector.get_process_info(pid)
                    if info:
                        if info.cmdline:
                            cmd = " ".join(info.cmdline)
                            print(f"      cmd:  {cmd}")
                        if info.user:
                            print(f"      user: {info.user}")
                except Exception:  # noqa: BLE001, S110
                    pass


def jsonify_docker(mappings: list[Any]) -> str:
    """Render list of Docker mappings as JSON string."""
    return json.dumps([asdict(m) for m in mappings], indent=2)
