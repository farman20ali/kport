"""
Centralized safety policy for kport.

Single source of truth for:
  - configuration loading relevant to safety;
  - protected ports and processes;
  - destructive-operation safety decisions.

Consumed by CLI, TUI, and MCP — none of which should duplicate this logic.

Design constraints
------------------
* No dependency on cli.py or mcp_server.py (avoids circular imports).
* No terminal formatting / ANSI output.
* SafetyDecision supports tuple-unpacking for backward compatibility with
  existing callers that expect ``(bool, str)``.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .constants import PROTECTED_PORTS, PROTECTED_PROCESS_NAMES
from .inspectors import BaseInspector

# ---------------------------------------------------------------------------
# Configuration loading
# ---------------------------------------------------------------------------

def _default_config_paths() -> list[str]:
    home = os.path.expanduser("~")
    return [
        os.path.join(os.getcwd(), ".kport.json"),
        os.path.join(home, ".kport.json"),
        os.path.join(home, ".config", "kport", "config.json"),
    ]


def load_kport_config(config_path: str | None = None) -> dict[str, Any]:
    """Load kport JSON configuration from standard search paths.

    Non-fatal: a missing or malformed config file returns ``{}``.
    This loader is safe to call from MCP (no sys.exit, no ANSI output).
    """
    candidate_paths = [config_path] if config_path else _default_config_paths()
    for path in candidate_paths:
        if not path:
            continue
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            # Non-fatal — caller receives {} and defaults apply.
            pass
    return {}


# ---------------------------------------------------------------------------
# Safety decision
# ---------------------------------------------------------------------------

class SafetyDecision:
    """Result of a centralized safety policy check.

    Attributes
    ----------
    allowed : bool
        ``True`` if the operation may proceed.
    reason : str
        Human-readable explanation (empty string when allowed).
    policy_source : str
        Which rule caused the decision: ``"default"``, ``"config"``,
        or ``"bypass"``.

    Backward compatibility
    ----------------------
    Supports ``allowed, reason = decision`` tuple-unpacking so existing
    callers that expect ``(bool, str)`` continue to work without changes.
    """

    __slots__ = ("allowed", "policy_source", "reason")

    def __init__(
        self,
        allowed: bool,
        reason: str = "",
        policy_source: str = "default",
    ) -> None:
        self.allowed = allowed
        self.reason = reason
        self.policy_source = policy_source

    # Tuple-unpack: ``ok, msg = decision``
    def __iter__(self):
        return iter((self.allowed, self.reason))

    def __bool__(self) -> bool:
        return self.allowed

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SafetyDecision(allowed={self.allowed!r}, "
            f"reason={self.reason!r}, policy_source={self.policy_source!r})"
        )


# ---------------------------------------------------------------------------
# Core policy
# ---------------------------------------------------------------------------

def resolve_protected_sets(
    config: dict[str, Any] | None = None,
) -> tuple[set[int], set[str]]:
    """Return the effective (ports, process-names) protection sets.

    Configuration values are ADDITIVE — they extend the hard-coded defaults,
    never replace them.  This preserves the Phase 2 correctness guarantee.
    """
    cfg = config or {}

    protected_ports: set[int] = set(PROTECTED_PORTS)
    config_ports = cfg.get("protected_ports")
    if isinstance(config_ports, list):
        protected_ports.update(
            p for p in config_ports if isinstance(p, int)
        )

    protected_procs: set[str] = set(PROTECTED_PROCESS_NAMES)
    config_procs = cfg.get("protected_processes")
    if isinstance(config_procs, list):
        protected_procs.update(
            p.lower() for p in config_procs if isinstance(p, str)
        )

    return protected_ports, protected_procs


def check_safety_policy(
    port: int | None,
    pids: list[int],
    inspector: BaseInspector,
    bypass_safety: bool = False,
    config: dict[str, Any] | None = None,
) -> SafetyDecision:
    """Evaluate whether a destructive operation on *port* / *pids* is safe.

    Parameters
    ----------
    port:
        Port number to check, or ``None`` when checking only by PIDs.
    pids:
        PIDs associated with the target.  Each is looked up via *inspector*.
    inspector:
        Used to resolve process information for PID-based checks.
    bypass_safety:
        When ``True`` the check logs the bypass but still allows the action.
        MCP callers **must not** pass ``True`` here.
    config:
        Pre-loaded configuration dict.  When ``None``, no config-level
        overrides are applied (defaults still apply).

    Returns
    -------
    SafetyDecision
        Supports ``allowed, reason = decision`` unpacking.
    """
    protected_ports, protected_procs = resolve_protected_sets(config)

    # 1. Port-level protection
    if port is not None and port in protected_ports:
        if bypass_safety:
            return SafetyDecision(True, "", policy_source="bypass")
        return SafetyDecision(
            False,
            (
                f"Security Shield Active: Port {port} is a protected port. "
                "Action aborted. Use --bypass-safety to override."
            ),
            policy_source="default",
        )

    # 2. Process-level protection
    for pid in pids:
        try:
            info = inspector.get_process_info(pid)
            if info:
                base_name = info.name.lower().split(" (")[0]
                if base_name in protected_procs:
                    if bypass_safety:
                        return SafetyDecision(True, "", policy_source="bypass")
                    return SafetyDecision(
                        False,
                        (
                            f"Security Shield Active: PID {pid} runs critical "
                            f"process '{info.name}' which is protected. "
                            "Action aborted. Use --bypass-safety to override."
                        ),
                        policy_source="default",
                    )
        except (OSError, AttributeError, ValueError, IndexError):
            pass

    return SafetyDecision(True, "", policy_source="default")
