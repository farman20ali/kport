# 🚀 kport v3.2.4 — Release Notes

**Release Date:** June 15, 2026
**Tag:** `v3.2.4`
**Previous Version:** [v3.2.3](./RELEASE_NOTES_3.2.3.md)

---

## Overview

kport 3.2.4 is a packaging and configuration release. It adds cross-platform application icons, improves packaging consistency, enhances VS Code extension configuration, and updates the project license metadata to comply with PEP 621 standards.

---

## 🆕 New Features

### 🎨 Cross-Platform Application Icons

kport now includes professional application icons for all platforms:

**New assets:**
- `assets/icons/windows/` — Windows `.ico` format for installers and system integration
- `assets/icons/macos/` — macOS `.icns` format for app bundles
- `assets/icons/linux/` — PNG icons in multiple sizes (16x16, 32x32, 64x64, 128x128, 256x256)

Icons are integrated into:
- Windows installer (NSIS)
- macOS app bundle and DMG
- Linux package managers (DEB, RPM, Snap)
- VS Code extension marketplace

---

### 📦 Improved VS Code Extension Configuration

Enhanced `vscode-extension/` with:
- Refined `package.json` configuration for better marketplace visibility
- Updated icon references and branding consistency
- Improved MCP server integration documentation

---

## 🐛 Bug Fixes

### License metadata compliance (PEP 621)

**Root cause:** `pyproject.toml` used non-standard license field format that may not be recognized by modern Python packaging tools.

**Fix:**
- Updated `project.license` to comply with PEP 621 standard
- License metadata now properly recognized by pip, poetry, and other modern tools
- Verified compatibility with setuptools 70.0+

---

## 🔄 CI/CD Improvements

Enhanced packaging workflows:
- Icon assets integrated into all package build scripts
- Improved icon path handling across platforms
- Better validation of icon formats during CI

---

## 📋 Full Changelog

| Commit | Type | Summary |
|---|---|---|
| `14a027e` | feat | Add cross-platform packaging, application icons, and VS Code extension configuration |
| `392a1dd` | fix | Update project.license to comply with PEP 621 |

---

## 🔄 Backward Compatibility

✅ **Fully backward compatible with kport 3.2.3.**

All existing commands, flags, and configurations continue to work without modification.

---

## 📦 Installation

| Platform | Command |
|---|---|
| **PyPI** | `pip install kport==3.2.4` |
| **Linux DEB** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.4) |
| **Linux RPM** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.4) |
| **Linux Snap** | `snap install kport` |
| **macOS** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.4) |
| **Windows EXE** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.4) |
| **Windows Chocolatey** | `choco install kport` |
| **VS Code** | Extensions marketplace → search "KPort" |

---

## 🛠️ For Developers & Maintainers

### Building from Source

```bash
# Prerequisites check
python kport_orchestrate.py --check

# Build everything
python kport_orchestrate.py --build-all

# Build individual formats
python kport_orchestrate.py --build-pypi    # PyPI wheel + sdist
python kport_orchestrate.py --build-vscode  # VS Code .vsix
python kport_orchestrate.py --build-snap    # Snap package
```

### Publishing

```bash
# Publish everything (requires credentials in env)
python kport_orchestrate.py --publish-all

# Publish individually
python publish.py                              # PyPI (TWINE_PASSWORD required)
python vscode_build.py --publish               # VS Code Marketplace (VSCE_PAT required)
python snap_publish.py --publish --channel stable  # Snap Store (snapcraft login required)
```

### Running Tests

```bash
python run_tests.py
# or
pytest tests/ -v
```

See [`BUILD_GUIDE.md`](../BUILD_GUIDE.md) for full documentation.

---

## 📖 Resources

- [GitHub Repository](https://github.com/farman20ali/kport)
- [PyPI Package](https://pypi.org/project/kport/)
- [Snap Package](https://snapcraft.io/kport)
- [VS Code Extension](https://marketplace.visualstudio.com/items?itemName=alienhub.kport-vscode)
- [Issue Tracker](https://github.com/farman20ali/kport/issues)
- [Build Guide](../BUILD_GUIDE.md)
- [Model Context Protocol](https://modelcontextprotocol.io/)
