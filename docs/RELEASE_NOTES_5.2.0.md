# kport v5.2.0 — Release Notes

**Release Date:** October 11, 2026  
**Tag:** `v5.2.0`  
**Repository:** [farman20ali/kport](https://github.com/farman20ali/kport)

---

## 🚀 What's New in v5.2.0

v5.2.0 is an infrastructure and quality release focused on **cross-platform packaging maturity**, **CI stability**, and **test suite completeness**. It delivers the first-ever WinGet (Windows Package Manager) presence for kport, unifies all distribution scripts under a single `--from-release` model, and hardens the CI pipeline across all three major OSes.

---

## 🪟 WinGet (Windows Package Manager)

**kport is now available on WinGet!**

```powershell
winget install farman20ali.kport
```

The v5.1.0 package was submitted as the first-time onboarding PR to `microsoft/winget-pkgs`. v5.2.0 and all future releases are published automatically via `wingetcreate update` — no manual manifest creation required.

### Update (v5.2.0+) — How it works

For **every future release**, publishing to WinGet is a one-liner:

```powershell
python scripts/publish_winget.py --publish --version 5.2.0
```

This automatically:
1. Downloads the release installer from GitHub and computes its SHA-256.
2. Runs `wingetcreate update farman20ali.kport --version X.Y.Z --urls <installer_url> --submit`.
3. Opens a PR on `microsoft/winget-pkgs` for human moderator review.

> **No manifest YAML files need to be committed to this repository** for update releases. Only the initial `5.1.0` manifests in `packaging/winget/manifests/f/farman20ali/kport/5.1.0/` remain as the permanent first-submission reference.

### WinGet publishing cheat-sheet

| Scenario | Command |
|---|---|
| Current version auto-publish | `python scripts/publish_winget.py --publish` |
| Specific version | `python scripts/publish_winget.py --publish --version 5.2.0` |
| Preview without submitting | `python scripts/publish_winget.py --publish --dry-run` |
| Generate manifests locally only | `python scripts/publish_winget.py --manifest-only` |
| Validate manifests locally | `python scripts/publish_winget.py --validate` |
| Via manage.py | `python manage.py publish --winget` |

---

## 📦 Unified `--from-release` Publishing

All distribution channels now support publishing directly from GitHub Release artifacts — no local builds needed:

```powershell
python manage.py publish --pypi  --from-release   # Download .whl from Release, push to PyPI
python manage.py publish --snap  --from-release   # Download .snap from Release, push to Snap Store
python manage.py publish --choco --from-release   # Compute checksum from .exe, push to Chocolatey
python manage.py publish --winget --from-release  # Compute checksum from .exe, submit WinGet PR
```

---

## 🧪 Test Suite — 335 Tests Passing

| Layer | New Test Files | Tests Added |
|---|---|---|
| CLI | `test_cli_commands`, `test_cli_diagnose`, `test_cli_stop_service`, `test_cli_watch`, `test_pid_commands`, `test_cli_main_exit` | +95 |
| MCP | `test_mcp_tools`, `test_mcp_server`, `test_mcp_stop_service`, `test_mcp_safety`, `test_mcp_pid_tools` | +105 |
| TUI | `test_interactive`, `test_tui_diagnose` | +18 |
| Unit | `test_enrichment`, `test_connections`, `test_inspectors`, `test_process_tree`, `test_audit`, `test_safety`, `test_profile`, `test_project`, `test_service_actions`, `test_diagnostics`, `test_doctor`, `test_kill_domain`, `test_notify`, `test_process_manager`, `test_audit_completeness` | +92 |
| Packaging | `test_publish_winget`, `test_publish_pypi` | +4 |

Total: **335 passed, 1 skipped** (Windows-only skip) in ~16 s on Python 3.12/3.13.

---

## 🔧 Bug Fixes

| Fix | Detail |
|---|---|
| `ModuleNotFoundError: No module named '_curses'` on Windows CI | `TestCursesWrapperExceptionHandling` now uses `pytest.importorskip("_curses")` — consistent with all other TUI tests. |
| `AttributeError: module 'psutil' has no attribute 'Error'` on Linux CI | Added `psutil.Error` base exception to the test psutil stub and a safety guard in `psutil_impl.py`. |
| `assert <MagicMock> == os.getppid()` in enrichment tests | Stub `psutil.Process` now returns realistic values (`os.getppid()`, `os.getcwd()`, positive timestamp). |
| `FileNotFoundError: Missing template: .../kport.yaml.template` on CI | `render_manifests()` falls back to embedded default templates when files are missing from checkout. |

---

## 📋 Full Changelog

See [CHANGELOG.md](../CHANGELOG.md#5.2.0---2026-10-11).

---

## ✅ Upgrade Instructions

### pip
```bash
pip install --upgrade kport
```

### winget
```powershell
winget upgrade farman20ali.kport
```

### pipx
```bash
pipx upgrade kport
```

### snap
```bash
sudo snap refresh kport
```
