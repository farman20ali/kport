# KPort Build & Packaging Guide

This guide explains how to use the comprehensive build and packaging scripts for KPort.

## Overview

KPort provides several build scripts for different purposes:

| Script | Purpose | Output |
|--------|---------|--------|
| **build.py** | Unified orchestrator for all builds | Various (see below) |
| **vscode_build.py** | Build & publish VS Code extension | `.vsix` file |
| **snap_publish.py** | Publish snap packages | Snap Store |
| **build_packages.py** | Build platform packages | .deb, .rpm, .exe, .pkg, etc. |
| **snap_build.py** | Build snap packages | `.snap` file |
| **publish.py** | Publish to PyPI | PyPI |

## Quick Start

### Interactive Mode (Recommended)

Start with interactive menus for guided workflows:

```bash
# Main orchestrator - choose what to build/publish
python3 build.py

# VS Code extension builder
python3 vscode_build.py

# Snap publisher
python3 snap_publish.py
```

### Command Line Mode (Automated/CI)

For scripting and CI/CD pipelines:

```bash
# Check all prerequisites
python3 build.py --check

# Build all packages
python3 build.py --build-all

# Build specific packages
python3 build.py --build-vscode
python3 build.py --build-snap
python3 build.py --build-pypi

# Publish packages
python3 build.py --publish-vscode
python3 build.py --publish-snap --snap-channel stable
python3 build.py --publish-pypi

# Full workflow: build and publish
python3 build.py --build-all --publish-all --dry-run  # Preview
python3 build.py --build-all --publish-all            # Execute
```

---

## 1. Main Orchestrator: `build.py`

Unified interface for building and publishing all packages.

### Prerequisites

All platform-specific tools (Node.js, snapcraft, etc.) must be installed.

### Check Prerequisites

```bash
python3 build.py --check
```

Shows status of:
- Python build tools
- Node.js & npm
- VS Code CLI
- vsce (VS Code Extension CLI)
- snapcraft
- snapcraft authentication

### Build Operations

```bash
# Build everything
python3 build.py --build-all

# Build specific packages
python3 build.py --build-pypi      # Python packages
python3 build.py --build-vscode    # VS Code extension
python3 build.py --build-snap      # Snap package

# Build and install VS Code extension locally
python3 build.py --build-vscode --install-vscode
```

### Publish Operations

```bash
# Publish everything
python3 build.py --publish-all

# Publish specific packages
python3 build.py --publish-pypi
python3 build.py --publish-vscode
python3 build.py --publish-snap --snap-channel stable
python3 build.py --publish-snap --snap-channel edge
```

### Dry-Run Mode

Preview what would happen without executing:

```bash
python3 build.py --build-all --dry-run
python3 build.py --publish-all --dry-run
```

### Full Workflows

```bash
# Build all, then publish all (interactive)
python3 build.py --build-all && python3 build.py --publish-all

# Build VS Code extension and install locally
python3 build.py --build-vscode --install-vscode

# Build snap and publish to edge channel
python3 build.py --build-snap && python3 build.py --publish-snap --snap-channel edge
```

---

## 2. VS Code Extension: `vscode_build.py`

Build and publish the KPort VS Code extension.

### Prerequisites

```bash
# Install Node.js and npm first
node --version    # Should be v14 or higher
npm --version
```

Check prerequisites:

```bash
python3 vscode_build.py --check
```

Must have:
- ✅ Node.js & npm
- ✅ VS Code CLI (code command)
- ✅ vsce CLI (`npm install -g @vscode/vsce`)
- ✅ `vscode-extension/` directory
- ✅ `vscode-extension/package.json`

### Build Extension (.vsix)

```bash
# Build interactively
python3 vscode_build.py

# Build from command line
python3 vscode_build.py --build

# Preview what would happen
python3 vscode_build.py --build --dry-run
```

Creates: `vscode-extension/kport-vscode-<version>.vsix`

### Install Locally (Testing)

```bash
# Install for testing
python3 vscode_build.py --install

# Or build + install in one command
python3 vscode_build.py --build --install
```

In VS Code, reload the window to activate the extension.

### Publish to Marketplace

Before publishing, you need:

1. **VS Code Marketplace Publisher Account**
   - Visit: https://marketplace.visualstudio.com/manage
   - Create a publisher (one-time setup)

2. **Personal Access Token (PAT)**
   - Generate at: https://dev.azure.com
   - Scopes needed: Marketplace > Manage

3. **Set Environment Variable**
   ```bash
   export VSCE_PAT=your_personal_access_token
   
   # Or for one-time use
   VSCE_PAT=token python3 vscode_build.py --publish
   ```

Publish:

```bash
# Publish (interactive)
python3 vscode_build.py

# Publish from command line
VSCE_PAT=your_token python3 vscode_build.py --publish

# Preview
python3 vscode_build.py --publish --dry-run
```

### Interactive Menu

