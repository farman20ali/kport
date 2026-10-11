"""
Model Context Protocol (MCP) server implementation for kport.
Implements standard stdio-based tool calls with zero dependencies.
Incorporates a strict safety shield to prevent AI agents from killing critical system ports.
"""
from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import __version__, audit
from .diagnostics import (
    detect_conflicts as _detect_conflicts_data,
)
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
    docker_mappings_for_host_port,
    list_docker_mappings,
)
from .inspectors import get_inspector
from .port_utils import poll_until_free as _poll_until_free
from .project import resolve_project
from .safety import check_safety_policy, load_kport_config

# R10 fix: use shared constants from kport.constants (single source of truth).
# MCP and CLI now share identical default protection lists.


def load_mcp_config() -> dict:
    """Load configuration dictionary from default kport config locations."""
    return load_kport_config()


def log(msg: str) -> None:
    """Print debug output directly to stderr to avoid corrupting JSON-RPC on stdout."""
    print(f"[kport-mcp] {msg}", file=sys.stderr, flush=True)


TOOLS = [
    {
        "name": "list_ports",
        "description": "Lists all active listening ports on the host machine, including both local processes and Docker containers with their PIDs, names, and states.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "inspect_port",
        "description": "Returns detailed information about the local process or Docker container holding a specific port.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "The port number to inspect.",
                }
            },
            "required": ["port"],
        },
    },
    {
        "name": "kill_port",
        "description": "Frees up a port by terminating the local process or executing a Docker container action if the port is owned by Docker.",
        "annotations": {"destructiveHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "The port number to free up.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to force-kill (SIGKILL/fuser fallback) the process if graceful SIGTERM fails.",
                },
                "docker_action": {
                    "type": "string",
                    "enum": ["stop", "restart", "rm"],
                    "default": "stop",
                    "description": "The action to perform if the port belongs to a Docker container (stop, restart, or remove container).",
                },
            },
            "required": ["port"],
        },
    },
    {
        "name": "diagnose_port",
        "description": "Runs a detailed semantic diagnostic analysis on a specific port, providing structured observations, inferred service relationships/manager context, process risks, and remediation recommendations.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "The port number to diagnose.",
                },
                "proto": {
                    "type": "string",
                    "enum": ["tcp", "udp", "both"],
                    "default": "tcp",
                    "description": "The protocol type to scan.",
                }
            },
            "required": ["port"],
        },
    },
    {
        "name": "list_connections",
        "description": (
            "Lists active network connections on the host, optionally filtered by PID, "
            "process name, port, or connection state. Results are bounded by max_results "
            "(default 500). If the returned count equals max_results the full set may be "
            "larger — narrow the filter or reduce max_results to paginate."
        ),
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "Only return connections owned by this PID.",
                },
                "process": {
                    "type": "string",
                    "description": "Case-insensitive substring match on process name.",
                },
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "Return connections where local_port or remote_port equals this value.",
                },
                "state": {
                    "type": "string",
                    "description": "Exact case-insensitive connection state (e.g. ESTABLISHED, LISTEN, TIME_WAIT).",
                },
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000,
                    "default": 500,
                    "description": "Hard cap on returned connections. Defaults to 500.",
                },
            },
        },
    },
    {
        "name": "conflicts",
        "description": "Runs conflict detection on the host machine to identify ports mapped to Docker containers that are also bound/occupied by native host processes.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "doctor",
        "description": "Runs an environment-wide diagnostic check, summarizing platform/capabilities, active listening sockets, connection counts, process/service context, and high-level configuration/risk findings.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "stop_service",
        "description": "Cleanly stops a process-manager controlled service (systemd, PM2, supervisor, Windows Service) occupying a port. Does not support force process termination.",
        "annotations": {"destructiveHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "The port number to stop the controlling service for.",
                },
                "dry_run": {
                    "type": "boolean",
                    "default": False,
                    "description": "Show stop command without running it.",
                }
            },
            "required": ["port"],
        },
    },
    {
        "name": "find_project",
        "description": (
            "Resolves Git project metadata (name, root, branch, remote origin) "
            "from a PID's working directory or a directly supplied filesystem path. "
            "Credentials are never exposed in the returned remote_origin field."
        ),
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "Resolve the working directory from this PID.",
                },
                "path": {
                    "type": "string",
                    "description": "Resolve directly from this filesystem path.",
                },
            },
        },
    },
    {
        "name": "suggest_resolution",
        "description": (
            "Returns structured remediation recommendations for a port without executing "
            "any destructive operation. Use this before kill_port or stop_service to "
            "understand the safest remediation path for a blocked port."
        ),
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "The port number to generate recommendations for.",
                },
                "proto": {
                    "type": "string",
                    "enum": ["tcp", "udp", "both"],
                    "default": "tcp",
                    "description": "Protocol filter for the underlying diagnostic.",
                },
            },
            "required": ["port"],
        },
    },
    {
        "name": "inspect_pid",
        "description": "Returns detailed information about a specific local process by PID, including its name, command line, user, parent PID, working directory, and active network port bindings.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "The process ID (PID) to inspect.",
                }
            },
            "required": ["pid"],
        },
    },
    {
        "name": "kill_pid",
        "description": "Terminates a specific local process by PID. Includes safety checks to prevent terminating critical system processes.",
        "annotations": {"destructiveHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "pid": {
                    "type": "integer",
                    "description": "The process ID (PID) to terminate.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to force-kill (SIGKILL) the process if graceful SIGTERM fails.",
                },
                "kill_tree": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to kill the process and all of its descendants.",
                },
            },
            "required": ["pid"],
        },
    },
    {
        "name": "kill_process",
        "description": "Kills processes by name. Safety checks are performed on all target PIDs.",
        "annotations": {"destructiveHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The process name to match.",
                },
                "exact": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to require an exact process name match.",
                },
                "force": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to force-kill matching processes.",
                },
                "kill_tree": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to kill matching processes and all descendants.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "find_alternative_port",
        "description": "Finds the next available free port(s) starting from a specified port number. Useful when a port is occupied.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "port": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 65535,
                    "description": "The starting port number to search after.",
                },
                "count": {
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Number of alternative free ports to return.",
                },
            },
            "required": ["port"],
        },
    },
    {
        "name": "get_audit_history",
        "description": "Returns recent audit log history of destructive operations performed by kport.",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum number of audit entries to return.",
                }
            },
        },
    },
]


