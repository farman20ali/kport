"""
Interactive TUI picker module for kport.

Provides a terminal user interface (curses-based with fallback) for listing active ports,
filtering, multi-selecting, and terminating processes.
"""

from __future__ import annotations

import sys
from typing import Any

from . import audit
from .diagnostics import diagnose_port
from .docker_engine import docker_action_on_container, list_docker_mappings
from .formatter import Colors, colorize, confirm_prompt
from .inspectors import BaseInspector
from .process_manager import detect_process_manager
from .safety import check_safety_policy, load_kport_config


def _format_diagnose_lines(data: dict[str, Any]) -> list[str]:
    """Helper to convert the dictionary returned by diagnose_port() to display lines."""
    lines = []
    is_blocked = data["blocked"]
    observations = data["observations"]
    inferences = data["inferences"]
    risks = data["risks"]
    recommendations = data["recommendations"]
    obs_type = observations["type"]
    obs_processes = observations["processes"]
    obs_docker = observations["docker_containers"]
    obs_bindings = observations["bindings"]
    port = data["port"]

    lines.append(f"=== DIAGNOSTICS FOR PORT {port} ===")
    lines.append("")

    # Section 1: OBSERVATION
    lines.append("OBSERVATION")
    lines.append(f"  Status: {'OCCUPIED' if is_blocked else 'FREE'}")
    if obs_type == "docker":
        lines.append("  Type:   Docker Container Mapping")
        for d in obs_docker:
            lines.append(f"  Container: {d['container_name']} ({d['container_id'][:12]})")
            lines.append(f"  Image:     {d['image']}")
            lines.append(f"  Mapping:   host {d['host_port']} -> container {d['container_port']}")
            lines.append(f"  Status:    {d['status']}")
    elif obs_type == "local":
        lines.append("  Type:   Local Process Binding")
        for p in obs_processes:
            lines.append(f"  - PID {p['pid']} ({p['name']})")
            if p.get("user"):
                lines.append(f"    User:       {p['user']}")
            if p.get("exe"):
                lines.append(f"    Executable: {p['exe']}")
            if p.get("cmdline"):
                lines.append(f"    Command:    {' '.join(p['cmdline'])}")
    elif obs_type == "local-unknown":
        lines.append("  Type:   Local Binding (Process Unidentified)")
        lines.append("  Warning: Port active but owning process not visible.")
        for b in obs_bindings:
            lines.append(f"  - Family {b['family']} binding: {b['laddr']} ({b['proto'].upper()} - {b['state'] or 'UNKNOWN'})")
    else:
        lines.append("  No processes or containers are bound to this port.")
    lines.append("")

    # Section 2: INFERENCE
    lines.append("INFERENCE")
    if inferences:
        for inf in inferences:
            if inf["type"] == "process_manager":
                lines.append(f"  - [Process Manager] PID {inf['pid']} appears to be managed by {inf['manager']} service '{inf['name']}'")
            elif inf["type"] == "docker_isolation":
                lines.append(f"  - [Docker Isolation] Container {inf['container_name']} isolates execution in network namespace")
            elif inf["type"] == "project_context":
                proj_label = inf.get("project_name") or inf.get("git_root") or "unknown"
                branch_label = f" ({inf['branch']})" if inf.get("branch") else ""
                wt_label = " [worktree]" if inf.get("is_worktree") else ""
                origin_label = f" — {inf['remote_origin']}" if inf.get("remote_origin") else ""
                confidence = inf.get("confidence", "medium")
                lines.append(f"  - [Project Context / confidence:{confidence}] PID {inf['pid']} cwd is inside repo '{proj_label}'{branch_label}{wt_label}{origin_label}")
    else:
        lines.append("  No inferred process relationships detected.")
    lines.append("")

    # Section 3: RISKS
    lines.append("RISKS")
    if risks:
        for r in risks:
            lines.append(f"  - [{r['severity']}] {r['message']}")
    else:
        lines.append("  No significant security or execution risks detected.")
    lines.append("")

    # Section 4: RECOMMENDATION
    lines.append("RECOMMENDATION")
    for rec in recommendations:
        lines.append(f"  - Action: {rec['action'].upper()}")
        lines.append(f"    Reason: {rec['reason']}")
        if rec.get("command"):
            lines.append(f"    Fix:    {rec['command']}")
    lines.append("")

    return lines



