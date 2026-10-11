"""
Docker integration module for kport.
Optimized to gather all container mappings in a single, high-performance O(1) query.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class DockerPortMapping:
    container_id: str
    container_name: str
    image: str
    status: str
    host_ip: str | None
    host_port: int
    container_port: int
    proto: str


def _run_docker(args: list[str], debug: bool = False) -> subprocess.CompletedProcess:
    """Helper to run a safe subprocess Docker command."""
    if debug:
        print(f"[debug] docker {' '.join(args)}", file=sys.stderr)
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def docker_available() -> bool:
    """Return True if docker CLI is available on PATH."""
    return shutil.which("docker") is not None


def list_docker_mappings(debug: bool = False) -> list[DockerPortMapping]:
    """
    Return host-port mappings for running containers.
    Optimized to run exactly one docker ps query and parse published ports and labels in Python.
    """
    if not docker_available():
        return []

    # Get container ID, Names, Image, Status, Ports, and Labels in a single subprocess call
    ps = _run_docker(
        [
            "ps",
            "--no-trunc",
            "--format",
            "{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}\t{{.Labels}}",
        ],
        debug=debug,
    )

    if ps.returncode != 0:
        if debug:
            err = ps.stderr.strip()
            if "permission denied" in err.lower() or "cannot connect" in err.lower():
                print(
                    "[debug] Docker socket not accessible — is Docker running? Do you have permission (e.g. docker group)?",
                    file=sys.stderr,
                )
            else:
                print(f"[debug] docker ps failed: {err}", file=sys.stderr)
        return []

    mappings: list[DockerPortMapping] = []
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

        if not ports_str:
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

            # Resolve Docker-Compose service names if present in labels
            compose_project = None
            compose_service = None
            if labels_str:
                for label_kv in labels_str.split(","):
                    if "=" in label_kv:
                        try:
                            lk, lv = [x.strip() for x in label_kv.split("=", 1)]
                            if lk == "com.docker.compose.project":
                                compose_project = lv
                            elif lk == "com.docker.compose.service":
                                compose_service = lv
                        except ValueError:
                            continue

            display_name = name
            if compose_project and compose_service:
                display_name = f"{name} ({compose_project}/{compose_service})"

            mappings.append(
                DockerPortMapping(
                    container_id=container_id,
                    container_name=display_name,
                    image=image,
                    status=status,
                    host_ip=host_ip,
                    host_port=host_port,
                    container_port=container_port,
                    proto=proto,
                )
            )

    # Deduplicate (Docker often returns both IPv4 and IPv6 lines for same port mapping)
    seen = set()
    uniq: list[DockerPortMapping] = []
    for m in mappings:
        key = (m.container_id, m.host_port, m.container_port, m.proto)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(m)

    return sorted(uniq, key=lambda x: (x.host_port, x.container_name))


def docker_mappings_for_host_port(
    port: int, debug: bool = False
) -> list[DockerPortMapping]:
    """Retrieve Docker port mappings matching a specific host port."""
    return [m for m in list_docker_mappings(debug=debug) if m.host_port == port]


def docker_mappings_for_host_ports(
    ports: list[int], debug: bool = False
) -> dict[int, list[DockerPortMapping]]:
    """Retrieve Docker port mappings for a batch of host ports in a single O(1) query.

    Returns a dict mapping host_port -> list[DockerPortMapping].
    """
    target_ports = set(ports)
    all_mappings = list_docker_mappings(debug=debug)
    result: dict[int, list[DockerPortMapping]] = {p: [] for p in target_ports}
    for m in all_mappings:
        if m.host_port in target_ports:
            result[m.host_port].append(m)
    return result


def docker_action_on_container(
    container_id: str, action: str, dry_run: bool, debug: bool = False
) -> tuple[bool, str]:
    """
    Apply action (stop, restart, rm) on a Docker container.

    WARNING: 'rm' uses 'docker rm -f' which is irreversible — the container
    and its non-persistent state will be permanently deleted.
    """
    if dry_run:
        suffix = " [IRREVERSIBLE once confirmed]" if action == "rm" else ""
        return True, f"Dry-run: would docker {action} {container_id[:12]}{suffix}"
    if action == "stop":
        r = _run_docker(["stop", container_id], debug=debug)
    elif action == "restart":
        r = _run_docker(["restart", container_id], debug=debug)
    elif action == "rm":
        r = _run_docker(["rm", "-f", container_id], debug=debug)
    else:
        return False, f"Unknown docker action: {action}"

    if r.returncode == 0:
        return True, f"docker {action} succeeded"
    return False, (r.stderr or r.stdout or "").strip() or f"docker {action} failed"
