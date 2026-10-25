"""
mcp_rules.py — MCP v2 Safety Shield Rulebuilder for kport (Phase 4).

This module provides a comprehensive, configurable safety rule system for
multi-tenant and enterprise AI agent environments.  It extends the basic
``safety.py`` shield with:

* **Named rule sets**: Operators define named policies (e.g. "ci", "prod",
  "dev") with different allow/deny scopes.
* **Per-agent identity controls**: Rules can be scoped to specific MCP client
  identifiers so that different AI agents (Copilot, Claude, Cursor) receive
  different permissions.
* **Granular action controls**: Separate allow/deny lists for ``kill_port``,
  ``kill_pid``, ``kill_process``, ``stop_service``, and ``swap`` actions.
* **Audit integration**: Every rule evaluation is optionally logged via the
  existing ``audit`` module.
* **Zero runtime dependencies**: Pure Python, relies only on the standard
  library and existing kport modules.

File format
-----------
Rules are stored as JSON in the kport config directory (``~/.kport/mcp_rules.json``
or ``%APPDATA%\\kport\\mcp_rules.json`` on Windows).  Example::

    {
      "version": 1,
      "default_policy": "deny",
      "rules": [
        {
          "id": "allow-dev-ports",
          "description": "Allow kill/stop on ephemeral dev ports",
          "agent_ids": ["*"],
          "actions": ["kill_port", "stop_service"],
          "port_ranges": [[3000, 9999]],
          "effect": "allow"
        },
        {
          "id": "deny-system",
          "description": "Always deny touching system ports",
          "agent_ids": ["*"],
          "actions": ["*"],
          "port_ranges": [[1, 1023]],
          "effect": "deny"
        }
      ]
    }

Design constraints
------------------
* No imports from cli.py, cli_commands.py, cli_utils.py, or formatter.py.
* Safe to import from mcp_server.py and domain modules.
"""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Config file location
# ---------------------------------------------------------------------------

_SYSTEM = platform.system()


def _default_rules_path() -> str:
    """Return the platform-appropriate default rules file path."""
    if _SYSTEM == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.path.expanduser("~")
    return os.path.join(base, ".kport", "mcp_rules.json")


RULES_PATH_ENV = "KPORT_MCP_RULES"


def get_rules_path() -> str:
    """Return the active rules file path (env override or default)."""
    return os.environ.get(RULES_PATH_ENV, _default_rules_path())


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

WILDCARD = "*"

# Recognised action names (also accepts "*" for all)
KNOWN_ACTIONS = frozenset(
    {
        "kill_port",
        "kill_pid",
        "kill_process",
        "stop_service",
        "swap",
    }
)


@dataclass
class McpRule:
    """A single named safety rule."""

    id: str
    effect: str  # "allow" | "deny"
    description: str = ""
    # Which MCP agent IDs this rule applies to ("*" = all)
    agent_ids: list[str] = field(default_factory=lambda: [WILDCARD])
    # Which actions this rule matches ("*" = all)
    actions: list[str] = field(default_factory=lambda: [WILDCARD])
    # Port ranges [[low, high], ...] that this rule covers ([] = any port)
    port_ranges: list[list[int]] = field(default_factory=list)
    # Explicit PID list ([] = any PID)
    pids: list[int] = field(default_factory=list)
    # Explicit process name patterns ([] = any process)
    process_names: list[str] = field(default_factory=list)
    # Whether this rule is currently active
    enabled: bool = True

    def matches_agent(self, agent_id: str | None) -> bool:
        """Return True if this rule applies to *agent_id*."""
        if not self.enabled:
            return False
        if WILDCARD in self.agent_ids:
            return True
        return bool(agent_id) and agent_id in self.agent_ids  # type: ignore[operator]

    def matches_action(self, action: str) -> bool:
        """Return True if this rule covers *action*."""
        if not self.enabled:
            return False
        if WILDCARD in self.actions:
            return True
        return action in self.actions

    def matches_port(self, port: int | None) -> bool:
        """Return True if *port* is covered by this rule's port_ranges."""
        if not self.port_ranges:
            return True  # No restriction = matches any port
        if port is None:
            return True
        return any(low <= port <= high for low, high in self.port_ranges)

    def matches_pid(self, pid: int | None) -> bool:
        """Return True if *pid* is covered by this rule's pid list."""
        if not self.pids:
            return True
        if pid is None:
            return True
        return pid in self.pids

    def matches_process(self, process_name: str | None) -> bool:
        """Return True if *process_name* matches this rule's process_names list."""
        if not self.process_names:
            return True
        if not process_name:
            return True
        lname = process_name.lower()
        return any(p.lower() in lname or lname in p.lower() for p in self.process_names)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RuleSet:
    """Collection of MCP safety rules with a default policy."""

    version: int = 1
    default_policy: str = "allow"  # "allow" | "deny"
    rules: list[McpRule] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "default_policy": self.default_policy,
            "rules": [r.to_dict() for r in self.rules],
        }