def _fetch_interactive_rows(inspector: BaseInspector) -> list[dict[str, Any]]:
    """Gather all local and docker port entries for the picker."""
    rows = []

    # 1. Local listening bindings
    local_bindings = inspector.list_listening(proto="both")
    seen_local_ports = set()
    for b in local_bindings:
        port = b.port
        seen_local_ports.add(port)
        pid = b.pid
        proc_name = b.process_name or "Unknown"
        state = b.state or "LISTEN"
        proto = b.proto

        pm_info = detect_process_manager(pid) if pid else None
        managed_by = pm_info["managed_by"] if pm_info else ""

        rows.append(
            {
                "type": "local",
                "port": port,
                "pid": pid,
                "process": proc_name,
                "proto": proto,
                "state": state,
                "managed_by": managed_by,
                "selected": False,
            }
        )

    # 2. Docker port mappings
    docker_hits = list_docker_mappings()
    for m in docker_hits:
        rows.append(
            {
                "type": "docker",
                "port": m.host_port,
                "pid": m.container_id[:12],
                "process": m.container_name,
                "proto": m.proto,
                "state": f"Docker ({m.container_port})",
                "managed_by": f"docker:{m.image}",
                "selected": False,
            }
        )

    return sorted(rows, key=lambda r: (r["port"], r["type"]))


def _fallback_numbered_menu(inspector: BaseInspector, args: Any) -> int:
    """Simple text fallback menu when curses UI is unavailable."""
    rows = _fetch_interactive_rows(inspector)
    if not rows:
        print(colorize("No active listening ports found.", Colors.GREEN))
        return 0

    print(colorize("\nActive Listening Ports:", Colors.CYAN + Colors.BOLD))
    print(
        f"{'#':<4} {'Port':<8} {'PID/ID':<12} {'Process':<20} {'Proto':<6} {'Managed By / State'}"
    )
    print("-" * 75)

    for idx, r in enumerate(rows, 1):
        pid_str = str(r["pid"]) if r["pid"] else "hidden"
        mb_str = r["managed_by"] or r["state"]
        print(
            f"[{idx:<2}] {r['port']:<8} {pid_str:<12} {r['process']:<20} {r['proto']:<6} {mb_str}"
        )

    try:
        inp = input(
            colorize(
                "\nEnter number(s) to kill (e.g. 1, 3), 'd <num>' to diagnose (e.g. d 1), or 'q' to quit: ",
                Colors.YELLOW,
            )
        ).strip()
        if not inp or inp.lower() == "q":
            print("Cancelled.")
            return 0

        if inp.lower().startswith("d "):
            target_token = inp[2:].strip()
            if target_token.isdigit():
                val = int(target_token)
                if 1 <= val <= len(rows):
                    target_row = rows[val - 1]
                    cfg = load_kport_config()
                    config = {}
                    config_ports = getattr(args, "protected_ports", None)
                    if isinstance(config_ports, list):
                        config["protected_ports"] = config_ports
                    config_procs = getattr(args, "protected_processes", None)
                    if isinstance(config_procs, list):
                        config["protected_processes"] = config_procs
                    if not config and cfg:
                        config = cfg

                    bypass = getattr(args, "bypass_safety", False)
                    try:
                        diag_res = diagnose_port(
                            port=target_row["port"],
                            inspector=inspector,
                            proto=target_row.get("proto", "tcp"),
                            config=config if config else None,
                            bypass_safety=bypass,
                        )
                        diag_lines = _format_diagnose_lines(diag_res)
                        print(colorize(f"\n--- Diagnosis for port {target_row['port']} ---", Colors.CYAN + Colors.BOLD))
                        for line in diag_lines:
                            print(line)
                    except Exception as e:  # noqa: BLE001
                        print(colorize(f"\nError diagnosing port: {e}", Colors.RED))

                    input("\nPress Enter to continue...")
                    return _fallback_numbered_menu(inspector, args)

        selected_indices = []
        for token in inp.split(","):
            token = token.strip()
            if token.isdigit():
                val = int(token)
                if 1 <= val <= len(rows):
                    selected_indices.append(val - 1)

        if not selected_indices:
            print("No valid selection.")
            return 0

        selected_rows = [rows[i] for i in selected_indices]
        return _execute_kills(inspector, selected_rows, args)
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
        return 0


