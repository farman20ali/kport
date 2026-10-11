"""
Unit tests for named profile loading (tests/unit/test_profile.py).
"""

from __future__ import annotations

import pytest

from kport.profile import load_profiles, resolve_profile


@pytest.mark.unit
class TestProfileLoading:
    def test_valid_list_profile_parsed_correctly(self):
        config = {"profiles": {"dev": [8080, "9000", "invalid"], "prod": 80}}
        profiles = load_profiles(config)
        assert "dev" in profiles
        assert profiles["dev"] == [8080, 9000]
        assert "prod" not in profiles  # invalid type (not a list)

    def test_resolve_profile_case_insensitive(self):
        profiles = {"dev-stack": [8080, 9000]}
        assert resolve_profile("dev-stack", profiles) == [8080, 9000]
        assert resolve_profile("DEV-stack", profiles) == [8080, 9000]

    def test_resolve_profile_missing_returns_none(self):
        profiles = {"dev": [8080]}
        assert resolve_profile("missing", profiles) is None