def handle_inspect_pid(inspector, pid: int) -> dict[str, Any]:
    """Execute inspect_pid tool request (read-only)."""
    info = inspector.get_process_info(pid)
    if not info:
        raise ValueError(f"Process with PID {pid} not found or details unavailable.")

    # Find active port bindings for this PID
    all_bindings = inspector.list_listening(proto="both")
    pid_bindings = [
        {
            "port": b.port,
            "proto": b.proto,
            "state": b.state or "LISTEN",
            "address": b.laddr
        }
        for b in all_bindings
        if b.pid == pid
    ]

    from dataclasses import asdict
    return {
        "pid": pid,
        "process": asdict(info) if info else None,
        "active_ports": pid_bindings
    }


def handle_kill_pid(inspector, pid: int, force: bool = False, kill_tree: bool = False) -> dict[str, Any]:
    """Execute kill_pid tool request (destructive) under safety shield validations."""
    cfg = load_mcp_config()
    allowed, reason = check_safety_policy(
        port=None,
        pids=[pid],
        inspector=inspector,
        bypass_safety=False,
        config=cfg if cfg else None,
    )
    if not allowed:
        return {
            "success": False,
            "message": reason,
        }

    info = inspector.get_process_info(pid)
    pname = info.name if info else "Unknown"

    if kill_tree:
        ok, msg = inspector.kill_process_tree(
            pid,
            graceful_timeout=3.0,
            force=force,
            dry_run=False,
            assume_yes=True,
        )
    else:
        ok, msg = inspector.kill_pid(
            pid,
            graceful_timeout=3.0,
            force=force,
            dry_run=False,
            assume_yes=True,
        )

    audit.log_kill_pid(
        pid, pname, dry_run=False, success=ok, message=msg
    )

    return {"success": ok, "pid": pid, "message": msg}


