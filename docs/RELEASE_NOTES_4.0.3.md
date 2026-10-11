# 🔧 kport v4.0.3 — Release Notes

**Release Date:** 2026-07-31
**Tag:** `v4.0.3`
**Branch:** `fixes` → merged to `main`
**PyPI:** [`kport==4.0.3`](https://pypi.org/project/kport/4.0.3/)
**Chocolatey:** [`kport 4.0.3`](https://community.chocolatey.org/packages/kport/4.0.3)

---

## Overview

kport 4.0.3 is a **patch release** focused on packaging correctness, cross-platform polish, and CI reliability. It directly addresses the Chocolatey community reviewer rejection of `kport 3.2.4`, resolves all outstanding Ruff linting violations, and incorporates the Dependabot CI upgrade for `actions/setup-python@v7`.

---

## 🐛 Bug Fixes

### Chocolatey Shim Cleanup (Critical)

- **File:** `packaging/chocolatey/tools/chocolateyuninstall.ps1`
- **Problem:** The install script created a `kport` command shim via `Install-BinFile`, but the uninstall script never called `Uninstall-BinFile`. This left a dangling shim in `%ChocolateyInstall%\bin\kport.exe` after uninstall and caused the Chocolatey community package to be rejected with:
  > *"This package uses Install-BinFile without also calling Uninstall-BinFile or Remove-BinFile in chocolateyUninstall.ps1"*
- **Fix:** Added `Uninstall-BinFile -Name "kport"` at the top of `chocolateyuninstall.ps1`, ensuring the shim is cleaned up on every uninstall.

### Windows Elevation Hint Messages

- **File:** `src/kport/cli.py`
- **Problem:** When `kport` found a process but couldn't read its port bindings due to insufficient privileges, it displayed a Linux-only hint (`sudo kport -ip 'name'`) on Windows.
- **Fix:** The hint is now platform-aware:
  - **Windows:** `"Try running as Administrator: Right-click your terminal → 'Run as administrator'"`
  - **Linux/macOS:** `"Try: sudo kport -ip 'name'"`

### FallbackInspector `proto` Parameter

- **File:** `src/kport/inspectors/system_impl.py`
- **Problem:** `FallbackInspector.find_ports_by_process_name()` was missing the `proto: str = "tcp"` parameter, causing a `TypeError: unexpected keyword argument 'proto'` when calling the method via the base class interface.
- **Fix:** Added `proto: str = "tcp"` to the method signature, aligning it with `BaseInspector` and `PsutilInspector`.

---

## 🧹 Code Quality

### Ruff Linting (Zero Violations)

All three Ruff F401 (unused import) violations are resolved:

| File | Violation | Fix |
|------|-----------|-----|
| `benchmarks/bench_list.py` | `sys` imported but unused | Removed |
| `benchmarks/bench_list.py` | `USING_PSUTIL` imported but unused | Removed |
| `manage.py` | `import pytest` inside try block (unused) | Replaced with `importlib.util.find_spec("pytest")` |

---

## ⚙️ CI / Infrastructure

### `actions/setup-python` Bumped to v7

- **Files:** `.github/workflows/ci.yml`, `.github/workflows/build-packages.yml`
- Updated all occurrences of `actions/setup-python@v6` → `@v7` across both workflow files (8 total occurrences).
- This incorporates the Dependabot PR #17 change and ensures the `fixes` branch passes CI checks cleanly when merged into the protected `main` branch.
- `actions/setup-python@v7` migrates action internals to ESM for compatibility with newer `@actions/*` packages.

---

## 📦 Chocolatey Packaging — Interactive Workflows

Two enhanced interactive workflows are now available for Chocolatey build and publish.

### Building (`build_packages.py --choco`)

```
─── Building Chocolatey Package ───

--- Chocolatey Installer Checksum Resolution ---
Select an option to resolve the SHA-256 checksum:
  [1] Calculate from local installer file (default)
  [2] Download and calculate from GitHub release URL
  [3] Enter checksum manually (64-char hex)
  [4] Read/calculate checksum from a custom file
Enter choice [1-4] (default 1):
```

### Publishing (`publish_packages.py --chocolatey`)

```
─── Publishing to Chocolatey ───

--- Chocolatey Package Publish Options ---
Choose an action:
  [1] Push the existing package as-is (default)
  [2] Rebuild package using live GitHub release checksum
  [3] Rebuild package using manual checksum entry
  [4] Rebuild package using custom file
  [5] Rebuild package using local installer file
Enter choice [1-5] (default 1):
```

---

## 📋 Upgrade Guide

### From v4.0.0 / v4.0.x

No breaking changes. This is a pure patch release.

**pip:**
```bash
pip install --upgrade kport
```

**Chocolatey:**
```powershell
choco upgrade kport
```

**Winget:**
```powershell
winget upgrade kport
```

---

## 📊 Test Coverage

| Suite | Tests | Status |
|-------|-------|--------|
| `test_cli.py` | 13 | ✅ All pass |
| `test_commands.py` | 21 | ✅ All pass |
| `test_mcp.py` | 10 | ✅ All pass |
| `test_new_features.py` | 38 (2 skipped) | ✅ All pass |
| `test_publish_pypi.py` | 1 | ✅ All pass |
| **Total** | **82 passed, 2 skipped** | ✅ |

> The 2 skipped tests are curses TUI tests that require a live TTY and are intentionally skipped in headless/CI environments.

---

## 🔗 Links

- **GitHub Release:** https://github.com/farman20ali/kport/releases/tag/v4.0.3
- **Chocolatey Package:** https://community.chocolatey.org/packages/kport/4.0.3
- **PyPI Package:** https://pypi.org/project/kport/4.0.3/
- **Changelog:** [CHANGELOG.md](../CHANGELOG.md)
- **Issues:** https://github.com/farman20ali/kport/issues
