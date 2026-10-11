"""
Read-only diagnostic intelligence for kport.

Provides structured computation of:
  - port diagnosis (observations / inferences / risks / recommendations);
  - environment doctor report;
  - Docker/local port conflict detection.

Design constraints
------------------
* No terminal formatting, ANSI codes, or Rich output.
* No dependency on cli.py or mcp_server.py.
* No destructive operations.
* Returns plain Python dicts/lists suitable for JSON serialisation.

The CLI and MCP layers consume these results and apply their own
presentation / serialisation on top.
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .docker_engine import (
    docker_available,
    docker_mappings_for_host_port,
    list_docker_mappings,
)
from .process_manager import detect_process_manager
from .project import resolve_project
from .safety import resolve_protected_sets

if TYPE_CHECKING:
    from .inspectors import BaseInspector


# ---------------------------------------------------------------------------
# Port Diagnosis
# ---------------------------------------------------------------------------

def diagnose_port(
    port: int,
    inspector: BaseInspector,
    proto: str = "tcp",
    config: dict[str, Any] | None = None,
    bypass_safety: bool = False,
) -> dict[str, Any]:
    """Compute a structured diagnostic report for *port*.

    Returns a plain dict with keys:
      port, blocked, observations, inferences, risks, recommendations

    This function is **read-only** — it never kills or modifies anything.
    Safety-shield enforcement is the responsibility of the caller for
    destructive operations; this function only *reports* whether a target
    would be blocked.

    Parameters
    ----------
    port:
        Port number to diagnose (1–65535, caller is responsible for validation).
    inspector:
        Active inspector used for system queries.
    proto:
        Protocol filter passed to the inspector (``"tcp"``, ``"udp"``, ``"both"``).
    config:
        Pre-loaded kport configuration dict for protected-port/process lookups.
        When ``None`` only the hard-coded defaults are used.
    """
    # ------------------------------------------------------------------
    # 1. Observations
    # ------------------------------------------------------------------
    docker_hits = docker_mappings_for_host_port(port)
    local_bindings = inspector.find_bindings_on_port(port, proto=proto)
    pids = inspector.find_pids_on_port(port, proto=proto)

    is_blocked = bool(docker_hits or local_bindings or pids)

    obs_type = "free"
    if docker_hits:
        obs_type = "docker"
    elif pids:
        obs_type = "local"
    elif local_bindings:
        obs_type = "local-unknown"

    obs_processes = []
    for pid in pids:
        info = inspector.get_process_info(pid)
        if info:
            proc_dict = asdict(info)
            # Enrich with parent process name for lineage context
            if info.ppid:
                parent_info = inspector.get_process_info(info.ppid)
                proc_dict["parent_name"] = parent_info.name if parent_info else None
            else:
                proc_dict["parent_name"] = None
            obs_processes.append(proc_dict)
        else:
            obs_processes.append(
                {"pid": pid, "name": "unknown", "exe": None, "cmdline": None,
                 "user": None, "ppid": None, "parent_name": None, "cwd": None,
                 "start_time": None}
            )

    obs_docker = [asdict(m) for m in docker_hits]
    obs_bindings = [asdict(b) for b in local_bindings]

    # Active connections on this port (bounded to 50 to keep response tight)
    try:
        port_connections = filter_connections(inspector, port=port, max_results=50)
    except Exception:
        port_connections = []

    observations = {
        "port": port,
        "blocked": is_blocked,
        "type": obs_type,
        "bindings": obs_bindings,
        "processes": obs_processes,
        "docker_containers": obs_docker,
        "connections": port_connections,
    }

    # ------------------------------------------------------------------
    # 2. Inferences
    # ------------------------------------------------------------------
    inferences: list[dict] = []

    for p in obs_processes:
        pid = p["pid"]

        pm_info = detect_process_manager(pid)
        if pm_info:
            inferences.append({
                "type": "process_manager",
                "pid": pid,
                "manager": pm_info["manager"],
                "name": pm_info["name"],
                "confidence": "high",
                "reason": (
                    f"Process {pid} is running under process manager "
                    f"'{pm_info['manager']}' service '{pm_info['name']}'"
                ),
            })

        cwd = p.get("cwd")
        project = resolve_project(cwd)
        if project is not None:
            context_desc = []
            if project.framework:
                context_desc.append(f"{project.framework} project")
            else:
                context_desc.append("project")
            context_desc.append(f"'{project.project_name}'")
            if project.branch:
                context_desc.append(f"on branch '{project.branch}'")

            reason_str = f"Process {pid} cwd ({cwd!r}) is inside {' '.join(context_desc)}"

            inferences.append({
                "type": "project_context",
                "pid": pid,
                "git_root": project.git_root,
                "project_name": project.project_name,
                "branch": project.branch,
                "remote_origin": project.remote_origin,
                "is_worktree": project.is_worktree,
                "manifest_path": project.manifest_path,
                "manifest_type": project.manifest_type,
                "framework": project.framework,
                "confidence": "high" if project.manifest_path else "medium",
                "reason": reason_str,
            })

    for d in obs_docker:
        inferences.append({
            "type": "docker_isolation",
            "container_name": d["container_name"],
            "container_id": d["container_id"],
            "confidence": "high",
            "reason": (
                f"Container '{d['container_name']}' isolates the socket in a "
                f"virtual network namespace mapped to host port {d['host_port']}"
            ),
        })

    # ------------------------------------------------------------------
    # 3. Risks
    # ------------------------------------------------------------------
    risks: list[dict] = []

    # Wildcard bind risk
    for b in local_bindings:
        laddr_ip = b.laddr.split(":")[0] if ":" in b.laddr else b.laddr
        if laddr_ip in ("0.0.0.0", "::", "*"):
            risks.append({
                "type": "public_exposure",
                "message": (
                    f"Socket {b.laddr} bound to wildcard address ({laddr_ip})"
                    " - exposed to local network"
                ),
                "severity": "WARNING",
            })

    # Safety-shield informational risks (read-only annotation)
    protected_ports, protected_procs = resolve_protected_sets(config)

    is_protected_port = port in protected_ports
    is_protected_process = False
    protected_pids: list[int] = []

    if is_protected_port:
        risks.append({
            "type": "protected_port",
            "message": (
                f"Port {port} is listed in protected ports. "
                "Termination will be blocked by default."
            ),
            "severity": "IMPORTANT",
        })

    for p in obs_processes:
        base_name = p["name"].lower().split(" (")[0]
        if base_name in protected_procs:
            is_protected_process = True
            protected_pids.append(p["pid"])
            risks.append({
                "type": "protected_process",
                "message": (
                    f"PID {p['pid']} ({p['name']}) is listed in protected processes. "
                    "Termination will be blocked by default."
                ),
                "severity": "IMPORTANT",
            })

    for inf in inferences:
        if inf["type"] == "process_manager":
            risks.append({
                "type": "auto_restart",
                "message": (
                    f"PID {inf['pid']} is managed by {inf['manager']}. "
                    "Standard termination will trigger auto-restart."
                ),
                "severity": "WARNING",
            })

    # ------------------------------------------------------------------
    # 4. Recommendations (informational — caller decides to act)
    # ------------------------------------------------------------------
    recommendations: list[dict] = []

    safety_blocks = (is_protected_port or is_protected_process) and not bypass_safety

    if not is_blocked:
        recommendations.append({
            "action": "bind",
            "command": None,
            "reason": "Port is free and available to bind.",
            "safe": True,
        })
    elif safety_blocks:
        reasons = []
        if is_protected_port:
            reasons.append(f"Port {port} is protected")
        if is_protected_process:
            reasons.append(
                f"Process(es) {', '.join(map(str, protected_pids))} are protected"
            )
        recommendations.append({
            "action": "abort",
            "command": None,
            "reason": (
                f"Safety Shield Active: {' and '.join(reasons)}. "
                "Termination aborted. To override, re-run with --bypass-safety."
            ),
            "safe": False,
        })
    elif obs_docker:
        d = obs_docker[0]
        recommendations.append({
            "action": "stop_docker_container",
            "command": f"kport kill {port} --docker-action stop",
            "reason": (
                f"Port belongs to Docker container '{d['container_name']}'. "
                "Stop container cleanly to release port."
            ),
            "safe": True,
        })
    else:
        pm_managed = [inf for inf in inferences if inf["type"] == "process_manager"]
        if pm_managed:
            pm = pm_managed[0]
            if pm["manager"] == "systemd":
                cmd = f"systemctl stop {pm['name']}"
            elif pm["manager"] == "pm2":
                cmd = f"pm2 stop {pm['name']}"
            elif pm["manager"] == "supervisor":
                cmd = f"supervisorctl stop {pm['name']}"
            elif pm["manager"] == "windows-service":
                services = pm["name"].split(",")
                if len(services) == 1:
                    cmd = f"Stop-Service -Name {services[0]}"
                else:
                    cmd = " ; ".join(f"Stop-Service -Name {s}" for s in services)
            else:
                cmd = f"kport kill {port}"
            recommendations.append({
                "action": "stop_service",
                "command": cmd,
                "reason": (
                    f"Process is managed by {pm['manager']}. "
                    "Stopping via the service manager prevents auto-restart."
                ),
                "safe": True,
            })
        elif obs_type == "local-unknown":
            recommendations.append({
                "action": "escalate_privileges",
                "command": (
                    f"sudo kport diagnose {port}"
                    if sys.platform != "win32"
                    else "Run terminal as Administrator"
                ),
                "reason": (
                    "Port is occupied but owning process is not visible. "
                    "Diagnose with administrator/root privileges."
                ),
                "safe": True,
            })
        else:
            pids_str = " ".join(str(p["pid"]) for p in obs_processes)
            recommendations.append({
                "action": "kill_processes",
                "command": f"kport kill {port}",
                "reason": f"Terminate standard local process(es) on port (PIDs: {pids_str})",
                "safe": True,
            })

    suggested_alt = suggest_next_free_port(port, count=1, inspector=inspector, proto=proto) if is_blocked else []

    return {
        "port": port,
        "blocked": is_blocked,
        "observations": observations,
        "inferences": inferences,
        "risks": risks,
        "recommendations": recommendations,
        "alternative_port": suggested_alt[0] if suggested_alt else None,
    }


def suggest_next_free_port(
    start_port: int,
    count: int = 1,
    max_attempts: int = 20,
    inspector: BaseInspector | None = None,
    proto: str = "tcp",
) -> list[int]:
    """Find the next available port(s) starting from start_port + 1 up to start_port + max_attempts."""
    if inspector is None:
        from .inspectors import get_inspector

        inspector = get_inspector()

    available: list[int] = []
    for candidate in range(start_port + 1, min(start_port + max_attempts + 1, 65536)):
        pids = inspector.find_pids_on_port(candidate, proto=proto)
        bindings = inspector.find_bindings_on_port(candidate, proto=proto)
        docker_hits = docker_mappings_for_host_port(candidate)
        if not pids and not bindings and not docker_hits:
            available.append(candidate)
            if len(available) >= count:
                break
    return available


# ---------------------------------------------------------------------------
# Doctor / Environment Diagnostics
# ---------------------------------------------------------------------------

def _doctor_platform_info() -> dict:
    """Return a minimal, non-sensitive snapshot of the inspection environment."""
    import platform as _plat

    from . import inspectors as _insp

    using_psutil = _insp.USING_PSUTIL
    psutil_accessible = _insp.USING_PSUTIL and _insp._psutil_accessible()

    return {
        "os": _plat.system(),
        "os_version": _plat.version(),
        "python": _plat.python_version(),
        "inspector_backend": "psutil" if psutil_accessible else "fallback",
        "psutil_available": using_psutil,
        "psutil_accessible": psutil_accessible,
    }


def _doctor_capabilities(platform_info: dict) -> dict:
    """Assess what the current backend can and cannot inspect."""
    limitations: list[str] = []
    notes: list[str] = []

    if not platform_info["psutil_available"]:
        limitations.append(
            "psutil is not installed — using OS-native fallback inspector"
        )
    elif not platform_info["psutil_accessible"]:
        limitations.append(
            "psutil is installed but cannot enumerate connections "
            "(possible AppArmor/snap restriction) — using fallback inspector"
        )

    if platform_info["os"] == "Windows":
        notes.append("Process owner resolution uses tasklist and ctypes (no /proc)")
    elif platform_info["os"] == "Darwin":
        notes.append(
            "macOS: connection enumeration uses lsof if psutil is unavailable"
        )

    return {
        "can_inspect_connections": True,
        "can_inspect_listeners": True,
        "can_inspect_processes": True,
        "limitations": limitations,
        "notes": notes,
    }


def _doctor_listener_findings(
    bindings: list,
    findings: list[dict],
) -> list[dict]:
    """Analyse the listening-port snapshot; populate *findings* in-place."""
    from collections import defaultdict

    port_groups: dict[int, list] = defaultdict(list)
    for b in bindings:
        port_groups[b.port].append(b)

    listeners_out = []
    for b in bindings:
        ip = b.laddr.rsplit(":", 1)[0] if ":" in b.laddr else b.laddr
        is_wildcard = ip in ("0.0.0.0", "::", "*")
        is_localhost = ip in ("127.0.0.1", "::1")

        entry = {
            "port": b.port,
            "address": b.laddr,
            "pid": b.pid,
            "process_name": b.process_name,
            "state": b.state,
            "proto": b.proto,
            "wildcard": is_wildcard,
            "localhost_only": is_localhost,
        }
        listeners_out.append(entry)

        if is_wildcard:
            findings.append({
                "severity": "WARNING",
                "category": "listener",
                "message": (
                    f"Port {b.port} is bound to wildcard address ({ip}), "
                    f"exposing it to the local network"
                    + (f" — observed process: {b.process_name}" if b.process_name else "")
                ),
            })

    for port, group in port_groups.items():
        ips = {
            (b.laddr.rsplit(":", 1)[0] if ":" in b.laddr else b.laddr)
            for b in group
        }
        if "0.0.0.0" in ips and "::" in ips:
            findings.append({
                "severity": "INFO",
                "category": "listener",
                "message": (
                    f"Port {port} has both IPv4 (0.0.0.0) and IPv6 (::) wildcard "
                    "listeners — dual-stack binding (normal for many servers)"
                ),
            })

    return listeners_out


def _doctor_connection_summary(conns: list) -> dict:
    """Produce a concise connection-state summary."""
    from collections import Counter

    state_counts: Counter = Counter()
    for c in conns:
        state_counts[c.state] += 1

    return {
        "total": len(conns),
        "LISTEN": state_counts.get("LISTEN", 0),
        "ESTABLISHED": state_counts.get("ESTABLISHED", 0),
        "TIME_WAIT": state_counts.get("TIME_WAIT", 0),
        "CLOSE_WAIT": state_counts.get("CLOSE_WAIT", 0),
        "other": sum(
            v for k, v in state_counts.items()
            if k not in ("LISTEN", "ESTABLISHED", "TIME_WAIT", "CLOSE_WAIT")
        ),
    }


def _doctor_process_findings(
    bindings: list,
    inspector: BaseInspector,
    findings: list[dict],
) -> list[dict]:
    """Collect enriched process info for each unique listener PID."""
    seen_pids: set[int] = set()
    proc_entries: list[dict] = []

    for b in bindings:
        pid = b.pid
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)

        try:
            info = inspector.get_process_info(pid)
        except Exception:
            info = None

        proc_entry: dict = {
            "pid": pid,
            "name": info.name if info else b.process_name or "unknown",
            "user": info.user if info else None,
            "ppid": info.ppid if info else None,
            "cwd": info.cwd if info else None,
            "start_time": info.start_time if info else None,
        }

        pm_info: dict | None = None
        try:
            pm_info = detect_process_manager(pid, proc_entry["name"])
        except Exception:
            pass

        if pm_info:
            proc_entry["service_manager"] = {
                "manager": pm_info["manager"],
                "name": pm_info["name"],
            }
            findings.append({
                "severity": "INFO",
                "category": "service",
                "message": (
                    f"PID {pid} ({proc_entry['name']}) appears to be managed by "
                    f"{pm_info['manager']} service '{pm_info['name']}' — "
                    "stopping via process manager is preferred over direct kill"
                ),
            })
        else:
            proc_entry["service_manager"] = None

        cwd = proc_entry.get("cwd")
        project_info: dict | None = None
        try:
            proj = resolve_project(cwd)
        except Exception:
            proj = None

        if proj:
            project_info = {
                "git_root": proj.git_root,
                "project_name": proj.project_name,
                "branch": proj.branch,
                "remote_origin": proj.remote_origin,
                "is_worktree": proj.is_worktree,
                "manifest_path": proj.manifest_path,
                "manifest_type": proj.manifest_type,
                "framework": proj.framework,
            }
            context_desc = []
            if proj.framework:
                context_desc.append(f"framework {proj.framework}")
            context_desc.append(f"'{proj.project_name or proj.git_root}'")
            if proj.branch:
                context_desc.append(f"on branch '{proj.branch}'")

            findings.append({
                "severity": "INFO",
                "category": "project",
                "message": (
                    f"PID {pid} ({proc_entry['name']}) is associated with "
                    f"{' '.join(context_desc)}"
                ),
            })

        proc_entry["project"] = project_info
        proc_entries.append(proc_entry)

    return proc_entries


def _doctor_docker_section(
    bindings: list,
    findings: list[dict],
) -> dict:
    """Query Docker for host-port mappings relevant to listening ports."""
    if not docker_available():
        findings.append({
            "severity": "INFO",
            "category": "docker",
            "message": "Docker CLI not found on PATH — Docker container inspection skipped",
        })
        return {"available": False, "containers": []}

    try:
        all_mappings = list_docker_mappings()
    except Exception:
        findings.append({
            "severity": "INFO",
            "category": "docker",
            "message": (
                "Docker is available but could not be queried "
                "(daemon not running or permission denied)"
            ),
        })
        return {"available": True, "daemon_accessible": False, "containers": []}

    if not all_mappings:
        return {"available": True, "daemon_accessible": True, "containers": []}

    listener_ports = {b.port for b in bindings}
    relevant = [m for m in all_mappings if m.host_port in listener_ports]

    for m in relevant:
        findings.append({
            "severity": "INFO",
            "category": "docker",
            "message": (
                f"Host port {m.host_port} is mapped to container "
                f"'{m.container_name}' (port {m.container_port}/{m.proto})"
            ),
        })

    containers_out = [
        {
            "container_id": m.container_id[:12],
            "container_name": m.container_name,
            "image": m.image,
            "status": m.status,
            "host_port": m.host_port,
            "container_port": m.container_port,
            "proto": m.proto,
        }
        for m in all_mappings
    ]
    return {"available": True, "daemon_accessible": True, "containers": containers_out}


def run_doctor(inspector: BaseInspector) -> dict[str, Any]:
    """Aggregate an environment-wide diagnostic report.

    Returns a plain dict with keys:
      platform, capabilities, listeners, connection_summary,
      processes, docker, findings

    Read-only — no destructive operations.
    """
    findings: list[dict] = []

    # 1. Platform / capabilities
    try:
        platform_info = _doctor_platform_info()
    except Exception:
        platform_info = {"os": "unknown", "inspector_backend": "unknown"}

    try:
        capabilities = _doctor_capabilities(platform_info)
    except Exception:
        capabilities = {"limitations": ["Could not assess capabilities"], "notes": []}

    # 2. Listeners
    bindings: list = []
    try:
        bindings = inspector.list_listening(proto="tcp")
    except Exception as exc:
        findings.append({
            "severity": "WARNING",
            "category": "capability",
            "message": f"Could not enumerate listening ports: {exc}",
        })

    listeners_out = _doctor_listener_findings(bindings, findings)

    # 3. Connection summary
    conns: list = []
    try:
        conns = inspector.list_connections()
    except Exception as exc:
        findings.append({
            "severity": "WARNING",
            "category": "capability",
            "message": f"Could not enumerate network connections: {exc}",
        })

    conn_summary = _doctor_connection_summary(conns)

    # 4 & 5. Process / service / project context per listener
    proc_entries: list[dict] = []
    try:
        proc_entries = _doctor_process_findings(bindings, inspector, findings)
    except Exception as exc:
        findings.append({
            "severity": "WARNING",
            "category": "capability",
            "message": f"Could not resolve process context: {exc}",
        })

    # 6. Docker
    docker_section: dict = {}
    try:
        docker_section = _doctor_docker_section(bindings, findings)
    except Exception as exc:
        findings.append({
            "severity": "WARNING",
            "category": "capability",
            "message": f"Docker inspection failed: {exc}",
        })
        docker_section = {"available": False, "containers": []}

    # 7. High-level recommendations
    public_listeners = [ln for ln in listeners_out if ln.get("wildcard")]
    if public_listeners:
        findings.append({
            "severity": "RECOMMENDATION",
            "category": "listener",
            "message": (
                f"{len(public_listeners)} listener(s) bound to wildcard address. "
                "If intended for local development only, bind to 127.0.0.1 instead."
            ),
        })

    capability_issues = [f for f in findings if f.get("category") == "capability"]
    if capability_issues:
        findings.append({
            "severity": "RECOMMENDATION",
            "category": "capability",
            "message": (
                "Some inspection subsystems reported limitations. "
                "Run with elevated privileges (sudo / Administrator) for full visibility."
            ),
        })

    return {
        "platform": platform_info,
        "capabilities": capabilities,
        "listeners": listeners_out,
        "connection_summary": conn_summary,
        "processes": proc_entries,
        "docker": docker_section,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# Conflict Detection
# ---------------------------------------------------------------------------

def detect_conflicts(inspector: BaseInspector) -> list[dict[str, Any]]:
    """Detect Docker/local port conflicts.

    Returns a list of conflict dicts, each with keys:
      port, docker, local

    Read-only — no destructive operations.
    """
    docker_maps = list_docker_mappings()
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
            conflicts.append({
                "port": m.host_port,
                "docker": asdict(m),
                "local": non_docker_pids,
            })
    return conflicts


# ---------------------------------------------------------------------------
# Connection Filtering
# ---------------------------------------------------------------------------

def filter_connections(
    inspector: BaseInspector,
    pid: int | None = None,
    process: str | None = None,
    port: int | None = None,
    state: str | None = None,
    max_results: int = 500,
) -> list[dict[str, Any]]:
    """Retrieve and filter active network connections.

    Returns a list of serialisable connection dicts. Read-only.

    Parameters
    ----------
    inspector:
        Active inspector.
    pid:
        If given, only connections with this PID.
    process:
        If given, case-insensitive substring match on process name.
    port:
        If given, connections where local_port or remote_port equals this.
    state:
        If given, exact case-insensitive state match (e.g. ``"ESTABLISHED"``).
    max_results:
        Hard cap on the number of results returned. Prevents unbounded responses
        on machines with thousands of connections. Default: 500.
    """
    conns = inspector.list_connections()

    if pid is not None:
        conns = [c for c in conns if c.pid == pid]

    if process:
        p_lower = process.lower()
        conns = [
            c for c in conns
            if c.process_name and p_lower in c.process_name.lower()
        ]

    if port is not None:
        conns = [
            c for c in conns
            if c.local_port == port or c.remote_port == port
        ]

    if state:
        s_upper = state.upper()
        conns = [c for c in conns if c.state and c.state.upper() == s_upper]

    # Apply hard cap to prevent unbounded responses
    conns = conns[:max_results]

    result = [
        {
            "pid": c.pid,
            "process_name": c.process_name,
            "protocol": c.proto,
            "local_address": c.local_address,
            "local_port": c.local_port,
            "remote_address": c.remote_address,
            "remote_port": c.remote_port,
            "state": c.state,
        }
        for c in conns
    ]
    return result