def handle_kill_process(
    inspector, name: str, exact: bool = False, force: bool = False, kill_tree: bool = False
) -> dict[str, Any]:
    """Execute kill_process tool request (destructive) under safety shield validations."""
    pids = inspector.find_pids_by_name(name, exact=exact)
    if not pids:
        return {"success": True, "message": f"No processes found matching '{name}'."}

    cfg = load_mcp_config()
    allowed, reason = check_safety_policy(
        port=None,
        pids=pids,
        inspector=inspector,
        bypass_safety=False,
        config=cfg if cfg else None,
    )
    if not allowed:
        return {
            "success": False,
            "message": reason,
        }

    killed = []
    failed = []
    for pid in pids:
        info = inspector.get_process_info(pid)
        pname = info.name if info else name

        if kill_tree:
            ok, msg = inspector.kill_process_tree(
                pid,
                graceful_timeout=3.0,
                force=force,
                dry_run=False,
                assume_yes=True,
            )
        else:
            ok, msg = inspector.kill_pid(
                pid,
                graceful_timeout=3.0,
                force=force,
                dry_run=False,
                assume_yes=True,
            )

        audit.log_kill_pid(
            pid, pname, dry_run=False, success=ok, message=msg
        )

        if ok:
            killed.append({"pid": pid, "msg": msg})
        else:
            failed.append({"pid": pid, "msg": msg})

    return {
        "success": len(failed) == 0,
        "killed": killed,
        "failed": failed,
    }


def handle_list_ports(inspector) -> dict[str, Any]:
    """Execute list_ports tool request."""
    local_bindings = inspector.list_listening()
    docker_maps = list_docker_mappings()

    local_list = []
    for b in local_bindings:
        local_list.append(
            {
                "port": b.port,
                "pid": b.pid,
                "process_name": b.process_name,
                "state": b.state,
                "address": b.laddr,
            }
        )

    docker_list = []
    for d in docker_maps:
        docker_list.append(
            {
                "port": d.host_port,
                "container_name": d.container_name,
                "image": d.image,
                "status": d.status,
                "container_port": d.container_port,
                "protocol": d.proto,
            }
        )

    return {"local_processes": local_list, "docker_containers": docker_list}


def handle_inspect_port(inspector, port: int) -> dict[str, Any]:
    """Execute inspect_port tool request."""
    if not (1 <= port <= 65535):
        raise ValueError(f"Port {port} is out of bounds (1-65535)")

    local_bindings = inspector.find_bindings_on_port(port)
    docker_hits = docker_mappings_for_host_port(port)
    pids = inspector.find_pids_on_port(port)

    response = {"port": port}

    if docker_hits:
        m = docker_hits[0]
        response["type"] = "docker"
        response["container_name"] = m.container_name
        response["container_id"] = m.container_id
        response["image"] = m.image
        response["status"] = m.status
        response["container_port"] = m.container_port
        response["protocol"] = m.proto
        return response

    if not pids:
        if local_bindings:
            response["type"] = "local-unknown"
            response["message"] = (
                "Port in use, but owning PID is not visible (needs administrative privileges)"
            )
        else:
            response["type"] = "free"
    else:
        response["type"] = "local"
        proc_list = []
        for pid in pids:
            info = inspector.get_process_info(pid)
            if info:
                proc_list.append(
                    {
                        "pid": pid,
                        "name": info.name,
                        "exe": info.exe,
                        "cmdline": info.cmdline,
                        "user": info.user,
                    }
                )
            else:
                proc_list.append({"pid": pid, "message": "process details unavailable"})
        response["processes"] = proc_list

    return response