# ---------------------------------------------------------------------------
# Evaluation result
# ---------------------------------------------------------------------------


@dataclass
class RuleDecision:
    """Result of a rule evaluation."""

    allowed: bool
    reason: str
    matched_rule_id: str | None = None
    evaluated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "matched_rule_id": self.matched_rule_id,
            "evaluated_at": self.evaluated_at,
        }


# ---------------------------------------------------------------------------
# Loader / persister
# ---------------------------------------------------------------------------

_SCHEMA_EXAMPLE: dict[str, Any] = {
    "version": 1,
    "default_policy": "allow",
    "rules": [],
}


def load_rules(path: str | None = None) -> RuleSet:
    """
    Load a ``RuleSet`` from *path* (defaults to ``get_rules_path()``).

    Returns an empty ``RuleSet`` with ``default_policy="allow"`` if the file
    does not exist or cannot be parsed.
    """
    fpath = path or get_rules_path()
    try:
        with open(fpath, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        return RuleSet()
    except (json.JSONDecodeError, OSError):
        return RuleSet()

    rules = []
    for rdata in raw.get("rules", []):
        try:
            rules.append(
                McpRule(
                    id=rdata.get("id", "unnamed"),
                    effect=rdata.get("effect", "allow"),
                    description=rdata.get("description", ""),
                    agent_ids=rdata.get("agent_ids", [WILDCARD]),
                    actions=rdata.get("actions", [WILDCARD]),
                    port_ranges=rdata.get("port_ranges", []),
                    pids=rdata.get("pids", []),
                    process_names=rdata.get("process_names", []),
                    enabled=rdata.get("enabled", True),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue

    return RuleSet(
        version=raw.get("version", 1),
        default_policy=raw.get("default_policy", "allow"),
        rules=rules,
    )


def save_rules(ruleset: RuleSet, path: str | None = None) -> None:
    """Persist *ruleset* to *path* as JSON (creates parent dirs if needed)."""
    fpath = path or get_rules_path()
    os.makedirs(os.path.dirname(fpath), exist_ok=True)
    with open(fpath, "w", encoding="utf-8") as fh:
        json.dump(ruleset.to_dict(), fh, indent=2)


# ---------------------------------------------------------------------------
# Rule evaluation engine
# ---------------------------------------------------------------------------


def evaluate(
    ruleset: RuleSet,
    action: str,
    port: int | None = None,
    pid: int | None = None,
    process_name: str | None = None,
    agent_id: str | None = None,
) -> RuleDecision:
    """
    Evaluate *action* against *ruleset* and return a ``RuleDecision``.

    Rules are evaluated in order.  The first matching rule wins (first-match
    semantics).  If no rule matches, the ``ruleset.default_policy`` is applied.

    Parameters
    ----------
    ruleset:
        The ``RuleSet`` to evaluate against.
    action:
        The action being requested (e.g. ``"kill_port"``).
    port:
        The target port number, if applicable.
    pid:
        The target PID, if applicable.
    process_name:
        The target process name, if applicable.
    agent_id:
        The MCP client / agent identifier from the request context.
    """
    for rule in ruleset.rules:
        if not rule.enabled:
            continue
        if not rule.matches_agent(agent_id):
            continue
        if not rule.matches_action(action):
            continue
        if not rule.matches_port(port):
            continue
        if not rule.matches_pid(pid):
            continue
        if not rule.matches_process(process_name):
            continue

        # Rule matched
        allowed = rule.effect == "allow"
        reason = rule.description or f"Matched rule '{rule.id}' (effect={rule.effect})"
        return RuleDecision(
            allowed=allowed,
            reason=reason,
            matched_rule_id=rule.id,
        )

    # No rule matched — apply default policy
    default_allow = ruleset.default_policy == "allow"
    return RuleDecision(
        allowed=default_allow,
        reason=f"Default policy: {ruleset.default_policy}",
        matched_rule_id=None,
    )


# ---------------------------------------------------------------------------
# Built-in rule templates
# ---------------------------------------------------------------------------


def make_dev_ruleset() -> RuleSet:
    """Return a permissive ruleset suitable for local development environments."""
    return RuleSet(
        default_policy="allow",
        rules=[
            McpRule(
                id="deny-privileged",
                effect="deny",
                description="Block all destructive actions on privileged ports (1-1023)",
                agent_ids=[WILDCARD],
                actions=["kill_port", "kill_pid", "kill_process", "stop_service", "swap"],
                port_ranges=[[1, 1023]],
            ),
        ],
    )


def make_ci_ruleset() -> RuleSet:
    """Return a CI/CD-appropriate ruleset allowing only ephemeral port ranges."""
    return RuleSet(
        default_policy="deny",
        rules=[
            McpRule(
                id="allow-ephemeral",
                effect="allow",
                description="Allow kill/stop on ephemeral ports (1024-65535) in CI",
                agent_ids=[WILDCARD],
                actions=["kill_port", "stop_service"],
                port_ranges=[[1024, 65535]],
            ),
            McpRule(
                id="readonly-always",
                effect="allow",
                description="Always allow read-only diagnostic actions",
                agent_ids=[WILDCARD],
                actions=["list_ports", "inspect_port", "diagnose_port", "doctor"],
                port_ranges=[],
            ),
        ],
    )


def make_enterprise_ruleset(
    allowed_agent_ids: list[str] | None = None,
) -> RuleSet:
    """
    Return a strict enterprise ruleset.

    Only explicitly listed *allowed_agent_ids* may perform destructive actions
    on ports above 1024.  All other agents are deny-all for destructive ops.
    """
    agents = allowed_agent_ids or []
    return RuleSet(
        default_policy="deny",
        rules=[
            McpRule(
                id="trusted-agents-dev-ports",
                effect="allow",
                description="Trusted agents may act on non-privileged ports",
                agent_ids=agents,
                actions=["kill_port", "kill_pid", "kill_process", "stop_service", "swap"],
                port_ranges=[[1024, 65535]],
            ),
            McpRule(
                id="readonly-always",
                effect="allow",
                description="All agents may perform read-only operations",
                agent_ids=[WILDCARD],
                actions=["list_ports", "inspect_port", "diagnose_port",
                         "list_connections", "conflicts", "doctor",
                         "find_project", "suggest_resolution", "get_audit_history",
                         "get_safety_rules", "find_alternative_port"],
                port_ranges=[],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Summary helper
# ---------------------------------------------------------------------------


def summarize_ruleset(ruleset: RuleSet) -> dict[str, Any]:
    """Return a compact summary dict for display in MCP tool responses."""
    return {
        "version": ruleset.version,
        "default_policy": ruleset.default_policy,
        "rule_count": len(ruleset.rules),
        "enabled_rule_count": sum(1 for r in ruleset.rules if r.enabled),
        "rules": [
            {
                "id": r.id,
                "effect": r.effect,
                "enabled": r.enabled,
                "actions": r.actions,
                "port_ranges": r.port_ranges,
                "description": r.description,
            }
            for r in ruleset.rules
        ],
    }
