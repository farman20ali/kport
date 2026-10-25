"""
test_mcp_rules.py — Unit tests for kport.mcp_rules (Phase 4 MCP v2 safety rulebuilder).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from kport.mcp_rules import (
    McpRule,
    RuleDecision,
    RuleSet,
    evaluate,
    load_rules,
    make_ci_ruleset,
    make_dev_ruleset,
    make_enterprise_ruleset,
    save_rules,
    summarize_ruleset,
)

# ---------------------------------------------------------------------------
# McpRule matching helpers
# ---------------------------------------------------------------------------


def test_rule_matches_wildcard_agent():
    rule = McpRule(id="r1", effect="allow", agent_ids=["*"])
    assert rule.matches_agent("cursor")
    assert rule.matches_agent("claude")
    assert rule.matches_agent(None)


def test_rule_matches_specific_agent():
    rule = McpRule(id="r1", effect="allow", agent_ids=["cursor", "copilot"])
    assert rule.matches_agent("cursor")
    assert not rule.matches_agent("claude")


def test_rule_matches_wildcard_action():
    rule = McpRule(id="r1", effect="allow", actions=["*"])
    assert rule.matches_action("kill_port")
    assert rule.matches_action("stop_service")


def test_rule_matches_specific_action():
    rule = McpRule(id="r1", effect="allow", actions=["kill_port"])
    assert rule.matches_action("kill_port")
    assert not rule.matches_action("stop_service")


def test_rule_matches_port_range():
    rule = McpRule(id="r1", effect="allow", port_ranges=[[3000, 9999]])
    assert rule.matches_port(3000)
    assert rule.matches_port(8080)
    assert rule.matches_port(9999)
    assert not rule.matches_port(80)
    assert not rule.matches_port(10000)


def test_rule_matches_any_port_when_no_ranges():
    rule = McpRule(id="r1", effect="allow", port_ranges=[])
    assert rule.matches_port(80)
    assert rule.matches_port(None)


def test_rule_matches_pid():
    rule = McpRule(id="r1", effect="allow", pids=[1234, 5678])
    assert rule.matches_pid(1234)
    assert not rule.matches_pid(9999)


def test_rule_matches_any_pid_when_empty():
    rule = McpRule(id="r1", effect="allow", pids=[])
    assert rule.matches_pid(9999)
    assert rule.matches_pid(None)


def test_rule_disabled_never_matches():
    rule = McpRule(id="r1", effect="allow", enabled=False, agent_ids=["*"], actions=["*"])
    assert not rule.matches_agent("cursor")
    assert not rule.matches_action("kill_port")


def test_rule_to_dict():
    rule = McpRule(id="my-rule", effect="deny", description="test")
    d = rule.to_dict()
    assert d["id"] == "my-rule"
    assert d["effect"] == "deny"


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


def test_evaluate_allow_by_default():
    rs = RuleSet(default_policy="allow", rules=[])
    decision = evaluate(rs, action="kill_port", port=8080)
    assert decision.allowed is True
    assert decision.matched_rule_id is None


def test_evaluate_deny_by_default():
    rs = RuleSet(default_policy="deny", rules=[])
    decision = evaluate(rs, action="kill_port", port=8080)
    assert decision.allowed is False


def test_evaluate_first_matching_rule_wins():
    allow_rule = McpRule(id="allow-dev", effect="allow", port_ranges=[[3000, 9999]])
    deny_rule = McpRule(id="deny-all", effect="deny")
    rs = RuleSet(default_policy="allow", rules=[allow_rule, deny_rule])

    decision = evaluate(rs, action="kill_port", port=8080)
    assert decision.allowed is True
    assert decision.matched_rule_id == "allow-dev"


def test_evaluate_deny_rule_blocks():
    deny_rule = McpRule(id="deny-privileged", effect="deny", port_ranges=[[1, 1023]])
    rs = RuleSet(default_policy="allow", rules=[deny_rule])

    decision = evaluate(rs, action="kill_port", port=80)
    assert decision.allowed is False
    assert decision.matched_rule_id == "deny-privileged"


def test_evaluate_port_not_in_deny_range_falls_through():
    deny_rule = McpRule(id="deny-privileged", effect="deny", port_ranges=[[1, 1023]])
    rs = RuleSet(default_policy="allow", rules=[deny_rule])

    decision = evaluate(rs, action="kill_port", port=8080)
    assert decision.allowed is True  # falls through to default "allow"


def test_evaluate_agent_id_filtering():
    rule = McpRule(id="cursor-only", effect="allow", agent_ids=["cursor"])
    rs = RuleSet(default_policy="deny", rules=[rule])

    assert evaluate(rs, action="kill_port", port=8080, agent_id="cursor").allowed is True
    assert evaluate(rs, action="kill_port", port=8080, agent_id="claude").allowed is False


def test_evaluate_action_filtering():
    rule = McpRule(id="read-only", effect="allow", actions=["list_ports", "inspect_port"])
    rs = RuleSet(default_policy="deny", rules=[rule])

    assert evaluate(rs, action="list_ports").allowed is True
    assert evaluate(rs, action="kill_port").allowed is False


def test_rule_decision_to_dict():
    d = RuleDecision(allowed=True, reason="ok", matched_rule_id="r1").to_dict()
    assert d["allowed"] is True
    assert d["reason"] == "ok"
    assert "evaluated_at" in d


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------


def test_dev_ruleset_allows_dev_ports():
    rs = make_dev_ruleset()
    # Port 8080 (dev range) should be allowed
    assert evaluate(rs, action="kill_port", port=8080).allowed is True


def test_dev_ruleset_denies_privileged_ports():
    rs = make_dev_ruleset()
    # Port 80 (privileged) should be denied
    assert evaluate(rs, action="kill_port", port=80).allowed is False


def test_ci_ruleset_allows_ephemeral():
    rs = make_ci_ruleset()
    assert evaluate(rs, action="kill_port", port=5000).allowed is True


def test_ci_ruleset_denies_privileged():
    rs = make_ci_ruleset()
    # Port 22 is privileged; default is deny
    assert evaluate(rs, action="kill_port", port=22).allowed is False


def test_enterprise_ruleset_readonly_always_allowed():
    rs = make_enterprise_ruleset(allowed_agent_ids=["trusted-bot"])
    assert evaluate(rs, action="list_ports", agent_id="any-agent").allowed is True
    assert evaluate(rs, action="doctor", agent_id="unknown").allowed is True


def test_enterprise_ruleset_destructive_only_for_trusted():
    rs = make_enterprise_ruleset(allowed_agent_ids=["trusted-bot"])
    assert evaluate(rs, action="kill_port", port=8080, agent_id="trusted-bot").allowed is True
    assert evaluate(rs, action="kill_port", port=8080, agent_id="untrusted").allowed is False


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def test_load_rules_returns_default_when_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "nonexistent_rules.json")
        rs = load_rules(path=path)
        assert isinstance(rs, RuleSet)
        assert rs.default_policy == "allow"
        assert rs.rules == []


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "mcp_rules.json")
        original = make_dev_ruleset()
        save_rules(original, path=path)

        loaded = load_rules(path=path)
        assert loaded.default_policy == original.default_policy
        assert len(loaded.rules) == len(original.rules)
        assert loaded.rules[0].id == original.rules[0].id


def test_save_creates_parent_dirs():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "nested", "dir", "rules.json")
        save_rules(make_ci_ruleset(), path=path)
        assert os.path.isfile(path)


def test_load_rules_invalid_json_returns_default():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "bad.json")
        Path(path).write_text("not valid json {{{", encoding="utf-8")
        rs = load_rules(path=path)
        assert isinstance(rs, RuleSet)


# ---------------------------------------------------------------------------
# summarize_ruleset
# ---------------------------------------------------------------------------


def test_summarize_ruleset():
    rs = make_dev_ruleset()
    summary = summarize_ruleset(rs)
    assert summary["version"] == 1
    assert "default_policy" in summary
    assert "rule_count" in summary
    assert "rules" in summary
    assert len(summary["rules"]) == len(rs.rules)