def handle_kill_port(
    inspector, port: int, force: bool = False, docker_action: str = "stop"
) -> dict[str, Any]:
    """Execute kill_port tool request under safety shield validations."""
    if not (1 <= port <= 65535):
        raise ValueError(f"Port {port} is out of bounds (1-65535)")

    # 1. Load configuration and retrieve target pids
    cfg = load_mcp_config()
    inspector = get_inspector()
    pids = inspector.find_pids_on_port(port)

    # 2. Unified safety policy check
    # MCP server NEVER bypasses safety (so bypass_safety=False).
    allowed, reason = check_safety_policy(
        port=port,
        pids=pids,
        inspector=inspector,
        bypass_safety=False,
        config=cfg if cfg else None,
    )
    if not allowed:
        return {
            "success": False,
            "message": reason,
        }

    docker_hits = docker_mappings_for_host_port(port)

    # 3. Docker container path
    if docker_hits:
        m = docker_hits[0]
        if docker_action == "rm" and not force:
            return {
                "success": False,
                "type": "docker",
                "container_name": m.container_name,
                "action": docker_action,
                "message": "Removing a Docker container is irreversible and requires force parameter to be explicitly set to True.",
            }
        ok, msg = docker_action_on_container(
            m.container_id, docker_action, dry_run=False
        )
        return {
            "success": ok,
            "type": "docker",
            "container_name": m.container_name,
            "action": docker_action,
            "message": msg,
        }

    # 4. No PIDs path
    if not pids:
        local_bindings = inspector.find_bindings_on_port(port)
        if local_bindings:
            return {
                "success": False,
                "type": "local-unknown",
                "message": "Port is active but owning PID is not visible. Start the MCP server with elevated privileges (admin/sudo).",
            }
        return {"success": True, "message": f"Port {port} is already free."}

    # 5. Local process escalated kill
    ok, msg = inspector.kill_port(
        port,
        graceful_timeout=3.0,
        force=force,
        dry_run=False,
        debug=True,
        assume_yes=True,
    )
    # Emit audit record for every MCP-triggered local port kill (success or failure).
    audit.log_kill_port(
        port=port,
        pids=pids,
        dry_run=False,
        success=ok,
        message=msg,
    )
    return {"success": ok, "type": "local", "pids_targeted": pids, "message": msg}


def handle_list_connections(
    inspector,
    pid: int | None = None,
    process: str | None = None,
    port: int | None = None,
    state: str | None = None,
    max_results: int = 500,
) -> dict[str, Any]:
    """Execute list_connections tool request."""
    max_results = max(1, min(max_results, 2000))  # clamp to schema bounds
    conns = _filter_connections_data(
        inspector,
        pid=pid,
        process=process,
        port=port,
        state=state,
        max_results=max_results,
    )
    return {
        "connections": conns,
        "count": len(conns),
        # If we hit the cap exactly the full set may be larger — caller should
        # narrow their filter rather than assume this is the complete set.
        "capped": len(conns) >= max_results,
    }


def handle_diagnose_port(inspector, port: int, proto: str = "tcp") -> dict[str, Any]:
    """Execute diagnose_port tool request."""
    if not (1 <= port <= 65535):
        raise ValueError(f"Port {port} is out of bounds (1-65535)")
    cfg = load_mcp_config()
    return _diagnose_port_data(port, inspector, proto=proto, config=cfg)


def handle_find_project(
    inspector, pid: int | None = None, path: str | None = None
) -> dict[str, Any]:
    """Execute find_project tool request.

    Resolves Git project metadata from a PID's working directory or a directly
    supplied filesystem path.  Read-only — no destructive operations.
    """
    from dataclasses import asdict

    cwd: str | None = path
    if pid is not None:
        try:
            info = inspector.get_process_info(pid)
            cwd = info.cwd if info else None
        except Exception:  # noqa: BLE001
            cwd = None

    if not cwd:
        return {"project": None, "reason": "No working directory available"}

    try:
        proj = resolve_project(cwd)
    except Exception:  # noqa: BLE001
        proj = None

    if proj is None:
        return {"project": None, "reason": f"No Git repository found in {cwd!r}"}

    return {"project": asdict(proj)}


def handle_suggest_resolution(
    inspector, port: int, proto: str = "tcp"
) -> dict[str, Any]:
    """Execute suggest_resolution tool request.

    Returns structured remediation recommendations for a blocked port without
    executing any destructive operation.  Read-only.
    """
    if not (1 <= port <= 65535):
        raise ValueError(f"Port {port} is out of bounds (1-65535)")
    cfg = load_mcp_config()
    data = _diagnose_port_data(port, inspector, proto=proto, config=cfg)
    return {
        "port": port,
        "blocked": data["blocked"],
        "recommendations": data["recommendations"],
    }