def _execute_kills(
    inspector: BaseInspector, selected_rows: list[dict[str, Any]], args: Any
) -> int:
    """Execute kill action on all user-selected items."""
    if not selected_rows:
        return 0

    # Safety check before confirmation or execution
    cfg = load_kport_config()
    bypass = getattr(args, "bypass_safety", False)
    config = {}
    config_ports = getattr(args, "protected_ports", None)
    if isinstance(config_ports, list):
        config["protected_ports"] = config_ports
    config_procs = getattr(args, "protected_processes", None)
    if isinstance(config_procs, list):
        config["protected_processes"] = config_procs
    if not config and cfg:
        config = cfg

    for r in selected_rows:
        if r["type"] != "docker":
            pids = inspector.find_pids_on_port(r["port"], proto=r.get("proto", "tcp"))
            allowed, reason = check_safety_policy(
                port=r["port"],
                pids=pids,
                inspector=inspector,
                bypass_safety=bypass,
                config=config if config else None,
            )
            if not allowed:
                print(colorize(f"\n✗ {reason}", Colors.RED))
                return 1

    # Confirmation gate unless yes/assume_yes is specified
    assume_yes = getattr(args, "yes", False)
    if not assume_yes:
        print(colorize("\nWarning: You are about to terminate the following active port(s)/process(es):", Colors.YELLOW + Colors.BOLD))
        print(colorize(f"{'Type':<10} {'Port':<8} {'PID/ID':<12} {'Process/Container':<25} {'Managed By / State'}", Colors.BOLD))
        print("-" * 75)
        for r in selected_rows:
            pid_str = str(r["pid"]) if r["pid"] else "hidden"
            mb_str = r["managed_by"] or r["state"]
            type_str = r["type"].capitalize()
            proc_str = r["process"]
            if len(proc_str) > 24:
                proc_str = proc_str[:21] + "..."
            print(f"{type_str:<10} {r['port']:<8} {pid_str:<12} {proc_str:<25} {mb_str}")
        
        try:
            if not confirm_prompt("\nAre you sure you want to proceed with terminating these target(s)?", assume_yes=False):
                print(colorize("Cancelled.", Colors.RED))
                return 0
        except (KeyboardInterrupt, EOFError):
            print(colorize("\nCancelled.", Colors.RED))
            return 0

    print(
        colorize(
            f"\nProceeding to terminate {len(selected_rows)} selected target(s)...",
            Colors.CYAN,
        )
    )

    errors = 0
    for r in selected_rows:
        if r["type"] == "docker":
            action = getattr(args, "docker_action", None) or "stop"
            ok, msg = docker_action_on_container(
                r["pid"], action, dry_run=getattr(args, "dry_run", False)
            )
            if ok:
                print(
                    colorize(
                        f"✓ Port {r['port']} ({r['process']}): {msg}", Colors.GREEN
                    )
                )
            else:
                print(colorize(f"✗ Port {r['port']}: {msg}", Colors.RED))
                errors += 1
        else:
            kill_tree = getattr(args, "kill_tree", False)
            dry_run = getattr(args, "dry_run", False)
            ok, msg = inspector.kill_port(
                r["port"],
                force=getattr(args, "force", False),
                dry_run=dry_run,
                assume_yes=True,
                kill_tree=kill_tree,
                proto=r.get("proto", "tcp"),
            )
            # Emit audit record for every local kill attempt (success or failure).
            # r["pid"] is the integer PID displayed in the TUI row for local entries.
            tui_pids = [r["pid"]] if isinstance(r.get("pid"), int) else []
            audit.log_kill_port(
                port=r["port"],
                pids=tui_pids,
                dry_run=dry_run,
                success=ok,
                message=msg,
            )
            if ok:
                print(
                    colorize(
                        f"✓ Port {r['port']} ({r['process']}): {msg}", Colors.GREEN
                    )
                )
            else:
                print(colorize(f"✗ Port {r['port']}: {msg}", Colors.RED))
                errors += 1

    return 0 if errors == 0 else 1


