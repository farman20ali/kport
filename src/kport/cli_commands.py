"""
cli_commands.py — Command handlers and orchestration for kport.

Contains the actual command routing and execution workflows (e.g. handle_product_command,
handle_kill_port, handle_diagnose, handle_connections, etc.) and legacy CLI flags routing.

Architectural constraints:
  - cli_commands.py may import from cli_utils.py and domain modules.
  - cli.py imports from and routes to cli_commands.py.
  - Domain modules MUST NOT import from cli_commands.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any

from . import audit

# Import CLI utilities & exit codes
from .cli_utils import (
    EXIT_GENERAL_ERROR,
    EXIT_INVALID_INPUT,
    EXIT_OK,
    EXIT_PERMISSION,
    EXIT_PORT_DOCKER,
    EXIT_PORT_FREE,
    _has_access_denied_on_pids,
    _is_elevated,
    _json_out,
    _poll_until_free,
    _resolve_ports_for_args,
    _resolve_timeout,
    check_safety_policy,
    confirm_docker_rm,
    handle_setup_sudo,
    parse_port_range,
    validate_port,
)
from .constants import PROTECTED_PORTS, PROTECTED_PROCESS_NAMES
from .diagnostics import (
    diagnose_port as _diagnose_port_data,
)
from .diagnostics import (
    filter_connections as _filter_connections_data,
)
from .diagnostics import (
    run_doctor as _run_doctor_data,
)
from .docker_engine import (
    docker_action_on_container,
    docker_available,  # noqa: F401  # imported for mock-patching in tests
    docker_mappings_for_host_port,
    list_docker_mappings,
)
from .formatter import (
    Colors,
    choose_docker_action,
    colorize,
    confirm_prompt,
    print_port_bindings_detail,
    print_table_docker,
    print_table_list_product,
    print_table_listen,
)
from .inspectors import BaseInspector
from .notify import notify as _desktop_notify
from .process_manager import detect_process_manager
from .project import resolve_project  # noqa: F401  # imported for mock-patching in tests
from .service_actions import stop_service

DEFAULT_PROTECTED_PORTS = PROTECTED_PORTS
DEFAULT_PROTECTED_PROCESS_NAMES = PROTECTED_PROCESS_NAMES


def handle_diagnose(args: argparse.Namespace, inspector: BaseInspector) -> int:
    """Implement structured diagnostic analysis on a specific port.

    Delegates computation to diagnostics.py and renders the result here.
    Separates:
      - OBSERVATION (direct facts)
      - INFERENCE (derived / heuristic conclusions)
      - RISKS (security / process concerns)
      - RECOMMENDATION (remediation plan)
    """
    port = args.port
    validate_port(port)

    # Build config for safety-aware risk annotations
    config: dict = {}
    config_ports = getattr(args, "protected_ports", None)
    if isinstance(config_ports, list):
        config["protected_ports"] = config_ports
    config_procs = getattr(args, "protected_processes", None)
    if isinstance(config_procs, list):
        config["protected_processes"] = config_procs

    bypass = getattr(args, "bypass_safety", False)
    # Shared structured computation — no rendering here
    data = _diagnose_port_data(
        port=port,
        inspector=inspector,
        proto=getattr(args, "proto", "both"),
        config=config if config else None,
        bypass_safety=bypass,
    )

    is_blocked = data["blocked"]
    observations = data["observations"]
    inferences = data["inferences"]
    risks = data["risks"]
    recommendations = data["recommendations"]
    obs_type = observations["type"]
    obs_processes = observations["processes"]
    obs_docker = observations["docker_containers"]
    obs_bindings = observations["bindings"]

    if args.json:
        print(_json_out("diagnose", data))
        return EXIT_PORT_DOCKER if obs_type == "docker" else (EXIT_OK if is_blocked else EXIT_PORT_FREE)

    # Human-readable console print
    print(colorize(f"=== DIAGNOSTICS FOR PORT {port} ===", Colors.CYAN + Colors.BOLD))
    print()

    # Section 1: OBSERVATION
    print(colorize("OBSERVATION", Colors.BOLD + Colors.WHITE))
    print(f"  Status: {'OCCUPIED' if is_blocked else 'FREE'}")
    if obs_type == "docker":
        print("  Type:   Docker Container Mapping")
        for d in obs_docker:
            print(f"  Container: {d['container_name']} ({d['container_id'][:12]})")
            print(f"  Image:     {d['image']}")
            print(f"  Mapping:   host {d['host_port']} -> container {d['container_port']}")
            print(f"  Status:    {d['status']}")
    elif obs_type == "local":
        print("  Type:   Local Process Binding")
        for p in obs_processes:
            proto_val = (p.get("proto") or "tcp").upper()
            if "/" in proto_val:
                badge_color = Colors.CYAN
            elif proto_val == "TCP":
                badge_color = Colors.BLUE
            else:
                badge_color = Colors.MAGENTA
            badge = colorize(proto_val, badge_color + Colors.BOLD)
            print(f"  - PID {p['pid']} ({p['name']}) [{badge}]")
            if p.get("user"):
                print(f"    User:       {p['user']}")
            if p.get("exe"):
                print(f"    Executable: {p['exe']}")
            if p.get("cmdline"):
                print(f"    Command:    {' '.join(p['cmdline'])}")
    elif obs_type == "local-unknown":
        print("  Type:   Local Binding (Process Unidentified)")
        print(colorize("  Warning: Port active but owning process not visible.", Colors.YELLOW))
        for b in obs_bindings:
            print(f"  - Family {b['family']} binding: {b['laddr']} ({b['proto'].upper()} - {b['state'] or 'UNKNOWN'})")
    else:
        print("  No processes or containers are bound to this port.")
    print()

    # Section 2: INFERENCE
    print(colorize("INFERENCE", Colors.BOLD + Colors.WHITE))
    if inferences:
        for inf in inferences:
            if inf["type"] == "process_manager":
                print(f"  - [Process Manager] PID {inf['pid']} appears to be managed by {inf['manager']} service '{inf['name']}'")
            elif inf["type"] == "docker_isolation":
                print(f"  - [Docker Isolation] Container {inf['container_name']} isolates execution in network namespace")
            elif inf["type"] == "project_context":
                proj_label = inf.get("project_name") or inf.get("git_root") or "unknown"
                branch_label = f" ({inf['branch']})" if inf.get("branch") else ""
                wt_label = " [worktree]" if inf.get("is_worktree") else ""
                origin_label = f" — {inf['remote_origin']}" if inf.get("remote_origin") else ""
                confidence = inf.get("confidence", "medium")
                print(f"  - [Project Context / confidence:{confidence}] PID {inf['pid']} cwd is inside repo '{proj_label}'{branch_label}{wt_label}{origin_label}")
    else:
        print("  No inferred process relationships detected.")
    print()

    # Section 3: RISKS
    print(colorize("RISKS", Colors.BOLD + Colors.WHITE))
    if risks:
        for r in risks:
            sev_color = Colors.RED if r["severity"] == "IMPORTANT" else Colors.YELLOW
            print(colorize(f"  - [{r['severity']}] {r['message']}", sev_color))
    else:
        print("  No significant security or execution risks detected.")
    print()

    # Section 4: RECOMMENDATION
    print(colorize("RECOMMENDATION", Colors.BOLD + Colors.WHITE))
    for rec in recommendations:
        rec_color = Colors.GREEN if rec["safe"] else Colors.RED
        print(f"  - Action: {rec['action'].upper()}")
        print(f"    Reason: {rec['reason']}")
        if rec.get("command"):
            print(f"    Fix:    {colorize(rec['command'], rec_color)}")
    print()

    return EXIT_PORT_DOCKER if obs_type == "docker" else (EXIT_OK if is_blocked else EXIT_PORT_FREE)


def handle_stop_service(args: argparse.Namespace, inspector: BaseInspector) -> int:
    """Orchestrate stopping a process-manager controlled service occupying a port."""
    port = args.port
    validate_port(port)

    # 1. Resolve initial state (PIDs, manager)
    proto = getattr(args, "proto", "tcp")
    pids = inspector.find_pids_on_port(port, proto=proto)

    config: dict = {}
    config_ports = getattr(args, "protected_ports", None)
    if isinstance(config_ports, list):
        config["protected_ports"] = config_ports
    config_procs = getattr(args, "protected_processes", None)
    if isinstance(config_procs, list):
        config["protected_processes"] = config_procs

    bypass = getattr(args, "bypass_safety", False)
    data = _diagnose_port_data(
        port=port,
        inspector=inspector,
        proto=proto,
        config=config if config else None,
        bypass_safety=bypass,
    )

    is_blocked = data["blocked"]
    if not is_blocked:
        if args.json:
            print(_json_out("stop-service", {
                "success": True,
                "port": port,
                "message": f"Port {port} is already free.",
                "verified_free": True
            }))
        else:
            print(colorize(f"Port {port} is already free.", Colors.GREEN))
        return EXIT_PORT_FREE

    inferences = data["inferences"]
    pm_managed = [inf for inf in inferences if inf["type"] == "process_manager"]
    if not pm_managed:
        if args.json:
            print(_json_out("stop-service", {
                "success": False,
                "port": port,
                "message": f"No supported process manager detected for port {port}."
            }))
        else:
            print(colorize(f"No supported process manager detected for port {port}.", Colors.RED), file=sys.stderr)
            print(f"You can use:\n  kport kill {port}", file=sys.stderr)
        return EXIT_INVALID_INPUT

    pm = pm_managed[0]
    manager = pm["manager"]
    service_name = pm["name"]

    # 2. Safety check on current PIDs
    decision = check_safety_policy(port, pids, args, inspector)
    if not decision.allowed:
        if args.json:
            print(_json_out("stop-service", {"success": False, "message": decision.reason}))
        else:
            print(colorize(decision.reason, Colors.RED), file=sys.stderr)
        return EXIT_PERMISSION

    # Generate print command
    if manager == "systemd":
        cmd = f"systemctl stop {service_name}"
    elif manager == "pm2":
        cmd = f"pm2 stop {service_name}"
    elif manager == "supervisor":
        cmd = f"supervisorctl stop {service_name}"
    elif manager == "windows-service":
        services = [s.strip() for s in service_name.split(",") if s.strip()]
        if len(services) == 1:
            cmd = f"Stop-Service -Name {services[0]}"
        else:
            cmd = " ; ".join(f"Stop-Service -Name {s}" for s in services)
    else:
        cmd = ""

    # 3. Print stop service context card (human-only)
    if not args.json:
        print(colorize(f"=== STOPPING SERVICE FOR PORT {port} ===", Colors.CYAN + Colors.BOLD))
        print()
        print(colorize("DETECTION", Colors.BOLD + Colors.WHITE))
        print(f"  Manager:  {manager}")
        print(f"  Service:  {service_name}")
        print(f"  Command:  {cmd}")
        print()
        print(colorize("  ⚠️  Stopping via service manager prevents auto-restart.", Colors.YELLOW))
        print()

    # 4. Confirmation Prompt
    if not args.json:
        prompt_msg = f"Stop service '{service_name}' via {manager}?"
        try:
            if not confirm_prompt(prompt_msg, assume_yes=args.yes):
                print(colorize("Operation cancelled.", Colors.YELLOW))
                return EXIT_GENERAL_ERROR
        except KeyboardInterrupt:
            print(colorize("\nInterrupted.", Colors.YELLOW), file=sys.stderr)
            return EXIT_GENERAL_ERROR

    # 5. Execute action via service_actions domain layer
    timeout = getattr(args, "timeout", 30.0) or 30.0
    dry_run = getattr(args, "dry_run", False)

    res = stop_service(
        manager=manager,
        service_name=service_name,
        timeout=timeout,
        dry_run=dry_run,
    )

    # 6. Verify port status after service action
    verified_free = False
    if res.success and not dry_run:
        verified_free = _poll_until_free(port, 3.0, inspector, proto=proto)
    elif dry_run:
        verified_free = False

    # 7. Check force escalation logic
    escalation_triggered = False
    escalation_success = False
    escalation_message = ""
    second_decision_allowed = True
    new_pids = []

    if not verified_free and getattr(args, "force", False) and not dry_run:
        escalation_triggered = True
        new_pids = inspector.find_pids_on_port(port, proto=proto)
        if new_pids:
            # Re-run safety policy check on current PIDs (safety check again)
            second_decision = check_safety_policy(port, new_pids, args, inspector)
            if not second_decision.allowed:
                second_decision_allowed = False
                escalation_message = f"Escalation safety check failed: {second_decision.reason}"
            else:
                if not args.json:
                    print(colorize(f"Service stop did not free port {port}. Escalating to process kill...", Colors.YELLOW))
                kill_ok, kill_msg = inspector.kill_port(
                    port,
                    graceful_timeout=3.0,
                    force=True,
                    dry_run=False,
                    debug=bool(getattr(args, "debug", False)),
                    assume_yes=True,
                    proto=proto,
                )
                escalation_success = kill_ok
                escalation_message = kill_msg
                verified_free = _poll_until_free(port, 3.0, inspector, proto=proto)
        else:
            verified_free = _poll_until_free(port, 3.0, inspector, proto=proto)
            escalation_success = verified_free
            escalation_message = "Port went free during escalation check."

    # 8. Log audit log entries
    audit.log_service_stop(
        port=port,
        manager=manager,
        service_name=service_name,
        command=res.command_executed,
        dry_run=dry_run,
        success=res.success,
        verified_free=verified_free,
        message=res.message,
    )

    if escalation_triggered and second_decision_allowed:
        audit.log_kill_port(
            port=port,
            pids=new_pids,
            dry_run=False,
            success=escalation_success,
            message=f"Escalation kill: {escalation_message}",
        )

    # 9. Output/return result
    overall_success = verified_free or (res.success and dry_run)

    if args.json:
        out_data = {
            "success": res.success,
            "port": port,
            "manager": manager,
            "service_name": service_name,
            "command": res.command_executed,
            "verified_free": verified_free,
            "dry_run": dry_run,
            "message": res.message,
        }
        if escalation_triggered:
            out_data["escalation"] = {
                "triggered": True,
                "success": escalation_success,
                "message": escalation_message,
            }
        print(_json_out("stop-service", out_data))
        return EXIT_OK if overall_success else EXIT_GENERAL_ERROR

    if dry_run:
        print(colorize(f"[DRY RUN] Would execute: {res.command_executed}", Colors.YELLOW))
        print(colorize("[DRY RUN] No changes made.", Colors.YELLOW))
        return EXIT_OK

    if res.success:
        if verified_free:
            if escalation_triggered:
                print(colorize(f"✓ Port {port} successfully freed via process kill escalation.", Colors.GREEN))
            else:
                print(colorize(f"✓ Service '{service_name}' stopped and port {port} is now free.", Colors.GREEN))
            return EXIT_OK
        else:
            print(colorize(f"⚠ Service '{service_name}' stopped but port {port} is still blocked.", Colors.YELLOW), file=sys.stderr)
            if escalation_triggered:
                if not second_decision_allowed:
                    print(colorize(f"⚠ Escalation blocked by safety: {escalation_message}", Colors.RED), file=sys.stderr)
                elif escalation_success:
                    print(colorize(f"✓ Port {port} successfully freed via process kill escalation.", Colors.GREEN))
                    return EXIT_OK
                else:
                    print(colorize(f"Error: Escalation kill failed. {escalation_message}", Colors.RED), file=sys.stderr)
            else:
                print(colorize("Run stop-service with --force to attempt process termination.", Colors.YELLOW), file=sys.stderr)
            return EXIT_GENERAL_ERROR
    else:
        print(colorize(f"Error: Stop command failed: {res.message}", Colors.RED), file=sys.stderr)
        if escalation_triggered:
            if not second_decision_allowed:
                print(colorize(f"⚠ Escalation blocked by safety: {escalation_message}", Colors.RED), file=sys.stderr)
            elif escalation_success:
                print(colorize(f"✓ Port {port} successfully freed via process kill escalation.", Colors.GREEN))
                return EXIT_OK
            else:
                print(colorize(f"Error: Escalation kill failed. {escalation_message}", Colors.RED), file=sys.stderr)
        return EXIT_GENERAL_ERROR


def handle_connections(args: argparse.Namespace, inspector: BaseInspector) -> int:
    """Display active network connections with optional filters.

    Delegates filtering and serialization to diagnostics.py.
    """
    pid_filter = None
    if getattr(args, "pid", None) is not None:
        try:
            pid_filter = int(args.pid)
        except (ValueError, TypeError):
            pass

    port_filter = None
    if getattr(args, "port", None) is not None:
        try:
            port_filter = int(args.port)
        except (ValueError, TypeError):
            pass

    process_filter = getattr(args, "process", None) or None
    state_filter = getattr(args, "state", None) or None

    if getattr(args, "json", False):
        # JSON path: use shared serialization from diagnostics.py
        serialized = _filter_connections_data(
            inspector,
            pid=pid_filter,
            process=process_filter,
            port=port_filter,
            state=state_filter,
        )
        data = {"connections": serialized, "count": len(serialized)}
        print(_json_out("connections", data))
        return EXIT_OK

    # Text path: use raw ConnectionInfo objects so we can format the table
    conns = inspector.list_connections()

    if pid_filter is not None:
        conns = [c for c in conns if c.pid == pid_filter]

    if process_filter:
        p_lower = process_filter.lower()
        conns = [c for c in conns if c.process_name and p_lower in c.process_name.lower()]

    if port_filter is not None:
        conns = [c for c in conns if c.local_port == port_filter or c.remote_port == port_filter]

    if state_filter:
        s_upper = state_filter.upper()
        conns = [c for c in conns if c.state and c.state.upper() == s_upper]

    print(colorize("=== ACTIVE CONNECTIONS ===", Colors.CYAN + Colors.BOLD))
    print()

    if not conns:
        print("No active connections found.")
        print()
        return EXIT_OK

    # Print header  (PROCESS column width = 24 to fit most real names)
    _PROC_W = 24
    header = f"{'PID':<8}{'PROCESS':<{_PROC_W}}{'LOCAL':<28}{'REMOTE':<28}{'STATE':<12}"
    print(colorize(header, Colors.BOLD + Colors.WHITE))

    for c in conns:
        pid_str = str(c.pid) if c.pid is not None else "-"
        raw_name = c.process_name if c.process_name else "-"
        if len(raw_name) >= _PROC_W:
            pname_str = raw_name[:_PROC_W - 2] + "\u2026"
        else:
            pname_str = raw_name
        local_str = f"{c.local_address}:{c.local_port}"
        remote_str = f"{c.remote_address}:{c.remote_port}" if c.remote_port is not None else c.remote_address

        state_color = Colors.GREEN if c.state == "ESTABLISHED" else (Colors.YELLOW if c.state == "LISTEN" else Colors.CYAN)
        state_str = colorize(c.state, state_color)

        line = f"{pid_str:<8}{pname_str:<{_PROC_W}}{local_str:<28}{remote_str:<28}{state_str}"
        print(line)

    print()
    print(f"{len(conns)} connection(s) found.")
    print()
    return EXIT_OK


def handle_doctor(args: argparse.Namespace, inspector: BaseInspector) -> int:
    """
    Read-only environment-wide diagnostic report.

    Aggregates existing inspection capabilities to help a developer understand
    what is running on their machine and whether anything unusual is present.

    Sections:
      1. Platform & Capabilities
      2. Listener Health
      3. Connection Summary
      4. Process / Service Context
      5. Project Context (per-process)
      6. Docker
      7. Findings (OBSERVATION / INFERENCE / WARNING / RECOMMENDATION)
    """
    use_json = getattr(args, "json", False)
    data = _run_doctor_data(inspector)
    platform_info = data["platform"]
    capabilities = data["capabilities"]
    listeners_out = data["listeners"]
    conn_summary = data["connection_summary"]
    proc_entries = data["processes"]
    docker_section = data["docker"]
    findings = data["findings"]

    # ------------------------------------------------------------------
    # JSON output
    # ------------------------------------------------------------------
    if use_json:
        print(_json_out("doctor", data))
        return EXIT_OK

    # ------------------------------------------------------------------
    # Human-readable output
    # ------------------------------------------------------------------
    print(colorize("=== KPORT DOCTOR ===", Colors.CYAN + Colors.BOLD))
    print()

    # Section 1 — Platform
    print(colorize("PLATFORM & CAPABILITIES", Colors.BOLD + Colors.WHITE))
    print(f"  OS:        {platform_info.get('os', 'unknown')} {platform_info.get('os_version', '')[:48].strip()}")
    print(f"  Backend:   {platform_info.get('inspector_backend', 'unknown')}")
    for note in capabilities.get("notes", []):
        print(f"  Note:      {note}")
    for lim in capabilities.get("limitations", []):
        print(colorize(f"  Limit:     {lim}", Colors.YELLOW))
    print()

    # Section 2 — Listeners
    print(colorize("LISTENERS", Colors.BOLD + Colors.WHITE))
    if not listeners_out:
        print("  No listening ports detected.")
    else:
        print(f"  {'PORT':<8}{'PROTO':<7}{'ADDRESS':<26}{'PID':<8}{'PROCESS'}")
        for ln in sorted(listeners_out, key=lambda x: (x["port"], x.get("proto", ""))):
            flag = colorize(" [PUBLIC]", Colors.YELLOW) if ln.get("wildcard") else ""
            pname = ln.get("process_name") or "-"
            pid_s = str(ln.get("pid") or "-")
            proto_raw = (ln.get("proto") or "tcp").upper()
            proto_badge = colorize(f"{proto_raw:<6}", Colors.BLUE if proto_raw == "TCP" else Colors.MAGENTA)
            print(f"  {ln['port']:<8}{proto_badge} {ln['address']:<26}{pid_s:<8}{pname}{flag}")
    print()

    # Section 3 — Connections
    print(colorize("CONNECTION SUMMARY", Colors.BOLD + Colors.WHITE))
    print(f"  Total:       {conn_summary['total']}")
    print(f"  LISTEN:      {conn_summary['LISTEN']}")
    print(f"  ESTABLISHED: {conn_summary['ESTABLISHED']}")
    print(f"  TIME_WAIT:   {conn_summary['TIME_WAIT']}")
    print(f"  CLOSE_WAIT:  {conn_summary['CLOSE_WAIT']}")
    if conn_summary["other"]:
        print(f"  Other:       {conn_summary['other']}")
    print()

    # Section 4 & 5 — Processes / Services / Projects
    print(colorize("PROCESS & PROJECT CONTEXT", Colors.BOLD + Colors.WHITE))
    if not proc_entries:
        print("  No process context available.")
    else:
        for p in proc_entries:
            sm = p.get("service_manager")
            proj = p.get("project")
            sm_label = f" [{sm['manager']}:{sm['name']}]" if sm else ""
            print(f"  PID {p['pid']} ({p['name']}){sm_label}")
            if p.get("user"):
                print(f"    User:    {p['user']}")
            if p.get("cwd"):
                print(f"    CWD:     {p['cwd']}")
            if proj:
                branch = f" ({proj['branch']})" if proj.get("branch") else ""
                origin = f" — {proj['remote_origin']}" if proj.get("remote_origin") else ""
                print(f"    Project: {proj['project_name'] or proj['git_root']}{branch}{origin}")
            else:
                print("    Project: none detected")
    print()

    # Section 6 — Docker
    print(colorize("DOCKER", Colors.BOLD + Colors.WHITE))
    if not docker_section.get("available"):
        print("  Docker CLI not found — skipped.")
    elif not docker_section.get("daemon_accessible", True):
        print("  Docker CLI found but daemon is not accessible (not running or permission denied).")
    elif not docker_section.get("containers"):
        print("  Docker is available but no containers with host-port mappings found.")
    else:
        for c in docker_section["containers"]:
            print(f"  {c['container_name']} ({c['image']})  host:{c['host_port']} → container:{c['container_port']}/{c['proto']}")
    print()

    # Section 7 — Findings
    print(colorize("FINDINGS", Colors.BOLD + Colors.WHITE))
    _SEV_COLOR = {
        "INFO": Colors.CYAN,
        "WARNING": Colors.YELLOW,
        "RISK": Colors.RED,
        "RECOMMENDATION": Colors.GREEN,
    }
    if not findings:
        print(colorize("  No issues found. Environment looks healthy.", Colors.GREEN))
    else:
        for f in findings:
            sev = f.get("severity", "INFO")
            color = _SEV_COLOR.get(sev, Colors.WHITE)
            cat = f.get("category", "")
            label = f"[{sev}]" if not cat else f"[{sev}/{cat.upper()}]"
            print(colorize(f"  {label} {f['message']}", color))
    print()

    return EXIT_OK


def handle_inspect_pid_cli(args: argparse.Namespace, inspector: BaseInspector) -> int:
    pid = args.pid
    info = inspector.get_process_info(pid)
    if not info:
        if getattr(args, "json", False):
            print(_json_out(args.command or "inspect", {"pid": pid, "error": f"Process {pid} not found or details unavailable"}))
        else:
            print(colorize(f"Error: Process {pid} not found or details unavailable", Colors.RED), file=sys.stderr)
        return EXIT_INVALID_INPUT

    # Find port bindings matching this PID.
    # On Linux without root, list_listening() may not resolve the PID for
    # root-owned sockets (inode→PID map requires fd access).
    # find_bindings_for_pid() applies a /proc/<pid>/net fallback in that case.
    pid_bindings = inspector.find_bindings_for_pid(pid)

    if getattr(args, "json", False):
        bindings_json = []
        for b in pid_bindings:
            bindings_json.append({
                "port": b.port,
                "proto": b.proto,
                "state": b.state or "LISTEN",
                "laddr": b.laddr
            })
        out = {
            "pid": pid,
            "type": "process",
            "process": asdict(info) if info else None,
            "active_ports": bindings_json
        }
        print(_json_out(args.command or "inspect", out))
    else:
        print(colorize(f"\n🔍 Inspecting PID {pid}\n", Colors.CYAN + Colors.BOLD))
        print(f"PID:         {pid}")
        print(f"Process:     {info.name}")
        if info.cmdline:
            print(f"Command:     {' '.join(info.cmdline)}")
        if info.user:
            print(f"User:        {info.user}")
        if info.ppid:
            print(f"Parent PID:  {info.ppid}")
        if info.cwd:
            print(f"CWD:         {info.cwd}")
        
        print("\nActive Port Bindings:")
        if not pid_bindings:
            print("  None")
        else:
            for b in pid_bindings:
                state_str = f" ({b.state})" if b.state else ""
                print(f"  - Port {b.port} ({b.proto}{state_str})")
        print()

    return EXIT_OK


def handle_kill_pid_cli(args: argparse.Namespace, inspector: BaseInspector) -> int:
    pid = args.pid
    info = inspector.get_process_info(pid)
    if not info:
        if getattr(args, "json", False):
            print(_json_out(args.command or "kill", {"pid": pid, "success": False, "message": f"Process {pid} not found"}))
        else:
            print(colorize(f"Error: Process {pid} not found", Colors.RED), file=sys.stderr)
        return EXIT_INVALID_INPUT

    # Check safety policy
    safe, safety_msg = check_safety_policy(None, [pid], args, inspector)
    if not safe:
        if getattr(args, "json", False):
            print(_json_out(args.command or "kill", {"pid": pid, "success": False, "message": safety_msg}))
        else:
            print(colorize(safety_msg, Colors.RED), file=sys.stderr)
        return EXIT_PERMISSION

    # Confirm unless yes/assume_yes is specified
    assume_yes = getattr(args, "yes", False)
    if not assume_yes and not getattr(args, "json", False):
        print(colorize("Action plan:", Colors.CYAN))
        print(colorize(f"1. Terminate local PID: {pid} ({info.name})", Colors.CYAN))
        if not confirm_prompt("Proceed?", assume_yes=False):
            print(colorize("Operation cancelled.", Colors.YELLOW))
            return EXIT_GENERAL_ERROR

    kill_tree = getattr(args, "kill_tree", False)
    dry_run = getattr(args, "dry_run", False)
    
    if kill_tree:
        ok, msg = inspector.kill_process_tree(
            pid,
            graceful_timeout=_resolve_timeout(args),
            force=getattr(args, "force", False),
            dry_run=dry_run,
            assume_yes=assume_yes,
            confirm_fn=confirm_prompt,
        )
    else:
        ok, msg = inspector.kill_pid(
            pid,
            graceful_timeout=_resolve_timeout(args),
            force=getattr(args, "force", False),
            dry_run=dry_run,
            assume_yes=assume_yes,
            confirm_fn=confirm_prompt,
        )

    # Log audit event
    audit.log_kill_pid(
        pid, info.name, dry_run=dry_run, success=ok, message=msg
    )

    if getattr(args, "json", False):
        print(_json_out(args.command or "kill", {
            "pid": pid,
            "success": ok,
            "message": msg,
            "dry_run": dry_run
        }))
    else:
        if ok:
            print(colorize(f"✓ PID {pid}: {msg}", Colors.GREEN))
        else:
            print(colorize(f"✗ PID {pid}: {msg}", Colors.RED))

    return EXIT_OK if ok else EXIT_GENERAL_ERROR


def _select_pids_interactively(
    pids: list[int], pname: str, args: argparse.Namespace, inspector: BaseInspector
) -> list[int] | None:
    # Resolve ports for each PID using find_bindings_for_pid so that the
    # Linux /proc/<pid>/net fallback is applied for root-owned processes whose
    # sockets are not visible in the global inode→PID map without privileges.
    pid_to_ports: dict[int, list[int]] = {}
    for pid in pids:
        try:
            bindings = inspector.find_bindings_for_pid(pid)
            if bindings:
                pid_to_ports[pid] = [b.port for b in bindings]
        except (OSError, AttributeError, ValueError):
            pass

    print(colorize(f"Found {len(pids)} process(es) matching '{pname}':", Colors.YELLOW))
    for idx, pid in enumerate(pids, 1):
        info = inspector.get_process_info(pid)
        ports_held = pid_to_ports.get(pid, [])
        ports_str = f"ports: {', '.join(map(str, sorted(set(ports_held))))}" if ports_held else "no active ports"
        p_display = f"[{idx}] PID {pid}: {info.name if info else 'Unknown'} ({ports_str})"
        print(colorize(f"  {p_display}", Colors.WHITE))

    # If --yes is specified or non-interactive (no TTY), default to all
    if getattr(args, "yes", False) or not sys.stdin.isatty() or not sys.stdout.isatty():
        return pids

    try:
        inp = input(
            colorize(
                "\nSelect processes to kill (indices/comma-separated, 'all' to kill all, 'q' to cancel) [default: all]: ",
                Colors.YELLOW,
            )
        ).strip()
        if inp.lower() in ("q", "quit", "none", "n", "no", "cancel"):
            return None
        
        if not inp or inp.lower() in ("all", "y", "yes"):
            return pids
        
        selected_pids = []
        for token in inp.split(","):
            token = token.strip()
            if token.isdigit():
                val = int(token)
                if 1 <= val <= len(pids):
                    selected_pids.append(pids[val - 1])
        
        if not selected_pids:
            print(colorize("No valid selection.", Colors.YELLOW))
            return None
        
        return selected_pids
    except (KeyboardInterrupt, EOFError):
        print()
        return None


def handle_hold(args: argparse.Namespace, inspector: BaseInspector) -> int:
    """Hold / reserve a port by binding sockets across IPv4 and IPv6 until user cancels or timeout occurs."""
    import os as _os
    import select
    import socket
    import time

    port = args.port
    validate_port(port)

    proto = getattr(args, "proto", "tcp").lower()
    if proto not in ("tcp", "udp", "both"):
        proto = "tcp"

    pids = inspector.find_pids_on_port(port, proto=proto)
    bindings = inspector.find_bindings_on_port(port, proto=proto)
    docker_hits = docker_mappings_for_host_port(port)

    if pids or bindings or docker_hits:
        proto_msg = f" on {proto.upper()}" if proto != "both" else ""
        msg = f"Cannot hold port {port}: port is currently occupied{proto_msg}."
        if getattr(args, "json", False):
            print(_json_out("hold", {"port": port, "held": False, "error": msg}))
        else:
            print(colorize(f"\u26a0  {msg}", Colors.RED), file=sys.stderr)
            # Show who currently holds the port (grouped by PID)
            if bindings:
                by_pid: dict = {}
                for b in bindings:
                    by_pid.setdefault(b.pid, []).append(b)
                for pid, blist in by_pid.items():
                    pid_str = f"PID {pid}" if pid else "(unknown PID)"
                    pname = blist[0].process_name or "?"
                    proto_tag = f"[{(blist[0].proto or 'tcp').upper()}]"
                    addrs = ", ".join(dict.fromkeys(b.laddr or f"*:{port}" for b in blist))
                    print(
                        colorize(f"   {proto_tag} {pid_str}  {pname}  @ {addrs}", Colors.YELLOW),
                        file=sys.stderr,
                    )
            elif docker_hits:
                for m in docker_hits:
                    print(
                        colorize(f"   [Docker] container: {m.container_name}", Colors.YELLOW),
                        file=sys.stderr,
                    )
            print(
                colorize(f"   Tip: Run 'kport inspect {port}' for full details.", Colors.CYAN),
                file=sys.stderr,
            )
        return EXIT_GENERAL_ERROR

    held_socks: list[socket.socket] = []
    held_addresses: list[str] = []
    protos_to_bind = ["tcp", "udp"] if proto == "both" else [proto]

    _SO_EXCL = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)

    def _make_exclusive(sock: socket.socket) -> None:
        """Apply SO_EXCLUSIVEADDRUSE on Windows. Never set SO_REUSEADDR (allows sharing on Windows)."""
        if sys.platform == "win32" and _SO_EXCL is not None:
            sock.setsockopt(socket.SOL_SOCKET, _SO_EXCL, 1)

    def _bind_socket(family: int, sock_type: int, addr: tuple, listen: bool) -> None:
        """Create, configure, bind (and optionally listen) a socket; track in held_socks."""
        s = socket.socket(family, sock_type)
        s.set_inheritable(False)
        try:
            if family == socket.AF_INET6:
                s.setsockopt(socket.IPPROTO_IPV6, getattr(socket, "IPV6_V6ONLY", 27), 1)
            _make_exclusive(s)
            s.bind(addr)
            if listen:
                s.listen(1)
            held_socks.append(s)
            held_addresses.append(f"{addr[0]}:{addr[1]}")
        except OSError:
            s.close()
            raise

    try:
        for p in protos_to_bind:
            sock_type = socket.SOCK_STREAM if p == "tcp" else socket.SOCK_DGRAM
            listen = p == "tcp"

            # 1. Bind IPv4 wildcard — mandatory
            try:
                _bind_socket(socket.AF_INET, sock_type, ("0.0.0.0", port), listen)
            except OSError as err:
                for s in held_socks:
                    s.close()
                msg = f"Failed to reserve port {port} (IPv4/{p.upper()}): {err}"
                if getattr(args, "json", False):
                    print(_json_out("hold", {"port": port, "held": False, "error": msg}))
                else:
                    print(colorize(f"Error: {msg}", Colors.RED), file=sys.stderr)
                return EXIT_PERMISSION if "permission" in str(err).lower() else EXIT_GENERAL_ERROR

            # 2. Bind IPv6 wildcard — mandatory to block dual-stack bypass
            if getattr(socket, "has_ipv6", False):
                try:
                    _bind_socket(socket.AF_INET6, sock_type, ("::", port), listen)
                except OSError as err:
                    # Only skip if IPv6 is genuinely absent on this system
                    winerr = getattr(err, "winerror", None)
                    errno_code = getattr(err, "errno", None)
                    ipv6_absent = winerr in (10047,) or errno_code in (47, 43)  # WSAEAFNOSUPPORT
                    if not ipv6_absent:
                        for s in held_socks:
                            s.close()
                        msg = f"Failed to reserve port {port} (IPv6/{p.upper()}): {err}"
                        if getattr(args, "json", False):
                            print(_json_out("hold", {"port": port, "held": False, "error": msg}))
                        else:
                            print(colorize(f"Error: {msg}", Colors.RED), file=sys.stderr)
                        return EXIT_PERMISSION if "permission" in str(err).lower() else EXIT_GENERAL_ERROR

    except OSError as e:
        for s in held_socks:
            s.close()
        msg = f"Failed to reserve port {port}: {e}"
        if getattr(args, "json", False):
            print(_json_out("hold", {"port": port, "held": False, "error": msg}))
        else:
            print(colorize(f"Error: {msg}", Colors.RED), file=sys.stderr)
        return EXIT_PERMISSION if "permission" in str(e).lower() else EXIT_GENERAL_ERROR

    # Post-bind intruder check: catch any process that raced us during initialization
    own_pid = _os.getpid()
    intruders = [
        pid for pid in inspector.find_pids_on_port(port, proto=proto)
        if pid is not None and pid != own_pid
    ]
    if intruders:
        for s in held_socks:
            s.close()
        msg = (
            f"Port {port} was claimed by another process (PID {', '.join(map(str, intruders))}) "
            "during reservation. Try again or kill that process first."
        )
        if getattr(args, "json", False):
            print(_json_out("hold", {"port": port, "held": False, "error": msg}))
        else:
            print(colorize(f"\u26a0  {msg}", Colors.YELLOW), file=sys.stderr)
        return EXIT_GENERAL_ERROR

    timeout = getattr(args, "timeout", None)
    timeout_str = f" for {timeout}s" if timeout else ""
    proto_label = "TCP & UDP" if proto == "both" else proto.upper()
    addrs_label = "  ".join(
        colorize(a, Colors.WHITE + Colors.BOLD) for a in held_addresses
    ) if held_addresses else colorize(f"*:{port}", Colors.WHITE)

    if getattr(args, "json", False):
        print(_json_out("hold", {
            "port": port,
            "proto": proto,
            "held": True,
            "addresses": held_addresses,
            "timeout": timeout,
        }))

    print(colorize(
        f"🔒 Port {port} ({proto_label}) is reserved{timeout_str}.  ",
        Colors.CYAN + Colors.BOLD,
    ))
    print(f"   Holding:  {addrs_label}")
    print(colorize("   Press Enter or Ctrl+C to release...", Colors.CYAN))

    try:
        if timeout:
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    if sys.stdin in select.select([sys.stdin], [], [], 0.2)[0]:
                        sys.stdin.readline()
                        break
                except KeyboardInterrupt:
                    break
        else:
            try:
                input()
            except (EOFError, KeyboardInterrupt):
                pass
    except Exception:  # noqa: BLE001, S110 - best-effort socket cleanup
        pass
    finally:
        for s in held_socks:
            try:
                s.close()
            except Exception:
                pass
        print(colorize(f"\n🔓 Port {port} ({proto_label}) released.", Colors.GREEN))

    return EXIT_OK

def handle_audit(args: argparse.Namespace) -> int:
    """Display recent audit log history."""
    limit = getattr(args, "limit", 20)
    entries = audit.read_recent_audit_events(limit=limit)

    if getattr(args, "json", False):
        print(_json_out("audit", {"entries": entries}))
        return EXIT_OK

    if not entries:
        print(colorize("No audit log entries found in ~/.kport/audit.log", Colors.YELLOW))
        return EXIT_OK

    print(
        colorize(
            f"=== kport Audit History (last {len(entries)} events) ===",
            Colors.CYAN + Colors.BOLD,
        )
    )
    for entry in entries:
        ts = entry.get("ts", "")[:19].replace("T", " ")
        action = entry.get("action", "unknown")
        user = entry.get("user", "unknown")
        target = entry.get("target", {})
        msg = entry.get("message", "")
        status = (
            colorize("SUCCESS", Colors.GREEN)
            if entry.get("success")
            else colorize("FAILED", Colors.RED)
        )
        if entry.get("dry_run"):
            status += colorize(" [DRY-RUN]", Colors.YELLOW)
        print(f"[{ts}] {action.upper()} by {user} — {status}")
        print(f"  Target: {target}")
        print(f"  Details: {msg}\n")

    return EXIT_OK


def handle_product_command(args: argparse.Namespace, inspector: BaseInspector) -> int:
    """Implement subcommands defined in the product specification."""
    debug = bool(getattr(args, "debug", False))

    if args.command == "setup-sudo":
        target = getattr(args, "target", "/usr/local/bin/kport")
        return handle_setup_sudo(target=target)

    if args.command == "hold":
        return handle_hold(args, inspector)

    if args.command == "audit":
        return handle_audit(args)

    if args.command == "diagnose":
        return handle_diagnose(args, inspector)

    if args.command == "stop-service":
        return handle_stop_service(args, inspector)

    if args.command == "doctor":
        return handle_doctor(args, inspector)

    if args.command == "connections":
        return handle_connections(args, inspector)

    if args.command == "docker":
        extra = getattr(args, "extra", []) or []
        # Separate numeric args (port filters) from non-numeric (unknown subcommands).
        port_filters: list[int] = []
        unknown_args: list[str] = []
        for token in extra:
            try:
                port_filters.append(int(token))
            except ValueError:
                unknown_args.append(token)

        if unknown_args:
            print(
                colorize(
                    f"Note: 'kport docker' has no subcommands. Ignoring: {' '.join(unknown_args)}",
                    Colors.YELLOW,
                ),
                file=sys.stderr,
            )

        maps = list_docker_mappings(debug=debug)
        if port_filters:
            maps = [m for m in maps if m.host_port in port_filters]
            if not maps and not args.json:
                ports_str = ", ".join(str(p) for p in port_filters)
                print(
                    colorize(
                        f"No Docker-published ports found matching port(s): {ports_str}",
                        Colors.YELLOW,
                    )
                )

        if args.json:
            print(_json_out("docker", [asdict(m) for m in maps]))
        else:
            print_table_docker(maps)
        return EXIT_OK

    if args.command == "list":
        local = inspector.list_listening(proto=getattr(args, "proto", "both"))
        docker_maps = list_docker_mappings(debug=debug)
        if args.json:
            print(
                _json_out(
                    "list",
                    {
                        "local": [asdict(b) for b in local],
                        "docker": [asdict(m) for m in docker_maps],
                    },
                )
            )
        else:
            print_table_list_product(local, docker_maps)
        return EXIT_OK

    if args.command == "inspect":
        if getattr(args, "pid", None) is not None:
            return handle_inspect_pid_cli(args, inspector)
        ports = _resolve_ports_for_args(args)
        if not ports:
            print(
                colorize("Error: inspect requires a port or a --profile", Colors.RED),
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT

        results = []
        exit_code = EXIT_PORT_FREE
        for port in ports:
            validate_port(port)
            local_bindings = inspector.find_bindings_on_port(
                port, proto=getattr(args, "proto", "both")
            )
            docker_hits = docker_mappings_for_host_port(port, debug=debug)
            pids = inspector.find_pids_on_port(
                port, proto=getattr(args, "proto", "both")
            )

            if docker_hits:
                m = docker_hits[0]
                payload = {
                    "port": port,
                    "type": "docker",
                    "container": m.container_name,
                    "image": m.image,
                    "host_port": m.host_port,
                    "container_port": m.container_port,
                    "status": m.status,
                }
                results.append(payload)
                if not args.json:
                    print(colorize(f"Port: {port}", Colors.CYAN + Colors.BOLD))
                    print("Type: Docker Container")
                    print(f"Container: {m.container_name}")
                    print(f"Image: {m.image}")
                    print(f"Host Port: {m.host_port}")
                    print(f"Container Port: {m.container_port}")
                    print(f"Status: {m.status}")
                    print()
                exit_code = EXIT_PORT_DOCKER
            elif not pids and not local_bindings:
                payload = {"port": port, "type": "free"}
                results.append(payload)
                if not args.json:
                    print(colorize(f"Port {port} is free", Colors.GREEN))
                    print()
            elif not pids and local_bindings:
                msg = "Port is in use, but the owning PID is not visible (try running with sudo/admin)."
                payload = {
                    "port": port,
                    "type": "local-unknown",
                    "message": msg,
                    "bindings": [asdict(b) for b in local_bindings],
                }
                results.append(payload)
                if not args.json:
                    print(colorize(f"Port: {port}", Colors.CYAN + Colors.BOLD))
                    print("Type: Local Process")
                    print(colorize(msg, Colors.YELLOW))
                    print()
                if exit_code not in (EXIT_PORT_DOCKER, EXIT_OK):
                    exit_code = EXIT_OK
            else:
                info_list = []
                for b in local_bindings:
                    pid = b.pid
                    info = inspector.get_process_info(pid) if pid else None
                    info_list.append({
                        "pid": pid,
                        "proto": b.proto or "tcp",
                        "process": asdict(info) if info else None,
                    })
                payload = {"port": port, "type": "local", "pids": info_list}
                results.append(payload)
                if not args.json:
                    print_port_bindings_detail(
                        port, local_bindings, inspector,
                        header=f"Port {port} — Local Process(es):",
                    )
                    print()
                if exit_code != EXIT_PORT_DOCKER:
                    exit_code = EXIT_OK

        if args.json:
            if len(ports) == 1:
                print(_json_out("inspect", results[0]))
            else:
                print(_json_out("inspect", {"ports": results}))
        return exit_code

    if args.command == "explain":
        validate_port(args.port)
        # Check Docker FIRST — Docker awareness does not require elevated privileges.
        docker_hits = docker_mappings_for_host_port(args.port, debug=debug)
        if docker_hits:
            m = docker_hits[0]
            if args.json:
                print(
                    _json_out(
                        "explain",
                        {
                            "port": args.port,
                            "blocked": True,
                            "because": [
                                f"It is mapped to Docker container '{m.container_name}'",
                                f"Docker maps host port {m.host_port} \u2192 container port {m.container_port}",
                                "The process runs inside an isolated network namespace",
                            ],
                            "suggested_actions": [
                                {
                                    "action": "docker_stop",
                                    "port": args.port,
                                    "container": m.container_name,
                                    "requires_confirmation": True,
                                    "safe": True,
                                    "command": f"kport kill {args.port} --docker-action stop --yes",
                                },
                                {
                                    "action": "docker_restart",
                                    "port": args.port,
                                    "container": m.container_name,
                                    "requires_confirmation": True,
                                    "safe": True,
                                    "command": f"kport kill {args.port} --docker-action restart --yes",
                                },
                            ],
                        },
                    )
                )
            else:
                print(
                    colorize(
                        f"Port {args.port} is unavailable because:",
                        Colors.YELLOW + Colors.BOLD,
                    )
                )
                print(f'- It is mapped to Docker container "{m.container_name}"')
                print(
                    f"- Docker maps host port {m.host_port} \u2192 container port {m.container_port}"
                )
                print("- The process runs inside an isolated network namespace")
            return EXIT_PORT_DOCKER

        local_bindings = inspector.find_bindings_on_port(
            args.port, proto=getattr(args, "proto", "both")
        )
        pids = inspector.find_pids_on_port(
            args.port, proto=getattr(args, "proto", "both")
        )

        if not pids and not local_bindings:
            # Before declaring free: check raw /proc/net/tcp for a socket on this port.
            # This catches the snap/container case where:
            #   - docker_hits is empty (Docker daemon unreachable from snap sandbox)
            #   - local_bindings is empty (owning PID not visible without elevation)
            #   - but a listening socket IS present at the kernel level
            _raw_occupied = False
            import platform as _platform

            if _platform.system() == "Linux":
                try:
                    from .inspectors.system_impl import _parse_proc_net_file

                    for fname, fam in [
                        ("/proc/net/tcp", "IPv4"),
                        ("/proc/net/tcp6", "IPv6"),
                    ]:
                        for _ip, _port, _inode in _parse_proc_net_file(fname, fam):
                            if _port == args.port:
                                _raw_occupied = True
                                break
                        if _raw_occupied:
                            break
                except (OSError, ValueError, IndexError):
                    pass

            if _raw_occupied:
                # Port IS occupied at kernel level but PID/process is not visible.
                if args.json:
                    print(
                        _json_out(
                            "explain",
                            {
                                "port": args.port,
                                "blocked": True,
                                "type": "local-unknown",
                                "message": "A local process is listening, but the owning PID is not visible",
                                "because": [
                                    "A local process is listening, but the owning PID is not visible",
                                    "This is commonly due to missing privileges; try running with sudo",
                                ],
                                "suggested_actions": [
                                    {
                                        "action": "rerun_as_admin",
                                        "port": args.port,
                                        "requires_confirmation": False,
                                        "safe": True,
                                        "note": "Re-run kport with administrator/sudo privileges to see the owning PID",
                                    },
                                ],
                            },
                        )
                    )
                else:
                    print(
                        colorize(
                            f"Port {args.port} is unavailable because:",
                            Colors.YELLOW + Colors.BOLD,
                        )
                    )
                    print(
                        "- A local process is listening, but the owning PID is not visible"
                    )
                    print(
                        "- This is commonly due to missing privileges; try running with sudo"
                    )
                return EXIT_OK

            if args.json:
                print(
                    _json_out(
                        "explain",
                        {
                            "port": args.port,
                            "blocked": False,
                            "suggested_actions": [
                                {
                                    "action": "bind",
                                    "port": args.port,
                                    "requires_confirmation": False,
                                    "safe": True,
                                    "note": "Port is free \u2014 safe to bind",
                                },
                            ],
                        },
                    )
                )
            else:
                print(colorize(f"Port {args.port} is free", Colors.GREEN))
            return EXIT_PORT_FREE

        if not pids and local_bindings:
            if args.json:
                print(
                    _json_out(
                        "explain",
                        {
                            "port": args.port,
                            "blocked": True,
                            "type": "local-unknown",
                            "message": "Owning PID not visible (try sudo/admin)",
                            "bindings": [asdict(b) for b in local_bindings],
                            "suggested_actions": [
                                {
                                    "action": "rerun_as_admin",
                                    "port": args.port,
                                    "requires_confirmation": False,
                                    "safe": True,
                                    "note": "Re-run kport with administrator/sudo privileges to see the owning PID",
                                },
                            ],
                        },
                    )
                )
            else:
                print(
                    colorize(
                        f"Port {args.port} is unavailable because:",
                        Colors.YELLOW + Colors.BOLD,
                    )
                )
                print(
                    "- A local process is listening, but the owning PID is not visible"
                )
                print(
                    "- This is commonly due to missing privileges; try running with sudo"
                )
            return EXIT_OK

        # Local process explanation
        infos = []
        managed_by_list = []
        for pid in pids:
            info = inspector.get_process_info(pid)
            pm_info = detect_process_manager(pid)
            proc_dict = asdict(info) if info else None
            mb = pm_info["managed_by"] if pm_info else None
            if mb:
                managed_by_list.append(mb)
                if proc_dict:
                    proc_dict["managed_by"] = mb
            infos.append({"pid": pid, "process": proc_dict, "managed_by": mb})

        # Build safe suggested_actions (protected-port-aware)
        safe_kill = not any(
            p in DEFAULT_PROTECTED_PORTS for p in [args.port]
        ) and not any(
            (
                inspector.get_process_info(p)
                and inspector.get_process_info(p).name.lower().split(" (")[0]
                in DEFAULT_PROTECTED_PROCESS_NAMES
            )
            for p in pids
        )
        if args.json:
            print(
                _json_out(
                    "explain",
                    {
                        "port": args.port,
                        "blocked": True,
                        "type": "local",
                        "managed_by": managed_by_list[0]
                        if len(managed_by_list) == 1
                        else (managed_by_list if managed_by_list else None),
                        "pids": infos,
                        "suggested_actions": [
                            {
                                "action": "kill",
                                "port": args.port,
                                "requires_confirmation": True,
                                "safe": safe_kill,
                                "command": f"kport kill {args.port} --yes",
                                "note": None
                                if safe_kill
                                else "Port or process is protected — pass --bypass-safety to override",
                            },
                            {
                                "action": "dry_run",
                                "port": args.port,
                                "requires_confirmation": False,
                                "safe": True,
                                "command": f"kport kill {args.port} --dry-run --json",
                                "note": "Preview what would happen without executing",
                            },
                        ],
                    },
                )
            )
        else:
            print(
                colorize(
                    f"Port {args.port} is unavailable because:",
                    Colors.YELLOW + Colors.BOLD,
                )
            )
            for entry in infos:
                proc = entry["process"]
                mb = entry.get("managed_by")
                mb_str = f" (managed by {mb})" if mb else ""
                pid_protos = {
                    (b.proto or "tcp").upper()
                    for b in local_bindings
                    if b.pid == entry["pid"]
                }
                proto_tag = f" [{' / '.join(sorted(pid_protos))}]" if pid_protos else ""
                if proc:
                    print(
                        f"- PID {entry['pid']} ({proc.get('name')}){proto_tag} is listening{mb_str}"
                    )
                else:
                    print(f"- PID {entry['pid']}{proto_tag} is listening{mb_str}")
                if mb:
                    pm_info = detect_process_manager(entry["pid"])
                    if pm_info and pm_info.get("warning"):
                        print(colorize(f"  ⚠ {pm_info['warning']}", Colors.YELLOW))
        return EXIT_OK

    if args.command == "kill":
        if getattr(args, "pid", None) is not None:
            return handle_kill_pid_cli(args, inspector)
        ports = _resolve_ports_for_args(args)
        if not ports:
            print(
                colorize("Error: kill requires a port or a --profile", Colors.RED),
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT

        # For text output with multiple ports, confirm once if not yes
        if not args.json and not args.yes and len(ports) > 1:
            # We want to confirm proceeding with the action plan for all ports.
            pids_to_kill_all: list[int] = []
            docker_hits_all = []
            for port in ports:
                pids = inspector.find_pids_on_port(
                    port, proto=getattr(args, "proto", "both")
                )
                pids_to_kill_all.extend(pids)
                dh = docker_mappings_for_host_port(port, debug=debug)
                if dh:
                    docker_hits_all.append(dh[0])

            # Print Action Plan
            print(colorize("Action plan:", Colors.CYAN))
            if pids_to_kill_all:
                print(
                    colorize(
                        f"1. Terminate local PIDs: {', '.join(map(str, sorted(set(pids_to_kill_all))))}",
                        Colors.CYAN,
                    )
                )
            if docker_hits_all:
                action = getattr(args, "docker_action", None) or "stop"
                containers_str = ", ".join(m.container_name for m in docker_hits_all)
                print(
                    colorize(
                        f"2. Perform Docker action '{action}' on containers: {containers_str}",
                        Colors.CYAN,
                    )
                )

            if not confirm_prompt("Proceed?", assume_yes=args.yes):
                print(colorize("Operation cancelled.", Colors.YELLOW))
                return EXIT_GENERAL_ERROR

        results = []
        overall_ok = True
        exit_codes = []
        for port in ports:
            validate_port(port)
            pids = inspector.find_pids_on_port(
                port, proto=getattr(args, "proto", "both")
            )

            # Check safety policy
            safe, safety_msg = check_safety_policy(port, pids, args, inspector)
            if not safe:
                if args.json:
                    results.append(
                        {"port": port, "success": False, "message": safety_msg}
                    )
                else:
                    print(
                        colorize(f"Port {port}: {safety_msg}", Colors.RED),
                        file=sys.stderr,
                    )
                overall_ok = False
                exit_codes.append(EXIT_PERMISSION)
                continue

            local_bindings = inspector.find_bindings_on_port(
                port, proto=getattr(args, "proto", "both")
            )
            docker_hits = docker_mappings_for_host_port(port, debug=debug)
            if docker_hits:
                m = docker_hits[0]
                action = getattr(args, "docker_action", None)
                if not action and not args.json:
                    print(
                        colorize(
                            f"Port {port} belongs to Docker container: {m.container_name}",
                            Colors.YELLOW + Colors.BOLD,
                        )
                    )
                    action = choose_docker_action(assume_yes=args.yes)
                if not action:
                    if args.json:
                        results.append(
                            {
                                "port": port,
                                "type": "docker",
                                "container": m.container_name,
                                "container_id": m.container_id,
                                "available_actions": ["stop", "restart", "rm"],
                                "performed": None,
                                "message": "No action selected",
                            }
                        )
                    else:
                        print(colorize("Operation cancelled.", Colors.YELLOW))
                    overall_ok = False
                    exit_codes.append(EXIT_GENERAL_ERROR)
                    continue

                if args.json and not args.yes and not args.dry_run:
                    results.append(
                        {
                            "port": port,
                            "type": "docker",
                            "container": m.container_name,
                            "container_id": m.container_id,
                            "requested_action": action,
                            "performed": False,
                            "message": "Refusing to act without --yes in JSON mode",
                        }
                    )
                    overall_ok = False
                    exit_codes.append(EXIT_GENERAL_ERROR)
                    continue

                if (
                    action == "rm"
                    and not args.dry_run
                    and not confirm_docker_rm(
                        m.container_name,
                        m.container_id,
                        assume_yes=args.yes,
                        force=args.force,
                        image=getattr(m, "image", ""),
                        host_port=getattr(m, "host_port", port),
                        container_port=getattr(m, "container_port", None),
                    )
                ):
                        if args.json:
                            results.append(
                                {
                                    "port": port,
                                    "type": "docker",
                                    "container": m.container_name,
                                    "container_id": m.container_id,
                                    "action": "rm",
                                    "ok": False,
                                    "message": "Removing a Docker container is irreversible. Use --force in addition to --yes to bypass interactive confirmation.",
                                }
                            )
                        overall_ok = False
                        exit_codes.append(EXIT_PERMISSION)
                        continue

                ok, msg = docker_action_on_container(
                    m.container_id, action=action, dry_run=args.dry_run, debug=debug
                )
                # Audit log for docker action
                audit.log_docker_action(
                    m.container_id,
                    m.container_name,
                    action,
                    dry_run=args.dry_run,
                    success=ok,
                    message=msg,
                )
                if args.json:
                    results.append(
                        {
                            "port": port,
                            "type": "docker",
                            "container": m.container_name,
                            "container_id": m.container_id,
                            "action": action,
                            "ok": ok,
                            "message": msg,
                        }
                    )
                else:
                    if ok:
                        print(colorize(f"✓ Port {port}: {msg}", Colors.GREEN))
                    else:
                        print(colorize(f"✗ Port {port}: {msg}", Colors.RED))
                if not ok:
                    overall_ok = False
                exit_codes.append(EXIT_OK if ok else EXIT_GENERAL_ERROR)
                continue

            # Local process kill - show per-protocol detail and confirm per-proto
            if not pids and not local_bindings:
                if args.json:
                    results.append(
                        {
                            "port": port,
                            "killed": [],
                            "failed": [],
                            "message": "Port free",
                        }
                    )
                else:
                    print(colorize(f"Port {port} is free", Colors.GREEN))
                exit_codes.append(EXIT_PORT_FREE)
                continue

            if not pids and local_bindings:
                msg = "Port is in use but PID is not visible; cannot kill safely without PID. Try sudo/admin."
                if args.json:
                    results.append(
                        {
                            "port": port,
                            "ok": False,
                            "message": msg,
                            "bindings": [asdict(b) for b in local_bindings],
                        }
                    )
                else:
                    print(colorize(f"Port {port}: {msg}", Colors.RED))
                overall_ok = False
                exit_codes.append(EXIT_PERMISSION)
                continue

            # Show what will be killed, grouped by protocol
            if not args.json:
                print_port_bindings_detail(
                    port, local_bindings, inspector,
                    header=f"Port {port} — processes to terminate:",
                )

            # Determine which protocols are present
            tcp_bindings = [b for b in local_bindings if (b.proto or "tcp").lower() == "tcp"]
            udp_bindings = [b for b in local_bindings if (b.proto or "tcp").lower() == "udp"]
            requested_proto = getattr(args, "proto", "both")

            # Per-protocol confirmation and selection
            protos_to_kill: list[str] = []
            if requested_proto == "both" and tcp_bindings and udp_bindings and not args.json and not args.yes:
                tcp_pids = sorted({b.pid for b in tcp_bindings if b.pid})
                udp_pids = sorted({b.pid for b in udp_bindings if b.pid})
                print()
                print(colorize(f"Port {port} has dual-protocol listeners. Select what to kill:", Colors.BOLD))
                print(f"  [1] Both (TCP & UDP)")
                print(f"  [2] TCP only (PID {', '.join(map(str, tcp_pids))})")
                print(f"  [3] UDP only (PID {', '.join(map(str, udp_pids))})")
                print(f"  [q] Cancel")
                print()
                print(colorize(f"Tip: You can target directly next time with:\n  kport kill {port} --proto=tcp\n  kport kill {port} --proto=udp", Colors.CYAN))
                print()
                try:
                    choice = input(colorize("Choice [1/2/3/q]: ", Colors.CYAN + Colors.BOLD)).strip().lower()
                except (EOFError, KeyboardInterrupt):
                    choice = "q"
                    print()

                if choice in ("1", "both", "b"):
                    protos_to_kill = ["both"]
                elif choice in ("2", "tcp", "t"):
                    protos_to_kill = ["tcp"]
                elif choice in ("3", "udp", "u"):
                    protos_to_kill = ["udp"]
                else:
                    print(colorize("Operation cancelled.", Colors.YELLOW))
                    exit_codes.append(EXIT_GENERAL_ERROR)
                    overall_ok = False
                    continue
            elif not args.json and not args.yes:
                active_proto = "TCP" if tcp_bindings and not udp_bindings else (
                    "UDP" if udp_bindings and not tcp_bindings else requested_proto.upper()
                )
                active_bindings = tcp_bindings or udp_bindings or local_bindings
                active_pids = sorted({b.pid for b in active_bindings if b.pid})
                pids_str = ", ".join(map(str, active_pids))
                print()
                kill_confirmed = confirm_prompt(
                    f"Kill {active_proto} listener(s) on port {port} (PID {pids_str})? [{active_proto}]",
                    assume_yes=False,
                )
                if not kill_confirmed:
                    print(colorize("Operation cancelled.", Colors.YELLOW))
                    exit_codes.append(EXIT_GENERAL_ERROR)
                    overall_ok = False
                    continue
                protos_to_kill = [requested_proto if requested_proto in ("tcp", "udp") else active_proto.lower()]
            else:
                protos_to_kill = [requested_proto]

            for kill_proto in protos_to_kill:
                ok, msg = inspector.kill_port(
                    port,
                    graceful_timeout=_resolve_timeout(args),
                    force=args.force,
                    dry_run=args.dry_run,
                    debug=debug,
                    assume_yes=args.yes,
                    kill_tree=getattr(args, "kill_tree", False),
                    proto=kill_proto,
                    confirm_fn=confirm_prompt,
                )

                # Audit log
                audit.log_kill_port(
                    port, pids, dry_run=args.dry_run, success=ok, message=msg
                )

                if args.json:
                    results.append(
                        {
                            "port": port,
                            "proto": kill_proto,
                            "success": ok,
                            "message": msg,
                            "pids_targeted": pids,
                            "dry_run": args.dry_run,
                        }
                    )
                else:
                    proto_label = f" [{kill_proto.upper()}]" if kill_proto in ("tcp", "udp") else (" [TCP/UDP]" if kill_proto == "both" else "")
                    if ok:
                        print(colorize(f"✓ Port {port}{proto_label} freed: {msg}", Colors.GREEN))
                    else:
                        print(colorize(f"✗ Port {port}{proto_label}: {msg}", Colors.RED), file=sys.stderr)
                if not ok:
                    overall_ok = False
                exit_codes.append(EXIT_OK if ok else EXIT_GENERAL_ERROR)

        if getattr(args, "wait_for_exit", None) is not None and not args.dry_run:
            timeout = args.wait_for_exit
            wait_results = {}
            for i, port in enumerate(ports):
                if exit_codes[i] in (EXIT_OK, EXIT_PORT_FREE):
                    if not args.json:
                        print(
                            colorize(
                                f"⌛ Waiting for port {port} to be free (timeout {timeout}s)...",
                                Colors.WHITE,
                            )
                        )
                    wait_ok = _poll_until_free(port, timeout, inspector)
                    wait_results[port] = wait_ok
                    if not wait_ok:
                        overall_ok = False
                        exit_codes[i] = EXIT_GENERAL_ERROR
                        if not args.json:
                            print(
                                colorize(
                                    f"⏱ Process did not exit within {timeout}s",
                                    Colors.RED,
                                ),
                                file=sys.stderr,
                            )
                    else:
                        if not args.json:
                            print(colorize(f"✓ Port {port} is now free.", Colors.GREEN))
            for r in results:
                p = r.get("port")
                if p in wait_results:
                    r["wait_for_exit_ok"] = wait_results[p]

        if args.json:
            if len(ports) == 1:
                print(_json_out("kill", results[0]))
            else:
                print(_json_out("kill", {"ports": results}))

        if len(ports) == 1:
            return exit_codes[0]
        return EXIT_OK if overall_ok else EXIT_GENERAL_ERROR

    if args.command == "kill-process":
        pname = args.name
        pids = inspector.find_pids_by_name(pname, exact=args.exact)
        if not pids:
            if args.json:
                print(_json_out("kill-process", {"name": pname, "pids": []}))
            else:
                print(colorize(f"✗ No processes found matching '{pname}'", Colors.RED))
            return EXIT_OK

        # Check safety policy
        safe, safety_msg = check_safety_policy(None, pids, args, inspector)
        if not safe:
            if args.json:
                print(
                    _json_out(
                        "kill-process",
                        {"name": pname, "success": False, "message": safety_msg},
                    )
                )
            else:
                print(colorize(safety_msg, Colors.RED), file=sys.stderr)
            return EXIT_PERMISSION

        if not args.json:
            target_pids = _select_pids_interactively(pids, pname, args, inspector)
            if target_pids is None:
                print(colorize("Operation cancelled.", Colors.YELLOW))
                return EXIT_GENERAL_ERROR
        else:
            target_pids = pids

        killed = []
        failed = []
        for pid in target_pids:
            if getattr(args, "kill_tree", False):
                ok, msg = inspector.kill_process_tree(
                    pid,
                    graceful_timeout=_resolve_timeout(args),
                    force=args.force,
                    dry_run=args.dry_run,
                    assume_yes=args.yes,
                    confirm_fn=confirm_prompt,
                )
            else:
                ok, msg = inspector.kill_pid(
                    pid,
                    graceful_timeout=_resolve_timeout(args),
                    force=args.force,
                    dry_run=args.dry_run,
                    assume_yes=args.yes,
                    confirm_fn=confirm_prompt,
                )

            # Audit log
            audit.log_kill_pid(
                pid, pname, dry_run=args.dry_run, success=ok, message=msg
            )

            if ok:
                killed.append({"pid": pid, "msg": msg})
            else:
                failed.append({"pid": pid, "msg": msg})

        if args.json:
            print(_json_out("kill-process", {"killed": killed, "failed": failed}))
        else:
            for k in killed:
                print(colorize(f"✓ Killed PID {k['pid']} ({k['msg']})", Colors.GREEN))
            for f in failed:
                print(colorize(f"✗ Failed PID {f['pid']} ({f['msg']})", Colors.RED))

        return EXIT_OK if not failed else EXIT_GENERAL_ERROR

    if args.command == "conflicts":
        docker_maps = list_docker_mappings(debug=debug)
        conflicts = []
        for m in docker_maps:
            pids = inspector.find_pids_on_port(m.host_port)
            non_docker_pids = []
            for pid in pids:
                info = inspector.get_process_info(pid)
                pname = (info.name if info else "").lower()
                if "docker-proxy" in pname or pname.startswith("docker"):
                    continue
                non_docker_pids.append(
                    {"pid": pid, "process": asdict(info) if info else None}
                )
            if non_docker_pids:
                conflicts.append(
                    {
                        "port": m.host_port,
                        "docker": asdict(m),
                        "local": non_docker_pids,
                    }
                )
        if args.json:
            print(_json_out("conflicts", conflicts))
        else:
            if not conflicts:
                print(colorize("No port conflicts detected.", Colors.GREEN))
            else:
                print(
                    colorize(
                        "WARNING: Port conflict detected", Colors.YELLOW + Colors.BOLD
                    )
                )
                for c in conflicts:
                    print(f"\nPort: {c['port']}")
                    print(f"- Docker container: {c['docker']['container_name']}")
                    for lp in c["local"]:
                        proc = lp.get("process") or {}
                        print(f"- Local process: {proc.get('name') or 'Unknown'}")
        return EXIT_OK

    if args.command == "watch":
        ports_to_watch: list[int] = []
        single = getattr(args, "port", None)
        multi = getattr(args, "ports", None)
        rng = getattr(args, "range", None)
        if multi:
            for p in multi:
                validate_port(p)
                ports_to_watch.append(p)
        elif rng:
            ports_to_watch = parse_port_range(rng)
        elif single is not None:
            validate_port(single)
            ports_to_watch = [single]
        else:
            print(
                colorize(
                    "Error: watch requires a port, --ports, or --range", Colors.RED
                ),
                file=sys.stderr,
            )
            return EXIT_INVALID_INPUT

        interval = getattr(args, "interval", 1.0)
        do_notify = getattr(args, "notify", False)

        import time
        from datetime import datetime, timezone

        states: dict[int, dict[str, Any]] = {}

        def get_port_state(port: int) -> dict[str, Any]:
            _proto = getattr(args, "proto", "tcp")
            inspector._clear_cache(ttl=min(interval, 1.5))
            local_bindings = inspector.find_bindings_on_port(port, proto=_proto)
            docker_hits = docker_mappings_for_host_port(port, debug=debug)
            pids = inspector.find_pids_on_port(port, proto=_proto)

            if docker_hits:
                m = docker_hits[0]
                return {
                    "type": "docker",
                    "container": m.container_name,
                    "image": m.image,
                    "status": m.status,
                    "host_port": m.host_port,
                    "container_port": m.container_port,
                }
            elif pids:
                procs = []
                for pid in pids:
                    info = inspector.get_process_info(pid)
                    procs.append(info.name if info else "Unknown")
                return {"type": "local", "pids": pids, "processes": procs}
            elif local_bindings:
                return {"type": "local-unknown", "message": "Owning PID not visible"}
            else:
                return {"type": "free"}

        def describe_state(port: int, state: dict[str, Any]) -> str:
            stype = state["type"]
            if stype == "free":
                return f"port {port}: FREE"
            elif stype == "docker":
                return (
                    f"port {port}: DOCKER '{state['container']}' "
                    f"({state['image']}, status={state['status']})"
                )
            elif stype == "local":
                procs_str = ", ".join(
                    f"{name} (PID {pid})"
                    for pid, name in zip(state["pids"], state["processes"])
                )
                return f"port {port}: LOCAL {procs_str}"
            elif stype == "local-unknown":
                return f"port {port}: LOCAL (PID hidden)"
            return f"port {port}: UNKNOWN"

        def _states_differ(a: dict[str, Any], b: dict[str, Any]) -> bool:
            if a["type"] != b["type"]:
                return True
            if a["type"] == "docker":
                return a["container"] != b.get("container") or a["status"] != b.get(
                    "status"
                )
            if a["type"] == "local":
                return set(a["pids"]) != set(b.get("pids", [])) or sorted(
                    a["processes"]
                ) != sorted(b.get("processes", []))
            return False

        for port in ports_to_watch:
            st = get_port_state(port)
            states[port] = st
            ts = datetime.now(timezone.utc)
            if args.json:
                st_out = dict(st)
                st_out["event"] = "initial"
                st_out["port"] = port
                st_out["timestamp"] = ts.isoformat()
                print(json.dumps(st_out))
                sys.stdout.flush()
            else:
                desc = describe_state(port, st)
                print(
                    colorize(
                        f"[{ts.strftime('%Y-%m-%d %H:%M:%S')}] Initial: {desc}",
                        Colors.WHITE,
                    )
                )

        until_mode = getattr(args, "until", None)
        timeout_sec = getattr(args, "timeout", None)

        def _is_until_satisfied(current_states: dict[int, dict[str, Any]]) -> bool:
            if not until_mode:
                return False
            if until_mode == "free":
                return all(st["type"] == "free" for st in current_states.values())
            elif until_mode == "occupied":
                return all(st["type"] != "free" for st in current_states.values())
            return False

        if until_mode and _is_until_satisfied(states):
            if not args.json:
                print(
                    colorize(
                        f"✓ Port state condition '{until_mode}' satisfied.",
                        Colors.GREEN + Colors.BOLD,
                    )
                )
            return EXIT_OK

        if not args.json:
            ports_str = ", ".join(str(p) for p in ports_to_watch)
            print(
                colorize(
                    f"\n\U0001f440 Watching port(s) {ports_str} (interval={interval}s). "
                    "Press Ctrl+C to stop.",
                    Colors.CYAN + Colors.BOLD,
                )
            )

        start_time = time.time()
        try:
            while True:
                if (
                    timeout_sec is not None
                    and (time.time() - start_time) >= timeout_sec
                ):
                    if args.json:
                        print(
                            json.dumps(
                                {
                                    "command": "watch",
                                    "event": "timeout",
                                    "success": False,
                                    "message": f"Timeout of {timeout_sec}s reached before --until '{until_mode}' condition satisfied",
                                }
                            )
                        )
                    else:
                        print(
                            colorize(
                                f"⏱ Timeout of {timeout_sec}s reached before --until '{until_mode}' condition satisfied.",
                                Colors.RED,
                            ),
                            file=sys.stderr,
                        )
                    return EXIT_GENERAL_ERROR

                time.sleep(interval)
                for port in ports_to_watch:
                    current = get_port_state(port)
                    last = states[port]

                    if _states_differ(current, last):
                        ts = datetime.now(timezone.utc)
                        states[port] = current
                        desc = describe_state(port, current)

                        if args.json:
                            out = dict(current)
                            out["event"] = "change"
                            out["port"] = port
                            out["timestamp"] = ts.isoformat()
                            print(json.dumps(out))
                            sys.stdout.flush()

                        else:
                            color = (
                                Colors.GREEN
                                if current["type"] == "free"
                                else Colors.YELLOW
                            )
                            print(
                                colorize(
                                    f"[{ts.strftime('%Y-%m-%d %H:%M:%S')}] \U0001f501 {desc}",
                                    color + Colors.BOLD,
                                )
                            )

                        if do_notify:
                            _desktop_notify(
                                "kport — port state change",
                                desc,
                            )

                if until_mode and _is_until_satisfied(states):
                    if not args.json:
                        print(
                            colorize(
                                f"✓ Port state condition '{until_mode}' satisfied.",
                                Colors.GREEN + Colors.BOLD,
                            )
                        )
                    return EXIT_OK

        except KeyboardInterrupt:
            if not args.json:
                print(colorize("\nStopping watch mode.", Colors.CYAN))
        return EXIT_OK

    if args.command == "mcp":
        try:
            from .mcp_server import run_mcp_server

            run_mcp_server()
            return EXIT_OK
        except ImportError:
            print(
                colorize(
                    "Error: MCP server module not available. Install mcp extra: pip install kport[mcp]",
                    Colors.RED,
                ),
                file=sys.stderr,
            )
            return EXIT_GENERAL_ERROR
        except Exception as e:  # noqa: BLE001 - top-level MCP server error handler
            print(colorize(f"MCP server error: {e}", Colors.RED), file=sys.stderr)
            return EXIT_GENERAL_ERROR

    if args.command == "completion":
        shell = getattr(args, "shell", None) or "bash"
        _print_completion(shell)
        return EXIT_OK

    # Unrecognised subcommand
    print(
        colorize(f"Error: unknown subcommand '{args.command}'", Colors.RED),
        file=sys.stderr,
    )
    print("Run 'kport --help' for usage.", file=sys.stderr)
    return EXIT_INVALID_INPUT


def handle_legacy_command(args: argparse.Namespace, inspector: BaseInspector) -> int:
    """Execute business workflows for legacy flags."""
    if getattr(args, "pid", None) is not None:
        return handle_inspect_pid_cli(args, inspector)

    # List ports
    if args.list:
        bindings = inspector.list_listening(proto=getattr(args, "proto", "both"))
        if args.json:
            print(_json_out("list", {"bindings": [asdict(b) for b in bindings]}))
        else:
            print(colorize("\n📋 Listening ports\n", Colors.CYAN + Colors.BOLD))
            print_table_listen(bindings)

    # Inspect port
    if args.inspect is not None:
        validate_port(args.inspect)
        local_bindings = inspector.find_bindings_on_port(
            args.inspect, proto=getattr(args, "proto", "both")
        )
        docker_hits = docker_mappings_for_host_port(args.inspect, debug=args.debug)
        pids = inspector.find_pids_on_port(
            args.inspect, proto=getattr(args, "proto", "both")
        )
        if not pids:
            if docker_hits:
                m = docker_hits[0]
                if args.json:
                    print(
                        _json_out(
                            "inspect",
                            {
                                "port": args.inspect,
                                "type": "docker",
                                "container": m.container_name,
                                "image": m.image,
                                "host_port": m.host_port,
                                "container_port": m.container_port,
                                "status": m.status,
                            },
                        )
                    )
                else:
                    print(
                        colorize(
                            f"\n🐳 Port {args.inspect} is mapped to Docker container: {m.container_name}\n",
                            Colors.GREEN + Colors.BOLD,
                        )
                    )
                    print(f"Image: {m.image}")
                    print(
                        f"Host Port: {m.host_port} → Container Port: {m.container_port}/{m.proto}"
                    )
                    print(f"Status: {m.status}")
            elif local_bindings:
                msg = "Port is in use, but the owning PID is not visible (try running with sudo/admin)."
                if args.json:
                    print(
                        _json_out(
                            "inspect",
                            {
                                "port": args.inspect,
                                "type": "local-unknown",
                                "message": msg,
                                "bindings": [asdict(b) for b in local_bindings],
                            },
                        )
                    )
                else:
                    print(colorize("⚠ " + msg, Colors.YELLOW))
            else:
                msg = f"No processes found using port {args.inspect}"
                if args.json:
                    print(_json_out("inspect", {"port": args.inspect, "pids": []}))
                else:
                    print(colorize("❌ " + msg, Colors.RED))
                return EXIT_PORT_FREE
        else:
            info_list = []
            for b in local_bindings:
                pid = b.pid
                info = inspector.get_process_info(pid) if pid else None
                info_list.append({
                    "pid": pid,
                    "proto": b.proto or "tcp",
                    "process": asdict(info) if info else None,
                })
            if args.json:
                out = {"port": args.inspect, "pids": info_list}
                if docker_hits:
                    out["docker"] = [asdict(m) for m in docker_hits]
                print(_json_out("inspect", out))
            else:
                print_port_bindings_detail(
                    args.inspect, local_bindings, inspector,
                    header=f"Port {args.inspect} — In Use:",
                )
                if docker_hits:
                    m = docker_hits[0]
                    print(
                        colorize(
                            f"Docker: {m.container_name} ({m.image})" f" host {m.host_port} -> {m.container_port}/{m.proto}",
                            Colors.CYAN,
                        )
                    )

    # Inspect multiple ports
    if args.inspect_multiple:
        ports = args.inspect_multiple
        results = []
        for port in ports:
            validate_port(port)
            pids = inspector.find_pids_on_port(port, proto=getattr(args, "proto", "both"))
            for pid in pids:
                proc = inspector.get_process_info(pid)
                results.append(
                    {
                        "port": port,
                        "pid": pid,
                        "process": asdict(proc) if proc else None,
                    }
                )
        if args.json:
            print(_json_out("inspect-multiple", {"ports": ports, "results": results}))
        else:
            print(
                colorize(
                    f"\n🔍 Inspecting {len(ports)} port(s)...\n",
                    Colors.CYAN + Colors.BOLD,
                )
            )
            if not results:
                print(
                    colorize(
                        "❌ No processes found on any of the specified ports",
                        Colors.RED,
                    )
                )
            else:
                print(
                    colorize(f"{'Port':<8} {'PID':<8} {'Process':<30}", Colors.BOLD)
                )
                print("─" * 60)
                for r in results:
                    pname = r["process"]["name"] if r["process"] else "-"
                    print(
                        f"{colorize(str(r['port']), Colors.CYAN):<8} {r['pid']!s:<8} {pname:<30}"
                    )
                print(
                    colorize(
                        f"\n✓ Found processes on {len(results)} items", Colors.GREEN
                    )
                )

    # Inspect range
    if args.inspect_range:
        ports = parse_port_range(args.inspect_range)
        results = []
        for port in ports:
            pids = inspector.find_pids_on_port(port, proto=getattr(args, "proto", "both"))
            for pid in pids:
                proc = inspector.get_process_info(pid)
                results.append(
                    {
                        "port": port,
                        "pid": pid,
                        "process": asdict(proc) if proc else None,
                    }
                )
        if args.json:
            print(_json_out("inspect-range", {"range": args.inspect_range, "results": results}))
        else:
            print(
                colorize(
                    f"\n🔍 Inspecting port range {args.inspect_range} ({len(ports)} ports)...\n",
                    Colors.CYAN + Colors.BOLD,
                )
            )
            if not results:
                print(
                    colorize(
                        f"❌ No processes found in port range {args.inspect_range}",
                        Colors.RED,
                    )
                )
            else:
                print(
                    colorize(f"{'Port':<8} {'PID':<8} {'Process':<30}", Colors.BOLD)
                )
                print("─" * 60)
                for r in results:
                    pname = r["process"]["name"] if r["process"] else "-"
                    print(
                        f"{colorize(str(r['port']), Colors.CYAN):<8} {r['pid']!s:<8} {pname:<30}"
                    )
                print(
                    colorize(
                        f"\n✓ Found processes on {len(results)} entries",
                        Colors.GREEN,
                    )
                )

    # Inspect by process name
    if args.inspect_process:
        pname = args.inspect_process
        proto_req = getattr(args, "proto", "both")
        bindings = inspector.find_ports_by_process_name(
            pname, exact=args.exact, proto=proto_req
        )
        if args.json:
            print(_json_out("inspect-process", {"process": pname, "bindings": [asdict(b) for b in bindings]}))
            if not bindings:
                pids = inspector.find_pids_by_name(pname, exact=args.exact)
                if pids:
                    pids_str = ", ".join(map(str, pids))
                    other_proto = "udp" if proto_req == "tcp" else ("tcp" if proto_req == "udp" else None)
                    alt_bindings = (
                        inspector.find_ports_by_process_name(pname, exact=args.exact, proto=other_proto)
                        if other_proto
                        else []
                    )
                    if alt_bindings:
                        alt_s = "socket" if len(alt_bindings) == 1 else "sockets"
                        print(
                            colorize(
                                f"Note: Process '{pname}' has no {proto_req.upper()} bindings, "
                                f"but {len(alt_bindings)} {other_proto.upper()} {alt_s} exist. "
                                f"Try --proto={other_proto} or --proto=both.",
                                Colors.CYAN,
                            ),
                            file=sys.stderr,
                        )
                    elif not _is_elevated() and _has_access_denied_on_pids(pids):
                        if sys.platform == "win32":
                            _elev_hint = (
                                f"Warning: Found process(es) (PID(s): {pids_str}) but could not "
                                f"read their port bindings. "
                                f"Try running as Administrator: Right-click your terminal → 'Run as administrator'"
                            )
                        else:
                            _elev_hint = (
                                f"Warning: Found process(es) (PID(s): {pids_str}) but could not "
                                f"read their port bindings. Common inside containers/snap. "
                                f"Try: sudo kport -ip '{pname}'"
                            )
                        print(
                            colorize(_elev_hint, Colors.YELLOW),
                            file=sys.stderr,
                        )
                    else:
                        proto_desc = f"{proto_req.upper()} " if proto_req in ("tcp", "udp") else ""
                        print(
                            colorize(
                                f"Warning: Process(es) '{pname}' (PID(s): {pids_str}) are running "
                                f"but not listening on any {proto_desc}network ports.",
                                Colors.YELLOW,
                            ),
                            file=sys.stderr,
                        )
        else:
            print(
                colorize(
                    f"\n\U0001f50d Inspecting processes matching '{pname}'\n",
                    Colors.CYAN + Colors.BOLD,
                )
            )
            if not bindings:
                pids = inspector.find_pids_by_name(pname, exact=args.exact)
                if pids:
                    pids_str = (
                        f"{', '.join(map(str, pids[:5]))}, ... ({len(pids)} instances total)"
                        if len(pids) > 6
                        else ", ".join(map(str, pids))
                    )
                    other_proto = "udp" if proto_req == "tcp" else ("tcp" if proto_req == "udp" else None)
                    alt_bindings = (
                        inspector.find_ports_by_process_name(pname, exact=args.exact, proto=other_proto)
                        if other_proto
                        else []
                    )
                    if alt_bindings:
                        alt_s = "socket" if len(alt_bindings) == 1 else "sockets"
                        msg = (
                            f"\u2139  Process '{pname}' ({pids_str}) is running but is not listening on any {proto_req.upper()} ports.\n"
                            f"   Found {len(alt_bindings)} {other_proto.upper()} {alt_s}. Try: kport -ip '{pname}' --proto={other_proto} or --proto=both"
                        )
                        print(colorize(msg, Colors.CYAN))
                    elif not _is_elevated() and _has_access_denied_on_pids(pids):
                        if sys.platform == "win32":
                            _elev_msg = (
                                f"\u26a0  Process '{pname}' found ({pids_str}) "
                                f"but its port bindings are not accessible without elevated privileges.\n"
                                f"   Try: Run your terminal as Administrator "
                                f"(Right-click → 'Run as administrator') and retry."
                            )
                        else:
                            _elev_msg = (
                                f"\u26a0  Process '{pname}' found ({pids_str}) "
                                f"but its port bindings are not accessible without elevated privileges.\n"
                                f"   This is common for snap-packaged apps or system services.\n"
                                f"   Try: sudo kport -ip '{pname}'"
                            )
                        print(colorize(_elev_msg, Colors.YELLOW))
                    else:
                        proto_desc = f"{proto_req.upper()} " if proto_req in ("tcp", "udp") else ""
                        print(
                            colorize(
                                f"\u2139  Process '{pname}' ({pids_str}) is running "
                                f"but is not listening on any {proto_desc}network ports.",
                                Colors.CYAN,
                            )
                        )
                else:
                    print(
                        colorize(
                            f"\u274c No processes found matching '{pname}'",
                            Colors.RED,
                        )
                    )
            else:
                pid_groups: dict[int, list] = {}
                for b in bindings:
                    pid_groups.setdefault(b.pid or 0, []).append(b)
                print(
                    colorize(
                        f"{'PID':<8} {'Process':<18} {'Port':<8} {'Proto':<8} {'State':<10} {'Address'}",
                        Colors.BOLD,
                    )
                )
                print("\u2500" * 70)
                for pid, ports in pid_groups.items():
                    proc_name = ports[0].process_name or "-"
                    proto_0 = (ports[0].proto or "tcp").upper()
                    proto_color_0 = Colors.BLUE if proto_0 == "TCP" else Colors.MAGENTA
                    proto_badge_0 = colorize(f"{proto_0:<8}", proto_color_0)
                    pid_s = colorize(f"{str(pid):<8}", Colors.CYAN)
                    addr_0 = ports[0].laddr or "-"
                    print(
                        f"{pid_s} {proc_name:<18} {ports[0].port:<8} {proto_badge_0} {ports[0].state or '-':<10} {addr_0}"
                    )
                    for p in ports[1:]:
                        proto_p = (p.proto or "tcp").upper()
                        proto_color_p = Colors.BLUE if proto_p == "TCP" else Colors.MAGENTA
                        proto_badge_p = colorize(f"{proto_p:<8}", proto_color_p)
                        addr_p = p.laddr or "-"
                        print(f"{'':<8} {'':<18} {p.port:<8} {proto_badge_p} {p.state or '-':<10} {addr_p}")
                print(
                    colorize(
                        f"\n\u2713 Total processes found: {len(pid_groups)}",
                        Colors.GREEN,
                    )
                )
                print(
                    colorize(
                        f"\u2713 Total connections: {len(bindings)}", Colors.GREEN
                    )
                )

    # Kill by process name (legacy)
    if args.kill_process:
        pname = args.kill_process
        pids = inspector.find_pids_by_name(pname, exact=args.exact)
        if not pids:
            if args.json:
                print(_json_out("kill-process", {"name": pname, "pids": []}))
            else:
                print(
                    colorize(
                        f"❌ No processes found matching '{pname}'", Colors.RED
                    )
                )
        else:
            # Check safety policy
            safe, safety_msg = check_safety_policy(None, pids, args, inspector)
            if not safe:
                if args.json:
                    print(
                        _json_out(
                            "kill-process",
                            {
                                "name": pname,
                                "success": False,
                                "message": safety_msg,
                            },
                        )
                    )
                else:
                    print(colorize(safety_msg, Colors.RED), file=sys.stderr)
                return EXIT_PERMISSION
            if args.json:
                if not args.yes:
                    out = []
                    for pid in pids:
                        info = inspector.get_process_info(pid)
                        out.append(
                            {"pid": pid, "process": asdict(info) if info else None}
                        )
                    print(
                        _json_out(
                            "kill-process",
                            {
                                "name": pname,
                                "pids": out,
                                "message": "Note: Use --yes to actually perform kills.",
                            },
                        )
                    )
                else:
                    killed = []
                    failed = []
                    for pid in pids:
                        if getattr(args, "kill_tree", False):
                            ok, msg = inspector.kill_process_tree(
                                pid,
                                graceful_timeout=_resolve_timeout(args),
                                force=args.force,
                                dry_run=args.dry_run,
                                assume_yes=args.yes,
                                confirm_fn=confirm_prompt,
                            )
                        else:
                            ok, msg = inspector.kill_pid(
                                pid,
                                graceful_timeout=_resolve_timeout(args),
                                force=args.force,
                                dry_run=args.dry_run,
                                assume_yes=args.yes,
                                confirm_fn=confirm_prompt,
                            )

                        if ok:
                            killed.append({"pid": pid, "msg": msg})
                        else:
                            failed.append({"pid": pid, "msg": msg})
                    print(_json_out("kill-process", {"killed": killed, "failed": failed}))
                    if failed:
                        return EXIT_GENERAL_ERROR
            else:
                target_pids = _select_pids_interactively(pids, pname, args, inspector)
                if target_pids is None:
                    print(colorize("Operation cancelled.", Colors.YELLOW))
                else:
                    killed_count = 0
                    failed_count = 0
                    for pid in target_pids:
                        if getattr(args, "kill_tree", False):
                            ok, msg = inspector.kill_process_tree(
                                pid,
                                graceful_timeout=_resolve_timeout(args),
                                force=args.force,
                                dry_run=args.dry_run,
                                assume_yes=args.yes,
                                confirm_fn=confirm_prompt,
                            )
                        else:
                            ok, msg = inspector.kill_pid(
                                pid,
                                graceful_timeout=_resolve_timeout(args),
                                force=args.force,
                                dry_run=args.dry_run,
                                assume_yes=args.yes,
                                confirm_fn=confirm_prompt,
                            )

                        if ok:
                            killed_count += 1
                            print(
                                colorize(
                                    f"✓ Killed PID {pid} ({msg})", Colors.GREEN
                                )
                            )
                        else:
                            failed_count += 1
                            print(
                                colorize(
                                    f"✗ Failed to kill PID {pid} ({msg})",
                                    Colors.RED,
                                )
                            )

                    if failed_count > 0:
                        print(
                            colorize(
                                f"\n✗ Failed to kill {failed_count}/{len(target_pids)} process(es)",
                                Colors.RED + Colors.BOLD,
                            )
                        )
                        return EXIT_GENERAL_ERROR
                    else:
                        print(
                            colorize(
                                f"\n✓ Successfully killed {killed_count}/{len(target_pids)} process(es)",
                                Colors.GREEN + Colors.BOLD,
                            )
                        )

    # Kill single port (legacy)
    if args.kill is not None:
        validate_port(args.kill)
        pids = inspector.find_pids_on_port(
            args.kill, proto=getattr(args, "proto", "both")
        )

        # Check safety policy
        safe, safety_msg = check_safety_policy(args.kill, pids, args, inspector)
        if not safe:
            if args.json:
                print(
                    _json_out(
                        "kill",
                        {
                            "port": args.kill,
                            "success": False,
                            "message": safety_msg,
                        },
                    )
                )
            else:
                print(colorize(safety_msg, Colors.RED), file=sys.stderr)
            return EXIT_PERMISSION

        local_bindings = inspector.find_bindings_on_port(
            args.kill, proto=getattr(args, "proto", "both")
        )
        docker_hits = docker_mappings_for_host_port(args.kill, debug=args.debug)
        if not pids:
            if docker_hits:
                m = docker_hits[0]
                if args.json and not args.yes and not args.dry_run:
                    print(
                        _json_out(
                            "kill",
                            {
                                "port": args.kill,
                                "type": "docker",
                                "container": m.container_name,
                                "container_id": m.container_id,
                                "message": "Refusing to act without --yes in JSON mode",
                            },
                        )
                    )
                else:
                    action = "stop" if args.json else None
                    if not args.json:
                        print(
                            colorize(
                                f"\n🐳 Port {args.kill} belongs to Docker container: {m.container_name}",
                                Colors.YELLOW + Colors.BOLD,
                            )
                        )
                        action = choose_docker_action(assume_yes=args.yes)
                    if (
                        action == "rm"
                        and not args.dry_run
                        and not confirm_docker_rm(
                            m.container_name,
                            m.container_id,
                            assume_yes=args.yes,
                            force=args.force,
                        )
                    ):
                            if args.json:
                                print(
                                    _json_out(
                                        "kill",
                                        {
                                            "port": args.kill,
                                            "type": "docker",
                                            "container": m.container_name,
                                            "container_id": m.container_id,
                                            "action": "rm",
                                            "ok": False,
                                            "message": "Removing a Docker container is irreversible. Use --force in addition to --yes to bypass interactive confirmation.",
                                        },
                                    )
                                )
                            return EXIT_PERMISSION

                    if action:
                        ok, msg = docker_action_on_container(
                            m.container_id,
                            action=action,
                            dry_run=args.dry_run,
                            debug=args.debug,
                        )
                        wait_ok = True
                        if (
                            ok
                            and getattr(args, "wait_for_exit", None) is not None
                            and not args.dry_run
                        ):
                            if not args.json:
                                print(
                                    colorize(
                                        f"⌛ Waiting for port {args.kill} to be free (timeout {args.wait_for_exit}s)...",
                                        Colors.WHITE,
                                    )
                                )
                            wait_ok = _poll_until_free(
                                args.kill, args.wait_for_exit, inspector
                            )
                            if not wait_ok and not args.json:
                                print(
                                    colorize(
                                        f"⏱ Process did not exit within {args.wait_for_exit}s",
                                        Colors.RED,
                                    ),
                                    file=sys.stderr,
                                )
                            elif wait_ok and not args.json:
                                print(
                                    colorize(
                                        f"✓ Port {args.kill} is now free.",
                                        Colors.GREEN,
                                    )
                                )

                        if args.json:
                            out = {
                                "port": args.kill,
                                "type": "docker",
                                "action": action,
                                "ok": ok,
                                "message": msg,
                            }
                            if getattr(args, "wait_for_exit", None) is not None:
                                out["wait_for_exit_ok"] = wait_ok
                            print(_json_out("kill", out))
                        else:
                            if ok and wait_ok:
                                print(
                                    colorize(
                                        ("✓ " if ok else "✗ ") + msg,
                                        Colors.GREEN if ok else Colors.RED,
                                    )
                                )
                        if not ok or not wait_ok:
                            return EXIT_GENERAL_ERROR

            elif local_bindings:
                msg = "Port is in use but PID is not visible; cannot kill safely. Try sudo/admin."
                if args.json:
                    print(
                        _json_out(
                            "kill",
                            {
                                "port": args.kill,
                                "ok": False,
                                "message": msg,
                                "bindings": [asdict(b) for b in local_bindings],
                            },
                        )
                    )
                else:
                    print(colorize(msg, Colors.RED))
                return EXIT_PERMISSION
            else:
                if args.json:
                    print(_json_out("kill", {"port": args.kill, "killed": [], "failed": []}))
                else:
                    print(
                        colorize(
                            f"❌ No process found using port {args.kill}",
                            Colors.RED,
                        )
                    )
                return EXIT_PORT_FREE
        else:
            if args.json:
                ok, msg = inspector.kill_port(
                    args.kill,
                    graceful_timeout=_resolve_timeout(args),
                    force=args.force,
                    dry_run=args.dry_run,
                    debug=args.debug,
                    assume_yes=args.yes,
                    kill_tree=getattr(args, "kill_tree", False),
                    proto=getattr(args, "proto", "both"),
                    confirm_fn=confirm_prompt,
                )

                wait_ok = True
                if (
                    ok
                    and getattr(args, "wait_for_exit", None) is not None
                    and not args.dry_run
                ):
                    wait_ok = _poll_until_free(
                        args.kill, args.wait_for_exit, inspector,
                        proto=getattr(args, "proto", "both"),
                    )
                out = {
                    "port": args.kill,
                    "success": ok,
                    "message": msg,
                    "pids_targeted": pids,
                }
                if docker_hits:
                    out["docker"] = [asdict(m) for m in docker_hits]
                if getattr(args, "wait_for_exit", None) is not None:
                    out["wait_for_exit_ok"] = wait_ok
                print(_json_out("kill", out))
                return EXIT_OK if (ok and wait_ok) else EXIT_GENERAL_ERROR

            else:
                print_port_bindings_detail(
                    args.kill, local_bindings, inspector,
                    header=f"Port {args.kill} — processes to terminate:",
                )
                if docker_hits:
                    m = docker_hits[0]
                    print(
                        colorize(
                            f"🐳 Docker mapping: {m.container_name} ({m.image}) host {m.host_port} → {m.container_port}/{m.proto}",
                            Colors.CYAN,
                        )
                    )

                tcp_bindings = [b for b in local_bindings if (b.proto or "tcp").lower() == "tcp"]
                udp_bindings = [b for b in local_bindings if (b.proto or "tcp").lower() == "udp"]
                requested_proto = getattr(args, "proto", "both")

                protos_to_kill: list[str] = []
                if requested_proto == "both" and tcp_bindings and udp_bindings and not args.yes:
                    tcp_pids = sorted({b.pid for b in tcp_bindings if b.pid})
                    udp_pids = sorted({b.pid for b in udp_bindings if b.pid})
                    print()
                    print(colorize(f"Port {args.kill} has dual-protocol listeners. Select what to kill:", Colors.BOLD))
                    print(f"  [1] Both (TCP & UDP)")
                    print(f"  [2] TCP only (PID {', '.join(map(str, tcp_pids))})")
                    print(f"  [3] UDP only (PID {', '.join(map(str, udp_pids))})")
                    print(f"  [q] Cancel")
                    print()
                    print(colorize(f"Tip: You can target directly next time with:\n  kport -k {args.kill} --proto=tcp\n  kport -k {args.kill} --proto=udp", Colors.CYAN))
                    print()
                    try:
                        choice = input(colorize("Choice [1/2/3/q]: ", Colors.CYAN + Colors.BOLD)).strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        choice = "q"
                        print()

                    if choice in ("1", "both", "b"):
                        protos_to_kill = ["both"]
                    elif choice in ("2", "tcp", "t"):
                        protos_to_kill = ["tcp"]
                    elif choice in ("3", "udp", "u"):
                        protos_to_kill = ["udp"]
                    else:
                        print(colorize("Operation cancelled.", Colors.YELLOW))
                        return EXIT_GENERAL_ERROR
                elif not args.yes:
                    active_proto = "TCP" if tcp_bindings and not udp_bindings else (
                        "UDP" if udp_bindings and not tcp_bindings else requested_proto.upper()
                    )
                    active_bindings = tcp_bindings or udp_bindings or local_bindings
                    active_pids = sorted({b.pid for b in active_bindings if b.pid})
                    pids_str = ", ".join(map(str, active_pids))
                    print()
                    if not confirm_prompt(
                        f"Kill {active_proto} listener(s) on port {args.kill} (PID {pids_str})? [{active_proto}]",
                        assume_yes=False,
                    ):
                        print(colorize("Operation cancelled.", Colors.YELLOW))
                        return EXIT_GENERAL_ERROR
                    protos_to_kill = [requested_proto if requested_proto in ("tcp", "udp") else active_proto.lower()]
                else:
                    protos_to_kill = [requested_proto]

                overall_ok = True
                for kill_proto in protos_to_kill:
                    ok, msg = inspector.kill_port(
                        args.kill,
                        graceful_timeout=_resolve_timeout(args),
                        force=args.force,
                        dry_run=args.dry_run,
                        debug=args.debug,
                        assume_yes=args.yes,
                        kill_tree=getattr(args, "kill_tree", False),
                        proto=kill_proto,
                        confirm_fn=confirm_prompt,
                    )

                    wait_ok = True
                    if (
                        ok
                        and getattr(args, "wait_for_exit", None) is not None
                        and not args.dry_run
                    ):
                        print(
                            colorize(
                                f"⌛ Waiting for port {args.kill} to be free (timeout {args.wait_for_exit}s)...",
                                Colors.WHITE,
                            )
                        )
                        wait_ok = _poll_until_free(
                            args.kill, args.wait_for_exit, inspector,
                            proto=kill_proto,
                        )
                        if not wait_ok:
                            print(
                                colorize(
                                    f"⏱ Process did not exit within {args.wait_for_exit}s",
                                    Colors.RED,
                                ),
                                file=sys.stderr,
                            )
                        else:
                            print(
                                colorize(
                                    f"✓ Port {args.kill} is now free.", Colors.GREEN
                                )
                            )

                    proto_label = f" [{kill_proto.upper()}]" if kill_proto in ("tcp", "udp") else (" [TCP/UDP]" if kill_proto == "both" else "")
                    if ok and wait_ok:
                        print(
                            colorize(
                                f"\n✓ Port {args.kill}{proto_label} successfully freed ({msg})",
                                Colors.GREEN + Colors.BOLD,
                            )
                        )
                    else:
                        print(
                            colorize(
                                f"\n✗ Failed to free port {args.kill}{proto_label}: {msg if ok else 'Wait timeout'}",
                                Colors.RED + Colors.BOLD,
                            )
                        )
                        overall_ok = False

                return EXIT_OK if overall_ok else EXIT_GENERAL_ERROR

    # Kill multiple ports (legacy)
    if args.kill_all:
        for port in args.kill_all:
            validate_port(port)
        # Safety shield check
        for port in args.kill_all:
            pids = inspector.find_pids_on_port(port, proto=getattr(args, "proto", "both"))
            safe, safety_msg = check_safety_policy(port, pids, args, inspector)
            if not safe:
                print(
                    colorize(
                        f"Port {port} failed safety check: {safety_msg}", Colors.RED
                    ),
                    file=sys.stderr,
                )
                return EXIT_PERMISSION
        port_pid_map = {}
        for port in args.kill_all:
            pids = inspector.find_pids_on_port(port, proto=getattr(args, "proto", "both"))
            if pids:
                port_pid_map[port] = pids
        if not port_pid_map:
            print(
                colorize(
                    "❌ No processes found on any of the specified ports",
                    Colors.RED,
                )
            )
        else:
            print(
                colorize("Found processes on the following ports:", Colors.YELLOW)
            )
            for port, pids in port_pid_map.items():
                names = [
                    inspector.get_process_info(pid).name
                    if inspector.get_process_info(pid)
                    else "?"
                    for pid in pids
                ]
                print(
                    colorize(
                        f"  Port {port}: PIDs {', '.join(map(str, pids))} ({', '.join(names)})",
                        Colors.WHITE,
                    )
                )
            if not confirm_prompt(
                f"\nAre you sure you want to kill {sum(len(ps) for ps in port_pid_map.values())} process(es)?",
                assume_yes=args.yes,
            ):
                print(colorize("Operation cancelled.", Colors.YELLOW))
                return EXIT_GENERAL_ERROR
            else:
                failed_ports = 0
                total_ports = len(port_pid_map)
                for port in port_pid_map:
                    ok, msg = inspector.kill_port(
                        port,
                        graceful_timeout=_resolve_timeout(args),
                        force=args.force,
                        dry_run=args.dry_run,
                        debug=args.debug,
                        assume_yes=args.yes,
                        kill_tree=getattr(args, "kill_tree", False),
                        proto=getattr(args, "proto", "both"),
                        confirm_fn=confirm_prompt,
                    )

                    if ok:
                        print(
                            colorize(f"✓ Freed port {port} ({msg})", Colors.GREEN)
                        )
                        if (
                            getattr(args, "wait_for_exit", None) is not None
                            and not args.dry_run
                        ):
                            print(
                                colorize(
                                    f"⌛ Waiting for port {port} to be free (timeout {args.wait_for_exit}s)...",
                                    Colors.WHITE,
                                )
                            )
                            wait_ok = _poll_until_free(
                                port, args.wait_for_exit, inspector,
                                proto=getattr(args, "proto", "both"),
                            )
                            if not wait_ok:
                                print(
                                    colorize(
                                        f"⏱ Process did not exit within {args.wait_for_exit}s",
                                        Colors.RED,
                                    ),
                                    file=sys.stderr,
                                )
                                failed_ports += 1
                            else:
                                print(
                                    colorize(
                                        f"✓ Port {port} is now free.", Colors.GREEN
                                    )
                                )
                    else:
                        print(
                            colorize(
                                f"✗ Failed to free port {port}: {msg}", Colors.RED
                            )
                        )
                        failed_ports += 1

                if failed_ports > 0:
                    print(
                        colorize(
                            f"\n✗ Failed to free {failed_ports}/{total_ports} port(s)",
                            Colors.RED + Colors.BOLD,
                        )
                    )
                    return EXIT_GENERAL_ERROR
                else:
                    print(
                        colorize(
                            f"\n✓ Successfully freed all {total_ports} port(s)",
                            Colors.GREEN + Colors.BOLD,
                        )
                    )

    # Kill range (legacy)
    if args.kill_range:
        ports = parse_port_range(args.kill_range)
        # Safety shield check
        for port in ports:
            pids = inspector.find_pids_on_port(port, proto=getattr(args, "proto", "both"))
            safe, safety_msg = check_safety_policy(port, pids, args, inspector)
            if not safe:
                print(
                    colorize(
                        f"Port {port} failed safety check: {safety_msg}", Colors.RED
                    ),
                    file=sys.stderr,
                )
                return EXIT_PERMISSION
        port_pid_map = {}
        for port in ports:
            pids = inspector.find_pids_on_port(port, proto=getattr(args, "proto", "both"))
            if pids:
                port_pid_map[port] = pids
        if not port_pid_map:
            print(
                colorize(
                    f"❌ No processes found in port range {args.kill_range}",
                    Colors.RED,
                )
            )
        else:
            print(
                colorize(
                    f"Found processes on {len(port_pid_map)} port(s) in range:",
                    Colors.YELLOW,
                )
            )
            for port, pids in port_pid_map.items():
                print(
                    colorize(
                        f"  Port {port}: PIDs {', '.join(map(str, pids))}",
                        Colors.WHITE,
                    )
                )
            if not confirm_prompt(
                f"\nAre you sure you want to kill {sum(len(ps) for ps in port_pid_map.values())} process(es)?",
                assume_yes=args.yes,
            ):
                print(colorize("Operation cancelled.", Colors.YELLOW))
                return EXIT_GENERAL_ERROR
            else:
                failed_ports = 0
                total_ports = len(port_pid_map)
                for port in port_pid_map:
                    ok, msg = inspector.kill_port(
                        port,
                        graceful_timeout=_resolve_timeout(args),
                        force=args.force,
                        dry_run=args.dry_run,
                        debug=args.debug,
                        assume_yes=args.yes,
                        kill_tree=getattr(args, "kill_tree", False),
                        proto=getattr(args, "proto", "both"),
                        confirm_fn=confirm_prompt,
                    )

                    if ok:
                        print(
                            colorize(f"✓ Freed port {port} ({msg})", Colors.GREEN)
                        )
                        if (
                            getattr(args, "wait_for_exit", None) is not None
                            and not args.dry_run
                        ):
                            print(
                                colorize(
                                    f"⌛ Waiting for port {port} to be free (timeout {args.wait_for_exit}s)...",
                                    Colors.WHITE,
                                )
                            )
                            wait_ok = _poll_until_free(
                                port, args.wait_for_exit, inspector,
                                proto=getattr(args, "proto", "both"),
                            )
                            if not wait_ok:
                                print(
                                    colorize(
                                        f"⏱ Process did not exit within {args.wait_for_exit}s",
                                        Colors.RED,
                                    ),
                                    file=sys.stderr,
                                )
                                failed_ports += 1
                            else:
                                print(
                                    colorize(
                                        f"✓ Port {port} is now free.", Colors.GREEN
                                    )
                                )
                    else:
                        print(
                            colorize(
                                f"✗ Failed to free port {port}: {msg}", Colors.RED
                            )
                        )
                        failed_ports += 1

                if failed_ports > 0:
                    print(
                        colorize(
                            f"\n✗ Failed to free {failed_ports}/{total_ports} port(s) in range",
                            Colors.RED + Colors.BOLD,
                        )
                    )
                    return EXIT_GENERAL_ERROR
                else:
                    print(
                        colorize(
                            f"\n✓ Successfully freed all {total_ports} port(s) in range",
                            Colors.GREEN + Colors.BOLD,
                        )
                    )

    return EXIT_OK


def _print_completion(shell: str) -> None:
    if shell == "bash":
        print("""# bash completion for kport