def handle_conflicts(inspector) -> list[dict[str, Any]]:
    """Execute conflicts tool request."""
    return _detect_conflicts_data(inspector)


def handle_doctor(inspector) -> dict[str, Any]:
    """Execute doctor tool request."""
    return _run_doctor_data(inspector)


def handle_find_alternative_port(
    inspector, port: int, count: int = 1
) -> dict[str, Any]:
    """Execute find_alternative_port tool request."""
    from .diagnostics import suggest_next_free_port

    alt = suggest_next_free_port(start_port=port, count=count, inspector=inspector)
    return {
        "start_port": port,
        "alternative_ports": alt,
        "found": len(alt) > 0,
    }


def handle_get_audit_history(limit: int = 20) -> dict[str, Any]:
    """Execute get_audit_history tool request."""
    entries = audit.read_recent_audit_events(limit=limit)
    return {
        "count": len(entries),
        "entries": entries,
    }


def handle_stop_service(inspector, port: int, dry_run: bool = False) -> dict[str, Any]:
    """Execute stop_service tool request under safety shield validations."""
    if not (1 <= port <= 65535):
        raise ValueError(f"Port {port} is out of bounds (1-65535)")

    # 1. Resolve initial state (PIDs, manager)
    cfg = load_mcp_config()
    data = _diagnose_port_data(port, inspector, config=cfg)
    pids = inspector.find_pids_on_port(port)

    # 2. Safety policy check
    # MCP server NEVER bypasses safety (bypass_safety=False).
    allowed, reason = check_safety_policy(
        port=port,
        pids=pids,
        inspector=inspector,
        bypass_safety=False,
        config=cfg if cfg else None,
    )
    if not allowed:
        return {
            "success": False,
            "message": reason,
            "verified_free": False,
            "requires_force": False,
        }

    is_blocked = data["blocked"]
    if not is_blocked:
        return {
            "success": True,
            "port": port,
            "manager": "none",
            "service_name": "",
            "command": "",
            "verified_free": True,
            "dry_run": dry_run,
            "message": f"Port {port} is already free.",
        }

    inferences = data["inferences"]
    pm_managed = [inf for inf in inferences if inf["type"] == "process_manager"]
    if not pm_managed:
        return {
            "success": False,
            "port": port,
            "manager": "none",
            "service_name": "",
            "command": "",
            "verified_free": False,
            "requires_force": True,
            "message": f"No supported process manager detected for port {port}.",
        }

    pm = pm_managed[0]
    manager = pm["manager"]
    service_name = pm["name"]

    # 3. Call stop_service domain layer
    from .service_actions import stop_service
    res = stop_service(
        manager=manager,
        service_name=service_name,
        timeout=30.0,
        dry_run=dry_run,
    )

    # 4. Verify post-action state
    verified_free = False
    if res.success and not dry_run:
        verified_free = _poll_until_free(port, 3.0, inspector)
    elif dry_run:
        verified_free = False

    # 5. Audit log
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

    return {
        "success": res.success,
        "port": port,
        "manager": manager,
        "service_name": service_name,
        "command": res.command_executed,
        "verified_free": verified_free,
        "dry_run": dry_run,
        "message": res.message,
        "requires_force": res.success and not verified_free and not dry_run,
    }