```
🔨 KPort VS Code Extension Builder
====================================

Version: 3.2.3

What would you like to do?
  1. Check prerequisites
  2. Build extension (.vsix)
  3. Install locally (for testing)
  4. Publish to marketplace
  5. Build → Install (build & test locally)
  6. Build → Publish (build & publish)
  0. Exit
```

---

## 3. Snap Publisher: `snap_publish.py`

Publish snap packages to the Snap Store.

### Prerequisites

```bash
# Install snapcraft
sudo apt install snapcraft

# Log in to Snap Store
snapcraft login
# (You'll be prompted for Snap Store credentials)
```

Check prerequisites:

```bash
python3 snap_publish.py --check
```

Must have:
- ✅ snapcraft CLI
- ✅ snapcraft authentication
- ✅ Built .snap file from `snap_build.py`

### Authenticate with Snap Store

```bash
# Interactive login
python3 snap_publish.py --login

# Or manually
snapcraft login
```

Get credentials at: https://snapcraft.io/account

### Publish Snap

```bash
# Publish interactively
python3 snap_publish.py

# Publish from command line
python3 snap_publish.py --publish

# Publish to specific channel
python3 snap_publish.py --publish --channel stable
python3 snap_publish.py --publish --channel edge
python3 snap_publish.py --publish --channel candidate

# Preview
python3 snap_publish.py --publish --dry-run
```

### Channels Explained

| Channel | Purpose | Audience |
|---------|---------|----------|
| **stable** | Production release | End users |
| **candidate** | Release candidate | Testing before stable |
| **edge** | Development/beta | Testers & developers |

Typical workflow:
```bash
python3 snap_publish.py --publish --channel edge       # Test
python3 snap_publish.py --publish --channel candidate  # RC testing
python3 snap_publish.py --publish --channel stable     # Production
```

### Interactive Menu

```
📦 KPort Snap Package Publisher
==================================

Found: kport_3.2.3_amd64.snap (v3.2.3)

What would you like to do?
  1. Check prerequisites
  2. Login to Snap Store
  3. Publish to stable channel
  4. Publish to edge channel (testing)
  5. Publish to candidate channel (release candidate)
  0. Exit
```

---

## Complete Workflow Examples

### Example 1: Release v3.2.3

Full release to all platforms:

```bash
# 1. Check everything is ready
python3 build.py --check

# 2. Build everything
python3 build.py --build-all

# 3. Test VS Code extension locally
python3 build.py --install-vscode
# (Test in VS Code, reload window)

# 4. When ready, publish all
python3 build.py --publish-all
```

### Example 2: Release VS Code Extension Only

```bash
# Build and test
python3 vscode_build.py
# (Choose: Build → Install)

# Test in VS Code...

# Then publish
VSCE_PAT=your_token python3 vscode_build.py --publish
```

### Example 3: Snap Release (edge → stable)

```bash
# Build snap
python3 build.py --build-snap

# Test in edge channel
python3 snap_publish.py --publish --channel edge

# After testing, promote to stable
python3 snap_publish.py --publish --channel stable
```

### Example 4: CI/CD Pipeline

```bash
#!/bin/bash
set -e

# Build all packages
python3 build.py --build-all

# Run tests
python3 run_tests.py

# Publish
python3 build.py --publish-all
```

---

## Environment Variables

### For VS Code Publishing

```bash
export VSCE_PAT=your_personal_access_token
```

### For Snap Publishing

```bash
# snapcraft login stores credentials in ~/.local/share/snapcraft
# No explicit environment variable needed
```

### For PyPI Publishing

```bash
export TWINE_USERNAME=your_pypi_username
export TWINE_PASSWORD=your_pypi_token
```

---

## Troubleshooting

### Node.js Not Found

```bash
# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install nodejs
```

### VS Code CLI Not Found

```bash
# Ensure VS Code is installed and `code` is in PATH
# Or install VS Code
sudo apt install code
```

### vsce Not Installed

```bash
npm install -g @vscode/vsce
```

### snapcraft Not Installed

```bash
sudo apt install snapcraft
```

### Authentication Errors

```bash
# Re-authenticate
snapcraft logout
snapcraft login

# For VS Code, check your VSCE_PAT token is correct
```

### Build Failures

```bash
# Check prerequisites
python3 build.py --check

# Dry-run to see what would happen
python3 build.py --build-all --dry-run

# Check individual scripts
python3 vscode_build.py --check
python3 snap_publish.py --check
```

---

## Additional Resources

- [VS Code Extension Publishing](https://code.visualstudio.com/api/working-with-extensions/publishing-extension)
- [Snapcraft Documentation](https://snapcraft.io/docs)
- [PyPI Upload](https://packaging.python.org/tutorials/packaging-projects/)
- [KPort GitHub](https://github.com/farman20ali/kport)

---

## Support

For issues or questions, please open an issue on GitHub:
https://github.com/farman20ali/kport/issues