_kport_completion() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="inspect explain diagnose doctor connections kill kill-process list docker conflicts watch mcp completion --json --dry-run --yes --debug --config --bypass-safety --version --wait-for-exit --proto"
    case "${prev}" in
        inspect|explain|diagnose|connections|watch|kill)
            return 0
            ;;
        kill-process|--inspect-process|-ip|-kp)
            return 0
            ;;
        *)
            ;;
    esac
    COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
    return 0
}
complete -F _kport_completion kport
""")
    elif shell == "zsh":
        print("""# zsh completion for kport
#compdef kport
 
_kport() {
    local line
    _arguments -C \\
        '--json[Output machine-readable JSON]' \\
        '--dry-run[Show actions without executing]' \\
        '(-y --yes)'{-y,--yes}'[Skip confirmation prompts]' \\
        '--debug[Verbose internal logs]' \\
        '--config[Path to JSON config file]' \\
        '--bypass-safety[Bypass safety shields on protected ports/processes]' \\
        '--wait-for-exit[Wait for port to be free after killing (seconds)]' \\
        '--proto[Protocol type tcp|udp|both]' \\
        '(-v --version)'{-v,--version}'[Show version]' \\
        '1: :->cmds' \\
        '*:: :->args'
 
    case $state in
        cmds)
            _values "subcommand" \\
                'inspect[Inspect a port (docker-aware)]' \\
                'explain[Explain why a port is blocked]' \\
                'diagnose[Structured analysis and fix recommendations for a port]' \\
                'doctor[Environment-wide read-only diagnostic report]' \\
                'connections[List active network connections]' \\
                'kill[Safely free a port (docker-aware)]' \\
                'kill-process[Kill processes by name]' \\
                'list[List active ports (local + docker)]' \\
                'docker[List Docker-published ports]' \\
                'conflicts[Detect docker/local port conflicts]' \\
                'watch[Live monitoring of port ownership]' \\
                'mcp[Start the stdio Model Context Protocol (MCP) server]' \\
                'completion[Generate shell autocompletion]'
            ;;
    esac
}
""")
    elif shell == "fish":
        print("""# fish completion for kport