def run_mcp_server() -> None:
    """Run standard stdio MCP JSON-RPC execution loop."""
    log("kport MCP Server successfully started.")

    # P3 fix: create ONE inspector for the entire server lifetime.
    # Creating a new instance per-call was wasteful; more importantly,
    # a single instance will correctly clear its own per-query cache
    # (via _clear_cache()) on each call rather than allocating fresh objects.
    inspector = get_inspector()

    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")
            params = req.get("params", {})

            if method == "initialize":
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2026-07-28",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "kport", "version": __version__},
                    },
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

            elif method == "tools/list":
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})

                try:
                    if tool_name == "list_ports":
                        result_data = handle_list_ports(inspector)
                    elif tool_name == "inspect_port":
                        target_port = int(arguments.get("port"))
                        result_data = handle_inspect_port(inspector, target_port)
                    elif tool_name == "kill_port":
                        target_port = int(arguments.get("port"))
                        force_flag = bool(arguments.get("force", False))
                        docker_act = str(arguments.get("docker_action", "stop"))
                        result_data = handle_kill_port(
                            inspector, target_port, force_flag, docker_act
                        )
                    elif tool_name == "list_connections":
                        result_data = handle_list_connections(
                            inspector,
                            pid=arguments.get("pid"),
                            process=arguments.get("process"),
                            port=arguments.get("port"),
                            state=arguments.get("state"),
                            max_results=int(arguments.get("max_results", 500)),
                        )
                    elif tool_name == "diagnose_port":
                        target_port = int(arguments.get("port"))
                        proto_val = str(arguments.get("proto", "tcp"))
                        result_data = handle_diagnose_port(inspector, target_port, proto_val)
                    elif tool_name == "conflicts":
                        result_data = handle_conflicts(inspector)
                    elif tool_name == "doctor":
                        result_data = handle_doctor(inspector)
                    elif tool_name == "stop_service":
                        target_port = int(arguments.get("port"))
                        dry_run_flag = bool(arguments.get("dry_run", False))
                        result_data = handle_stop_service(inspector, target_port, dry_run_flag)
                    elif tool_name == "find_project":
                        pid_arg = arguments.get("pid")
                        path_arg = arguments.get("path")
                        result_data = handle_find_project(
                            inspector,
                            pid=int(pid_arg) if pid_arg is not None else None,
                            path=str(path_arg) if path_arg is not None else None,
                        )
                    elif tool_name == "suggest_resolution":
                        target_port = int(arguments.get("port"))
                        proto_val = str(arguments.get("proto", "tcp"))
                        result_data = handle_suggest_resolution(
                            inspector, target_port, proto_val
                        )
                    elif tool_name == "inspect_pid":
                        target_pid = int(arguments.get("pid"))
                        result_data = handle_inspect_pid(inspector, target_pid)
                    elif tool_name == "kill_pid":
                        target_pid = int(arguments.get("pid"))
                        force_flag = bool(arguments.get("force", False))
                        tree_flag = bool(arguments.get("kill_tree", False))
                        result_data = handle_kill_pid(inspector, target_pid, force_flag, tree_flag)
                    elif tool_name == "kill_process":
                        p_name = str(arguments.get("name"))
                        exact_flag = bool(arguments.get("exact", False))
                        force_flag = bool(arguments.get("force", False))
                        tree_flag = bool(arguments.get("kill_tree", False))
                        result_data = handle_kill_process(inspector, p_name, exact_flag, force_flag, tree_flag)
                    elif tool_name == "find_alternative_port":
                        target_port = int(arguments.get("port"))
                        cnt_val = int(arguments.get("count", 1))
                        result_data = handle_find_alternative_port(inspector, target_port, cnt_val)
                    elif tool_name == "get_audit_history":
                        lim_val = int(arguments.get("limit", 20))
                        result_data = handle_get_audit_history(lim_val)
                    else:
                        raise ValueError(f"Unknown tool: {tool_name}")

                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(result_data, indent=2),
                                }
                            ],
                            "isError": (
                                not result_data.get("success", True)
                                if isinstance(result_data, dict) and "success" in result_data
                                else False
                            ),
                        },
                    }
                except Exception as ex:  # noqa: BLE001 - top-level tool execution handler (intentional)
                    log(f"Tool execution failed: {traceback.format_exc()}")
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": f"Error executing tool '{tool_name}': {ex!s}",
                                }
                            ],
                            "isError": True,
                        },
                    }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

            elif method == "notifications/initialized":
                # Standard client initialized notification
                pass

            else:
                if req_id is not None:
                    resp = {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    }
                    sys.stdout.write(json.dumps(resp) + "\n")
                    sys.stdout.flush()

        except Exception:  # noqa: BLE001 - top-level RPC framing (must not crash the server)
            log(f"RPC framing error: {traceback.format_exc()}")
