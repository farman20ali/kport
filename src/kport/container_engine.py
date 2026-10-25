"""
Container integration module for kport.
Unifies Docker and Podman container engines with Docker Compose project awareness.
Optimized to gather all container mappings in a single, high-performance O(1) query.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass
class ContainerPortMapping:
    container_id: str
    container_name: str
    image: str
    status: str
    host_ip: str | None
    host_port: int
    container_port: int
    proto: str
    engine: str = "docker"
    compose_project: str | None = None
    compose_service: str | None = None
    networks: list[str] = field(default_factory=list)


# Backward-compatible alias
DockerPortMapping = ContainerPortMapping


def _run_container_cli(
    engine: str, args: list[str], debug: bool = False
) -> subprocess.CompletedProcess:
    """Helper to run a safe subprocess container engine command (docker or podman)."""
    if debug:
        print(f"[debug] {engine} {' '.join(args)}", file=sys.stderr)
    return subprocess.run([engine, *args], capture_output=True, text=True, check=False)


def docker_available() -> bool:
    """Return True if docker CLI is available on PATH."""
    return shutil.which("docker") is not None


def podman_available() -> bool:
    """Return True if podman CLI is available on PATH."""
    return shutil.which("podman") is not None


def container_engine_available() -> bool:
    """Return True if either docker or podman is available."""
    return docker_available() or podman_available()


def _query_engine_mappings(
    engine: str, debug: bool = False
) -> list[ContainerPortMapping]:
    """Query a specific container engine (docker or podman) for active port mappings."""
    # Query container ID, Names, Image, Status, Ports, Labels, and Networks in a single call
    ps = _run_container_cli(
        engine,
        [
            "ps",
            "--no-trunc",
            "--format",
            "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Labels}}\t{{.Networks}}",
        ],
        debug=debug,
    )

    if ps.returncode != 0:
        if debug:
            err = ps.stderr.strip()
            if "permission denied" in err.lower() or "cannot connect" in err.lower():
                print(
                    f"[debug] {engine} socket not accessible — is {engine} running? Do you have permissions?",
                    file=sys.stderr,
                )
            else:
                print(f"[debug] {engine} ps failed: {err}", file=sys.stderr)
        return []

    mappings: list[ContainerPortMapping] = []
    for line in (ps.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue

        container_id = parts[0].strip()
        name = parts[1].strip()
        image = parts[2].strip()
        status = parts[3].strip()
        ports_str = parts[4].strip()
        labels_str = parts[5].strip() if len(parts) >= 6 else ""
        networks_str = parts[6].strip() if len(parts) >= 7 else ""

        if not ports_str:
            continue

        # Parse networks
        networks = [net.strip() for net in networks_str.split(",") if net.strip()]

        # Resolve Compose project and service names if present in labels
        compose_project: str | None = None
        compose_service: str | None = None
        if labels_str:
            for label_kv in labels_str.split(","):
                if "=" in label_kv:
                    try:
                        lk, lv = [x.strip() for x in label_kv.split("=", 1)]
                        if lk in ("com.docker.compose.project", "io.podman.compose.project"):
                            compose_project = lv
                        elif lk in ("com.docker.compose.service", "io.podman.compose.service"):
                            compose_service = lv
                    except ValueError:
                        continue

        # Format of .Ports is comma-separated entries:
        # e.g., "0.0.0.0:80->80/tcp, :::80->80/tcp, 0.0.0.0:443->443/tcp"
        for entry in ports_str.split(","):
            entry = entry.strip()
            if "->" not in entry:
                continue

            left, right = [e.strip() for e in entry.split("->", 1)]

            # Host binding: "0.0.0.0:8080" or "[::]:8080"
            m_left = re.search(r":(\d+)$", left)
            if not m_left:
                continue
            host_port = int(m_left.group(1))
            host_ip = left[: left.rfind(":")].strip() or None

            # Container binding: "80/tcp"
            m_right = re.match(r"^(\d+)\/(tcp|udp)$", right)
            if not m_right:
                continue
            container_port = int(m_right.group(1))
            proto = m_right.group(2)

            display_name = name
            if compose_project and compose_service:
                display_name = f"{name} ({compose_project}/{compose_service})"

            mappings.append(
                ContainerPortMapping(
                    container_id=container_id,
                    container_name=display_name,
                    image=image,
                    status=status,
                    host_ip=host_ip,
                    host_port=host_port,
                    container_port=container_port,
                    proto=proto,
                    engine=engine,
                    compose_project=compose_project,
                    compose_service=compose_service,
                    networks=networks,
                )
            )

    return mappings


def list_container_mappings(
    engine: str | None = None,
    compose_project: str | None = None,
    compose_service: str | None = None,
    debug: bool = False,
) -> list[ContainerPortMapping]:
    """
    Return host-port mappings for running containers across Docker and Podman.
    Optionally filter by engine ('docker' or 'podman'), compose_project, and compose_service.
    """
    mappings: list[ContainerPortMapping] = []

    engines_to_check: list[str] = []
    if engine:
        engines_to_check.append(engine.lower())
    else:
        if docker_available():
            engines_to_check.append("docker")
        if podman_available():
            engines_to_check.append("podman")

    for eng in engines_to_check:
        mappings.extend(_query_engine_mappings(eng, debug=debug))

    # Deduplicate (engines often return both IPv4 and IPv6 lines for the same container mapping)
    seen = set()
    uniq: list[ContainerPortMapping] = []
    for m in mappings:
        key = (m.engine, m.container_id, m.host_port, m.container_port, m.proto)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(m)

    # Apply Compose project and service filters if requested
    if compose_project:
        cp_lower = compose_project.lower()
        uniq = [m for m in uniq if m.compose_project and cp_lower in m.compose_project.lower()]

    if compose_service:
        cs_lower = compose_service.lower()
        uniq = [m for m in uniq if m.compose_service and cs_lower in m.compose_service.lower()]

    return sorted(uniq, key=lambda x: (x.host_port, x.container_name))


# Backward-compatible function name
def list_docker_mappings(debug: bool = False) -> list[ContainerPortMapping]:
    """Backward-compatible wrapper for listing container mappings."""
    return list_container_mappings(debug=debug)


def container_mappings_for_host_port(
    port: int, engine: str | None = None, debug: bool = False
) -> list[ContainerPortMapping]:
    """Retrieve container port mappings matching a specific host port."""
    return [
        m for m in list_container_mappings(engine=engine, debug=debug) if m.host_port == port
    ]


# Backward-compatible function name
def docker_mappings_for_host_port(
    port: int, debug: bool = False
) -> list[ContainerPortMapping]:
    """Retrieve Docker/Podman port mappings matching a specific host port."""
    return container_mappings_for_host_port(port, debug=debug)


def container_mappings_for_host_ports(
    ports: list[int], engine: str | None = None, debug: bool = False
) -> dict[int, list[ContainerPortMapping]]:
    """Retrieve container port mappings for a batch of host ports in a single query."""
    target_ports = set(ports)
    all_mappings = list_container_mappings(engine=engine, debug=debug)
    result: dict[int, list[ContainerPortMapping]] = {p: [] for p in target_ports}
    for m in all_mappings:
        if m.host_port in target_ports:
            result[m.host_port].append(m)
    return result


# Backward-compatible function name
def docker_mappings_for_host_ports(
    ports: list[int], debug: bool = False
) -> dict[int, list[ContainerPortMapping]]:
    """Retrieve container port mappings for a batch of host ports."""
    return container_mappings_for_host_ports(ports, debug=debug)


def container_action_on_container(
    container_id: str,
    action: str,
    dry_run: bool,
    engine: str = "docker",
    debug: bool = False,
) -> tuple[bool, str]:
    """
    Apply action (stop, restart, rm) on a Docker or Podman container.

    WARNING: 'rm' uses 'rm -f' which is irreversible — the container
    and its non-persistent state will be permanently deleted.
    """
    if dry_run:
        suffix = " [IRREVERSIBLE once confirmed]" if action == "rm" else ""
        return True, f"Dry-run: would {engine} {action} {container_id[:12]}{suffix}"
    if action == "stop":
        r = _run_container_cli(engine, ["stop", container_id], debug=debug)
    elif action == "restart":
        r = _run_container_cli(engine, ["restart", container_id], debug=debug)
    elif action == "rm":
        r = _run_container_cli(engine, ["rm", "-f", container_id], debug=debug)
    else:
        return False, f"Unknown {engine} action: {action}"

    if r.returncode == 0:
        return True, f"{engine} {action} succeeded"
    return False, (r.stderr or r.stdout or "").strip() or f"{engine} {action} failed"


# Backward-compatible function name
def docker_action_on_container(
    container_id: str, action: str, dry_run: bool, debug: bool = False
) -> tuple[bool, str]:
    """Apply action (stop, restart, rm) on a Docker/Podman container."""
    return container_action_on_container(
        container_id, action, dry_run, engine="docker", debug=debug
    )
