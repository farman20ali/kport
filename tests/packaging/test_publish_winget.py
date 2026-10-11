"""Packaging and distribution tests for WinGet (tests/packaging/test_publish_winget.py).

Verifies manifest rendering, template validation, and CLI parameter handling.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(script_name: str):
    module_path = REPO_ROOT / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(script_name.replace(".py", ""), module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.packaging
def test_winget_render_manifests(tmp_path, monkeypatch):
    publish_winget = _load_module("publish_winget.py")
    dist_winget = tmp_path / "dist" / "winget"
    monkeypatch.setattr(publish_winget, "DIST_WINGET", dist_winget)

    version = "5.1.0"
    test_sha = "B0CB6DF0F3698CD5EEB84041B2AA97AD203DCD30299FEFBB400FB21C596DAEE1"

    singleton_file, multi_dir = publish_winget.render_manifests(version, test_sha)

    # 1. Verify singleton manifest
    assert singleton_file.exists()
    singleton_text = singleton_file.read_text(encoding="utf-8")
    assert f"PackageVersion: {version}" in singleton_text
    assert "PackageIdentifier: farman20ali.kport" in singleton_text
    assert "InstallerType: nullsoft" in singleton_text
    assert "Silent: /S" in singleton_text
    assert test_sha in singleton_text
    assert f"https://github.com/farman20ali/kport/releases/download/v{version}/kport-{version}-setup.exe" in singleton_text

    # 2. Verify multi-file manifest
    assert multi_dir.exists()
    version_file = multi_dir / "farman20ali.kport.yaml"
    installer_file = multi_dir / "farman20ali.kport.installer.yaml"
    locale_file = multi_dir / "farman20ali.kport.locale.en-US.yaml"

    assert version_file.exists()
    assert installer_file.exists()
    assert locale_file.exists()

    installer_text = installer_file.read_text(encoding="utf-8")
    assert "InstallerType: nullsoft" in installer_text
    assert test_sha in installer_text
    assert "Silent: /S" in installer_text


@pytest.mark.packaging
def test_winget_token_resolution(monkeypatch):
    publish_winget = _load_module("publish_winget.py")

    monkeypatch.delenv("WINGET_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    # When gh CLI is not available, should return None
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    assert publish_winget.get_token() is None

    # When env var is set, returns env var
    monkeypatch.setenv("WINGET_GITHUB_TOKEN", "ghp_test_token")
    assert publish_winget.get_token() == "ghp_test_token"

    # Explicit overrides env
    assert publish_winget.get_token("explicit_token") == "explicit_token"


@pytest.mark.packaging
def test_winget_release_url():
    publish_winget = _load_module("publish_winget.py")
    url = publish_winget.release_installer_url("5.1.0")
    assert url == "https://github.com/farman20ali/kport/releases/download/v5.1.0/kport-5.1.0-setup.exe"
