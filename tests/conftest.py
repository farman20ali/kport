"""
conftest.py – shared pytest fixtures and session-level setup.

psutil stub
-----------
kport ships psutil as an *optional* extra.  The CI test job installs only
``.[dev]`` (no psutil), so importing ``kport.inspectors.psutil_impl`` would
raise ``ModuleNotFoundError: No module named 'psutil'``.

We solve this by registering a lightweight stub in ``sys.modules`` *before*
any test module is collected.  Tests that exercise psutil-dependent behaviour
patch the individual attributes they need (e.g. ``psutil.Process``,
``psutil.net_connections``) on top of this stub, which works because
``patch()`` writes into the same module object.
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock


def _make_psutil_stub() -> types.ModuleType:
    """Return a minimal fake psutil module that satisfies psutil_impl imports."""
    stub = types.ModuleType("psutil")
    stub._is_stub = True

    # Constants referenced by psutil_impl
    stub.CONN_LISTEN = "LISTEN"
    stub.STATUS_ZOMBIE = "zombie"

    # Base and specific exception types
    stub.Error = type("Error", (Exception,), {})
    stub.NoSuchProcess = type("NoSuchProcess", (stub.Error,), {})
    stub.AccessDenied = type("AccessDenied", (stub.Error,), {})
    stub.ZombieProcess = type("ZombieProcess", (stub.Error,), {})
    stub.TimeoutExpired = type("TimeoutExpired", (stub.Error,), {})

    # Callable stubs – individual tests override these via patch()
    stub.net_connections = MagicMock(return_value=[])

    def _default_process(pid=None):
        proc = MagicMock()
        proc.pid = pid if pid is not None else os.getpid()
        proc.name.return_value = "python"
        proc.exe.return_value = sys.executable
        proc.cmdline.return_value = [sys.executable]
        proc.username.return_value = "user"
        proc.ppid.return_value = os.getppid()
        proc.cwd.return_value = os.getcwd()
        proc.create_time.return_value = 1700000000.0
        return proc

    stub.Process = MagicMock(side_effect=_default_process)
    stub.process_iter = MagicMock(return_value=iter([]))

    return stub


# Register the stub only when the real psutil is not installed.
if "psutil" not in sys.modules:
    try:
        import psutil as _real_psutil  # noqa: F401  (present in some envs)
    except ImportError:
        sys.modules["psutil"] = _make_psutil_stub()


# ---------------------------------------------------------------------------
# Shared Domain Fixtures & Helpers
# ---------------------------------------------------------------------------

import argparse
import json
from io import StringIO
from unittest.mock import patch

from kport.inspectors.base import (
    BaseInspector,
    ConnectionInfo,
    PortBinding,
)


class FakeInspector(BaseInspector):
    """Reusable mock inspector for testing domain, CLI, and MCP layers."""

    def __init__(
        self,
        pids_on_port=None,
        bindings_on_port=None,
        process_info=None,
        listening=None,
        connections=None,
    ):
        self._pids = pids_on_port or {}
        self._bindings = bindings_on_port or {}
        self._info = process_info or {}
        self._listening = listening or []
        self._connections = connections or []

    def find_pids_on_port(self, port: int, proto: str = "tcp"):
        if port in self._pids:
            return self._pids[port]
        p = proto.lower()
        return sorted({
            b.pid for b in self._listening
            if getattr(b, "port", None) == port
            and getattr(b, "pid", None)
            and (p == "both" or getattr(b, "proto", "tcp").lower() == p)
        })

    def find_bindings_on_port(self, port: int, proto: str = "tcp"):
        if port in self._bindings:
            return self._bindings[port]
        p = proto.lower()
        return [
            b for b in self._listening
            if getattr(b, "port", None) == port
            and (p == "both" or getattr(b, "proto", "tcp").lower() == p)
        ]

    def get_process_info(self, pid: int):
        return self._info.get(pid)

    def list_listening(self, proto: str = "tcp"):
        if proto == "both":
            return self._listening
        return [b for b in self._listening if getattr(b, "proto", "tcp") == proto]

    def list_connections(self):
        return self._connections

    def find_ports_by_process_name(self, name, exact=False, proto: str = "tcp"):
        return []

    def find_pids_by_name(self, name, exact=False):
        return []

    def kill_port(
        self,
        port,
        graceful_timeout=3.0,
        force=False,
        dry_run=False,
        debug=False,
        assume_yes=False,
        kill_tree=False,
        **kwargs,
    ):
        return True, f"Port {port} freed"

    def kill_pid(
        self,
        pid,
        graceful_timeout=3.0,
        force=False,
        dry_run=False,
        assume_yes=False,
        debug=False,
    ):
        return True, f"PID {pid} killed"


def _binding(
    port: int,
    pid: int | None = 1234,
    name: str | None = "node",
    proto: str = "tcp",
    laddr: str | None = None,
    state: str = "LISTEN",
) -> PortBinding:
    """Helper to create a PortBinding dataclass."""
    address = laddr if laddr else f"0.0.0.0:{port}"
    return PortBinding(
        port=port,
        family="inet",
        laddr=address,
        pid=pid,
        process_name=name,
        state=state,
        proto=proto,
    )


def _conn(
    pid: int | None = 100,
    name: str | None = "node",
    proto: str = "tcp",
    laddr: str = "127.0.0.1",
    lport: int = 8080,
    raddr: str = "127.0.0.1",
    rport: int | None = 50000,
    state: str = "ESTABLISHED",
) -> ConnectionInfo:
    """Helper to create a ConnectionInfo dataclass."""
    return ConnectionInfo(
        pid=pid,
        process_name=name,
        proto=proto,
        local_address=laddr,
        local_port=lport,
        remote_address=raddr,
        remote_port=rport,
        state=state,
    )


def _args(**kwargs) -> argparse.Namespace:
    """Helper to build CLI args namespace with safe defaults."""
    defaults = {
        "command": "inspect",
        "port": None,
        "ports": None,
        "range": None,
        "name": None,
        "json": False,
        "yes": True,
        "dry_run": False,
        "force": False,
        "debug": False,
        "interval": 1.0,
        "profile": None,
        "kill_tree": False,
        "wait_for_exit": None,
        "proto": "tcp",
        "until": None,
        "timeout": None,
        "docker_action": "stop",
        "bypass_safety": False,
        "filter_process": None,
        "state": None,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _send_mcp_messages(*messages: dict) -> list[dict]:
    """Feed JSON-RPC messages to the MCP server and collect responses."""
    from kport.mcp_server import run_mcp_server

    lines = [json.dumps(m) + "\n" for m in messages]
    captured = StringIO()
    with patch("sys.stdin", StringIO("".join(lines))), patch("sys.stdout", captured), patch("sys.stderr", StringIO()):
        run_mcp_server()
    return [json.loads(line) for line in captured.getvalue().strip().splitlines() if line.strip()]

