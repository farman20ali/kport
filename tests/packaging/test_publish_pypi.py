"""
Packaging and distribution tests (tests/packaging/test_publish_pypi.py).

Verifies license metadata validation in the publish script.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_publish_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "publish_pypi.py"
    spec = importlib.util.spec_from_file_location("publish_pypi", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.packaging
def test_check_license_metadata_accepts_license_file_metadata(tmp_path, monkeypatch):
    publish_pypi = _load_publish_module()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nlicense = { file = "LICENSE" }\n',
        encoding="utf-8",
    )
    (tmp_path / "LICENSE").write_text("Apache License\nVersion 2.0\n", encoding="utf-8")
    monkeypatch.setattr(publish_pypi, "REPO_ROOT", tmp_path)
    publish_pypi.check_license_metadata()