complete -c kport -f
complete -c kport -a "inspect explain diagnose doctor connections kill kill-process list docker conflicts watch mcp completion"
complete -c kport -s y -l yes -d "Skip confirmation prompts"
complete -c kport -l json -d "Output machine-readable JSON"
complete -c kport -l dry-run -d "Show actions without executing"
complete -c kport -l debug -d "Verbose internal logs"
complete -c kport -l config -d "Path to JSON config file"
complete -c kport -l bypass-safety -d "Bypass safety shields on protected ports/processes"
complete -c kport -l wait-for-exit -d "Wait for port to be free after killing (seconds)"
complete -c kport -l proto -r -f -a "tcp udp both" -d "Protocol type tcp|udp|both"
complete -c kport -s v -l version -d "Show version"
""")
    elif shell == "powershell":
        print("""# powershell completion for kport
Register-ArgumentCompleter -Native -CommandName kport -ScriptBlock {
    param($wordToComplete, $commandAst, $cursorPosition)
    $opts = @('inspect', 'explain', 'diagnose', 'doctor', 'connections', 'kill', 'kill-process', 'list', 'docker', 'conflicts', 'watch', 'mcp', 'completion', '--json', '--dry-run', '--yes', '--debug', '--config', '--bypass-safety', '--version', '--wait-for-exit', '--proto')
    $opts | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object {
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }
}
""")
    else:
        print(f"Error: unsupported shell '{shell}'", file=sys.stderr)
