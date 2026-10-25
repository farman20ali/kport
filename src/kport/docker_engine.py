"""
Docker & Container integration module for kport.
Maintains backward compatibility by re-exporting unified container engine
capabilities from container_engine.py.
"""
from __future__ import annotations

import subprocess
import sys

from .container_engine import (
    ContainerPortMapping,
    container_action_on_container,
    container_engine_available,
    container_mappings_for_host_port,
    container_mappings_for_host_ports,
    docker_available,
    list_container_mappings,
    podman_available,
)

# Backward-compatible alias
DockerPortMapping = ContainerPortMapping


def _run_docker(args: list[str], debug: bool = False) -> subprocess.CompletedProcess:
    """Helper to run a safe subprocess Docker command (kept for backward compatibility)."""
    if debug:
        print(f"[debug] docker {' '.join(args)}", file=sys.stderr)
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def list_docker_mappings(debug: bool = False) -> list[DockerPortMapping]:
    """Return host-port mappings for running containers across Docker and Podman."""
    return list_container_mappings(debug=debug)


def docker_mappings_for_host_port(
    port: int, debug: bool = False
) -> list[DockerPortMapping]:
    """Retrieve container port mappings matching a specific host port."""
    return container_mappings_for_host_port(port, debug=debug)


def docker_mappings_for_host_ports(
    ports: list[int], debug: bool = False
) -> dict[int, list[DockerPortMapping]]:
    """Retrieve container port mappings for a batch of host ports in a single query."""
    return container_mappings_for_host_ports(ports, debug=debug)


def docker_action_on_container(
    container_id: str, action: str, dry_run: bool, debug: bool = False
) -> tuple[bool, str]:
    """Apply action (stop, restart, rm) on a Docker or Podman container."""
    return container_action_on_container(
        container_id, action, dry_run, engine="docker", debug=debug
    )


__all__ = [
    "ContainerPortMapping",
    "DockerPortMapping",
    "_run_docker",
    "container_action_on_container",
    "container_engine_available",
    "container_mappings_for_host_port",
    "container_mappings_for_host_ports",
    "docker_action_on_container",
    "docker_available",
    "docker_mappings_for_host_port",
    "docker_mappings_for_host_ports",
    "list_container_mappings",
    "list_docker_mappings",
    "podman_available",
]