def run_interactive_picker(inspector: BaseInspector, args: Any) -> int:
    """
    Run interactive TUI picker.
    Tries stdlib curses first, falls back to text menu if TTY / curses unavailable.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return _fallback_numbered_menu(inspector, args)

    try:
        import curses
    except ImportError:
        return _fallback_numbered_menu(inspector, args)

    def _curses_main(stdscr):
        try:
            curses.curs_set(0)
        except curses.error:
            pass

        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)  # Highlight bar
        curses.init_pair(2, curses.COLOR_GREEN, -1)  # Free/Success
        curses.init_pair(3, curses.COLOR_YELLOW, -1)  # Warning/Managed

        rows = _fetch_interactive_rows(inspector)
        if not rows:
            return None
        current_idx = 0
        start_idx = 0
        search_query = ""

        while True:
            # Filter rows by search_query
            filtered_rows = [
                r
                for r in rows
                if not search_query
                or search_query.lower()
                in f"{r['port']} {r['process']} {r['managed_by']} {r['pid']}".lower()
            ]

            if current_idx >= len(filtered_rows):
                current_idx = max(0, len(filtered_rows) - 1)

            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            visible_count = max_y - 4

            # Maintain scrolling window offset
            if current_idx < start_idx:
                start_idx = current_idx
            elif current_idx >= start_idx + visible_count:
                start_idx = current_idx - visible_count + 1

            if start_idx >= len(filtered_rows):
                start_idx = max(0, len(filtered_rows) - visible_count)

            # Curses search is active by default; show cursor
            try:
                curses.curs_set(1)
            except curses.error:
                pass

            # Header
            header = " kport Interactive Picker | Type to filter  [Space] Select  [d] Diagnose  [Ctrl-r] Refresh  [Enter] Kill  [Esc] Quit"
            stdscr.addstr(
                0, 0, header[: max_x - 1], curses.A_BOLD | curses.color_pair(1)
            )

            filter_line = f" Filter: {search_query}_"
            stdscr.addstr(1, 0, filter_line[: max_x - 1])

            col_header = f" {'Sel':<4} {'Port':<7} {'PID/ID':<12} {'Process':<18} {'Proto':<6} {'Managed By / State'}"
            stdscr.addstr(2, 0, col_header[: max_x - 1], curses.A_UNDERLINE)

            # Draw rows
            for i in range(min(visible_count, len(filtered_rows) - start_idx)):
                r = filtered_rows[start_idx + i]
                sel_char = "[x]" if r["selected"] else "[ ]"
                pid_str = str(r["pid"]) if r["pid"] else "hidden"
                mb_str = r["managed_by"] or r["state"]

                line = f" {sel_char:<4} {r['port']:<7} {pid_str:<12} {r['process']:<18} {r['proto']:<6} {mb_str}"
                line = line[: max_x - 1]

                if (start_idx + i) == current_idx:
                    stdscr.addstr(3 + i, 0, line, curses.A_REVERSE)
                else:
                    stdscr.addstr(3 + i, 0, line)

            stdscr.refresh()

            try:
                ch = stdscr.getch()
            except KeyboardInterrupt:
                return None

            # Handle navigation keys
            if ch in (curses.KEY_UP,):
                current_idx = max(0, current_idx - 1)
            elif ch in (curses.KEY_DOWN,):
                current_idx = (
                    min(len(filtered_rows) - 1, current_idx + 1) if filtered_rows else 0
                )
            
            # Handle selection toggle
            elif ch == ord(" "):
                if filtered_rows and current_idx < len(filtered_rows):
                    filtered_rows[current_idx]["selected"] = not filtered_rows[
                        current_idx
                    ]["selected"]

            # Handle reload/refresh hotkey (Ctrl-r is code 18)
            elif ch == 18:
                selected_keys = {(r["type"], r["port"], r["pid"]) for r in rows if r["selected"]}
                rows = _fetch_interactive_rows(inspector)
                for r in rows:
                    if (r["type"], r["port"], r["pid"]) in selected_keys:
                        r["selected"] = True
                current_idx = 0
                start_idx = 0

            # Handle backspace
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if search_query:
                    search_query = search_query[:-1]

            # Handle exit / escape
            elif ch == 27:
                if search_query:
                    search_query = ""
                else:
                    return None

            # Handle diagnose key
            elif ch in (ord("d"), ord("D")):
                if not filtered_rows:
                    try:
                        curses.curs_set(0)
                    except curses.error:
                        pass
                    stdscr.addstr(1, 0, "No active port to diagnose.", curses.color_pair(3))
                    stdscr.refresh()
                    curses.napms(1500)
                else:
                    target_row = filtered_rows[current_idx]
                    port_to_diagnose = target_row["port"]
                    proto_to_diagnose = target_row.get("proto", "tcp")

                    cfg = load_kport_config()
                    config = {}
                    config_ports = getattr(args, "protected_ports", None)
                    if isinstance(config_ports, list):
                        config["protected_ports"] = config_ports
                    config_procs = getattr(args, "protected_processes", None)
                    if isinstance(config_procs, list):
                        config["protected_processes"] = config_procs
                    if not config and cfg:
                        config = cfg

                    bypass = getattr(args, "bypass_safety", False)

                    try:
                        diag_res = diagnose_port(
                            port=port_to_diagnose,
                            inspector=inspector,
                            proto=proto_to_diagnose,
                            config=config if config else None,
                            bypass_safety=bypass,
                        )
                        diag_lines = _format_diagnose_lines(diag_res)
                    except Exception as e:  # noqa: BLE001
                        diag_lines = [
                            f"=== DIAGNOSTICS FOR PORT {port_to_diagnose} ===",
                            "",
                            "Error: Failed to compute diagnostics.",
                            f"Details: {e}",
                        ]

                    diag_scroll = 0
                    while True:
                        my, mx = stdscr.getmaxyx()
                        by = max(0, 2)
                        bx = max(0, 2)
                        bh = max(1, my - 4)
                        bw = max(1, mx - 4)

                        stdscr.clear()

                        modal_title = f" Diagnosis overlay — Port {port_to_diagnose}  [Esc/q] Close "
                        stdscr.addstr(by, bx, modal_title[:bw-1], curses.A_REVERSE | curses.A_BOLD)

                        instr = " [Up/Down] Scroll  [Esc/q] Close overlay "
                        stdscr.addstr(by + bh - 1, bx, instr[:bw-1], curses.A_REVERSE)

                        visible_diag_lines = bh - 2

                        for line_idx in range(visible_diag_lines):
                            actual_idx = diag_scroll + line_idx
                            if actual_idx < len(diag_lines):
                                line_content = " " + diag_lines[actual_idx]
                                stdscr.addstr(by + 1 + line_idx, bx, line_content[:bw-1])

                        stdscr.refresh()

                        try:
                            diag_ch = stdscr.getch()
                        except KeyboardInterrupt:
                            break

                        if diag_ch in (27, ord("q"), ord("Q")):
                            break
                        elif diag_ch in (curses.KEY_UP, ord("k"), ord("K")):
                            diag_scroll = max(0, diag_scroll - 1)
                        elif diag_ch in (curses.KEY_DOWN, ord("j"), ord("J")):
                            max_scroll = max(0, len(diag_lines) - visible_diag_lines)
                            diag_scroll = min(max_scroll, diag_scroll + 1)

            # Handle execution / Enter key
            elif ch in (10, 13):
                selected = [r for r in rows if r["selected"]]
                if not selected and filtered_rows:
                    selected = [filtered_rows[current_idx]]
                return selected

            # Handle other printable keys for default search
            elif 32 < ch <= 126:
                search_query += chr(ch)
                # Check for /q (quit)
                if search_query.endswith("/q"):
                    return None
                # Check for /r (refresh)
                elif search_query.endswith("/r"):
                    # Strip /r
                    search_query = search_query[:-2]
                    # Refresh selection & rows
                    selected_keys = {(r["type"], r["port"], r["pid"]) for r in rows if r["selected"]}
                    rows = _fetch_interactive_rows(inspector)
                    for r in rows:
                        if (r["type"], r["port"], r["pid"]) in selected_keys:
                            r["selected"] = True
                    current_idx = 0
                    start_idx = 0

    try:
        selected_targets = curses.wrapper(_curses_main)
        if selected_targets is None:
            print("Cancelled.")
            return 0
        return _execute_kills(inspector, selected_targets, args)
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 0
    except Exception:  # noqa: BLE001 - fallback to text menu on any curses exception
        # If curses initialization fails (e.g. TERM missing or unsupported terminal)
        return _fallback_numbered_menu(inspector, args)
