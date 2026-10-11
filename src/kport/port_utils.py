"""
port_utils.py — Neutral port polling utilities for kport.

Provides infrastructure-level port-state helpers that are independent of
both the CLI presentation layer and the MCP transport layer.

Design constraints
------------------
* No imports from cli.py, cli_commands.py, cli_utils.py, or formatter.py.
* No imports from mcp_server.py.
* No interactive behaviour, no terminal output.
* Safe to import from any layer (domain, transport, application).
"""

from __future__ import annotations

import subprocess
import time

from .inspectors import BaseInspector


def poll_until_free(
    port: int,
    timeout: float,
    inspector: BaseInspector,
    interval: float = 0.2,
) -> bool:
    """Poll *port* until it has no active bindings, up to *timeout* seconds.

    Returns
    -------
    True
        If the port becomes free (no active bindings) within *timeout* seconds.
    False
        If *timeout* expires while the port is still bound.

    Parameters
    ----------
    port:
        The port number to monitor.
    timeout:
        Maximum number of seconds to wait.
    inspector:
        Active :class:`~kport.inspectors.BaseInspector` used to query bindings.
    interval:
        Sleep duration in seconds between successive polls. Defaults to 0.2 s.
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            bindings = inspector.find_bindings_on_port(port)
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            bindings = []
        if not bindings:
            return True
        time.sleep(interval)
    # Final check after the loop
    try:
        bindings = inspector.find_bindings_on_port(port)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        bindings = []
    return len(bindings) == 0
