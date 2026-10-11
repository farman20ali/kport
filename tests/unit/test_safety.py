"""
Unit tests for centralized safety policy engine (tests/unit/test_safety.py).

Tests check_safety_policy(), resolve_protected_sets(), and load_kport_config()
across default and custom configuration scenarios.
"""

from __future__ import annotations

import pytest

from kport.constants import PROTECTED_PORTS, PROTECTED_PROCESS_NAMES
from kport.inspectors.base import ProcessInfo
from kport.safety import (
    SafetyDecision,
    check_safety_policy,
    resolve_protected_sets,
)
from tests.conftest import FakeInspector, _binding


@pytest.mark.unit
class TestSafetyPolicy:
    """Test core safety policy evaluation."""

    def test_unprotected_port_allowed(self):
        inspector = FakeInspector()
        decision = check_safety_policy(8080, [], inspector)
        assert decision.allowed is True
        assert decision.policy_source == "default"
        assert decision.reason == ""

    def test_default_protected_port_blocked(self):
        inspector = FakeInspector()
        for port in (22, 53, 80, 443):
            decision = check_safety_policy(port, [], inspector)
            assert decision.allowed is False
            assert decision.policy_source == "default"
            assert f"Port {port}" in decision.reason

    def test_custom_protected_port_blocked(self):
        inspector = FakeInspector()
        config = {"protected_ports": [8000, 9000]}
        decision = check_safety_policy(8000, [], inspector, config=config)
        assert decision.allowed is False
        assert decision.policy_source == "default"
        assert f"Port {8000}" in decision.reason

    def test_additive_configuration_preserves_default_protections(self):
        """User config extends default protected sets rather than replacing them."""
        inspector = FakeInspector()
        config = {"protected_ports": [9000], "protected_processes": ["custom_daemon"]}

        # Default protected port 22 must still be blocked
        decision_22 = check_safety_policy(22, [], inspector, config=config)
        assert decision_22.allowed is False
        assert decision_22.policy_source == "default"

        # Custom protected port 9000 must also be blocked
        decision_9000 = check_safety_policy(9000, [], inspector, config=config)
        assert decision_9000.allowed is False
        assert decision_9000.policy_source == "default"

    def test_protected_process_name_blocked(self):
        inspector = FakeInspector(
            pids_on_port={8080: [100]},
            process_info={100: ProcessInfo(pid=100, name="systemd", exe="/usr/lib/systemd/systemd")},
            bindings_on_port={8080: [_binding(8080, pid=100, name="systemd")]},
        )
        decision = check_safety_policy(8080, [100], inspector)
        assert decision.allowed is False
        assert "systemd" in decision.reason

    def test_custom_protected_process_case_insensitive(self):
        inspector = FakeInspector(
            pids_on_port={8080: [200]},
            process_info={200: ProcessInfo(pid=200, name="MyCriticalApp.exe", exe="C:\\app.exe")},
            bindings_on_port={8080: [_binding(8080, pid=200, name="MyCriticalApp.exe")]},
        )
        config = {"protected_processes": ["mycriticalapp.exe"]}
        decision = check_safety_policy(8080, [200], inspector, config=config)
        assert decision.allowed is False
        assert decision.policy_source == "default"

    def test_bypass_safety_flag_overrides_policy(self):
        inspector = FakeInspector()
        decision = check_safety_policy(22, [], inspector, bypass_safety=True)
        assert decision.allowed is True
        assert decision.policy_source == "bypass"

    def test_multiple_pids_any_protected_blocks_all(self):
        inspector = FakeInspector(
            pids_on_port={8080: [101, 102]},
            process_info={
                101: ProcessInfo(pid=101, name="node", exe="/usr/bin/node"),
                102: ProcessInfo(pid=102, name="sshd", exe="/usr/sbin/sshd"),
            },
            bindings_on_port={
                8080: [
                    _binding(8080, pid=101, name="node"),
                    _binding(8080, pid=102, name="sshd"),
                ]
            },
        )
        decision = check_safety_policy(8080, [101, 102], inspector)
        assert decision.allowed is False
        assert "sshd" in decision.reason

    def test_missing_or_empty_process_info_allows_operation(self):
        inspector = FakeInspector(
            pids_on_port={8080: [103]},
            process_info={},  # Process info lookup returns None
            bindings_on_port={8080: [_binding(8080, pid=103, name="dockerd")]},
        )
        decision = check_safety_policy(8080, [103], inspector)
        assert decision.allowed is True
        assert decision.reason == ""


@pytest.mark.unit
class TestResolveProtectedSets:
    """Test resolution and parsing of protected ports and process sets."""

    def test_none_config_returns_defaults(self):
        ports, procs = resolve_protected_sets(None)
        assert ports == PROTECTED_PORTS
        assert procs == {p.lower() for p in PROTECTED_PROCESS_NAMES}

    def test_additive_set_resolution(self):
        config = {
            "protected_ports": [3000, 4000],
            "protected_processes": ["CustomService", "worker"],
        }
        ports, procs = resolve_protected_sets(config)
        assert 3000 in ports
        assert 4000 in ports
        assert 22 in ports  # Default preserved
        assert "customservice" in procs
        assert "worker" in procs
        assert "sshd" in procs  # Default preserved

    def test_safety_decision_tuple_unpacking(self):
        """Ensure SafetyDecision supports legacy boolean unpacking (allowed, reason)."""
        dec = SafetyDecision(allowed=True, policy_source="default", reason="ok")
        allowed, reason = dec
        assert allowed is True
        assert reason == "ok"
