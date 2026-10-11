# 🚀 kport v3.2.3 — Release Notes

**Release Date:** June 14, 2026
**Tag:** `v3.2.3`
**Previous Version:** [v3.2.2](./RELEASE_NOTES_3.2.2.md)

---

## Overview

kport 3.2.3 is a feature and infrastructure release. It ships the official **VS Code extension**, expands package manager support to **Chocolatey** and **Snap**, adds smarter **CLI diagnostics** for permission-restricted processes, and resolves two **critical CI regressions** that broke PyPI and Snap publishing in the previous release cycle.

---

## 🆕 New Features

### 🖥️ Official VS Code Extension

The KPort VS Code extension brings port management directly into the editor:

| Command | Description |
|---|---|
| `KPort: Inspect Ports` | View all active ports and associated processes |
| `KPort: Free Port` | Kill a process using a specific port |
| `KPort: List Ports` | Show all listening ports with process details |
| `KPort: Configure MCP Server` | Set up MCP server for AI assistant integration |

**MCP (Model Context Protocol) integration:**
- On first activation, the extension prompts once to configure MCP for AI tools
- Automatically generates `.vscode/mcp.json` with correct stdio settings
- Works with Claude, GitHub Copilot, and any MCP-compatible client
- MCP can be reconfigured at any time via the Command Palette

**Install from VS Code Marketplace:**
```
Extensions (Ctrl+Shift+X) → search "KPort" → Install
```
Or via CLI:
```bash
code --install-extension alienhub.kport-vscode
```

**New files:** `vscode-extension/` directory (TypeScript source, `package.json`, `tsconfig.json`, `mcpConfig.ts`, `extension.ts`)

---

### 📦 Chocolatey Package Support (Windows)

kport is now available via Chocolatey for Windows users:

```powershell
choco install kport
```

**New files:**
- `choco_build.py` — builds `.nupkg` from a staged Windows `.exe` installer
- `packaging/chocolatey/kport.nuspec.template`
- `packaging/chocolatey/tools/chocolateyinstall.ps1.template`

---

### 📦 Snap Package Support (Linux)

kport is now available from the Snap Store:

```bash
snap install kport
```

**New files:**
- `snap_build.py` — builds `.snap` from source using snapcraft
- `snap_publish.py` — publishes to Snap Store with channel management (`stable`, `candidate`, `edge`)
- `packaging/snap/snapcraft.yaml.template`
- `packaging/snap/launcher`

---

### 🔨 Unified Build & Publish Tooling

New developer-facing scripts and documentation for streamlined release workflows:

- **`kport_orchestrate.py`** *(formerly `build.py` — see Bug Fixes below)*
  — Single interactive or CLI entry point for all build + publish operations
  ```bash
  python kport_orchestrate.py                 # interactive menu
  python kport_orchestrate.py --build-all     # CI: build everything
  python kport_orchestrate.py --publish-all   # CI: publish everything
  python kport_orchestrate.py --build-snap --publish-snap --snap-channel edge
  ```

- **`vscode_build.py`** — VS Code extension builder and publisher
  ```bash
  python vscode_build.py --build     # compile .vsix
  python vscode_build.py --install   # install locally
  python vscode_build.py --publish   # publish to VS Code Marketplace
  ```

- **`snap_publish.py`** — Snap Store publisher
  ```bash
  python snap_publish.py --publish --channel stable
  python snap_publish.py --publish --channel edge
  ```

- **`BUILD_GUIDE.md`** — Comprehensive developer documentation covering all build scripts, prerequisites, credentials, and release workflows.

---

### ⚠️ Smarter CLI Diagnostics: Inaccessible Process Warnings

When a `kport` command targets a process by name and finds the process but cannot see its port bindings, it now diagnoses the most common cause and tells you clearly:

```
Warning: No port bindings found matching 'myapp', but matching processes exist
(PID(s): 12345). Try running with sudo/admin privileges.
```

**What changed in `src/kport/cli.py`:**
- When port inspection returns no bindings but matching PIDs are found, kport checks whether the current user is root/admin
- If not elevated, it prints an actionable `stderr` warning with the relevant PID(s)
- Cross-platform: uses `os.geteuid()` on Linux/macOS and `ctypes.windll.shell32.IsUserAnAdmin()` on Windows
- No warning is shown if the user is already elevated (no false noise)

---

## 🐛 Bug Fixes

### `python -m build` executed interactive menu instead of building wheels (PyPI CI failure)

**Root cause:** The repo contained a file named `build.py`. When the CI step ran `python -m build`, Python found the local `build.py` before the installed `build` pip package (because CWD is on `sys.path`). This launched the interactive orchestrator menu, which hit `EOFError` on `input()` in the non-TTY CI environment.

**Fix:**
- Renamed `build.py` → **`kport_orchestrate.py`** to eliminate the name collision entirely
- `python -m build` now correctly invokes the PyPI build frontend
- No workarounds needed in `build_packages.py`

### `Error installing snap 'core22'` (Snap CI failure)

**Root cause:** `ubuntu-latest` runners (Ubuntu 24.04) use a restricted `snapd` sandbox that cannot download and install `core22` from the Snap Store. The previous workflow used `sudo snap install snapcraft --classic`, which itself requires core22 and therefore fails.

**Fix:**
- Replaced `sudo snap install snapcraft --classic` + `python snap_build.py --build` with **`snapcore/action-build@v1`** — the official GitHub Action that runs snapcraft inside an LXD container, bypassing the snapd restriction entirely
- Added a "Prepare snap staging directory" step that renders `snapcraft.yaml` from the template and copies source files into `snap-build/` before the action runs

### Snap cross-compilation failure on amd64 CI runners

**Root cause:** `snapcraft.yaml.template` declared `architectures: [amd64, arm64]`, instructing snapcraft to cross-compile an arm64 snap on the amd64 CI runner. This fails because the LXD container does not have QEMU configured for arm64 emulation in the GitHub Actions environment.

**Fix:** Removed the `architectures` block from `snapcraft.yaml.template`. Snapcraft now builds for the native runner architecture (amd64). An arm64 snap can be added later using a separate arm64 runner job.

---

## 🔄 CI/CD Improvements

**`.github/workflows/build-packages.yml`** — complete rewrite:

| Improvement | Detail |
|---|---|
| PyPI job | Clean `python -m build` — no workarounds required |
| Snap job | Uses `snapcore/action-build@v1` with staged source directory |
| Resilience | `continue-on-error: true` on `build-deb`, `build-rpm`, `build-snap`, `build-choco` — platform package failures no longer block the core PyPI release |
| VS Code job | New `vscode-extension.yml` workflow added |
| Documentation | Inline comments explain every non-obvious step |

---

## 📋 Full Changelog

| Commit | Type | Summary |
|---|---|---|
| `88ba487` | fix | Rename `build.py` → `kport_orchestrate.py`; fix snap CI; rewrite workflow |
| `7eabe76` | feat | Add `kport_orchestrate.py`, `snap_publish.py`, `vscode_build.py`, `BUILD_GUIDE.md`; VS Code MCP first-run prompt |
| `2f6468d` | chore | Bump version to 3.2.3; initial release notes draft |
| `93c7a7c` | feat | Add VS Code extension (`extension.ts`, `mcpConfig.ts`, `package.json`); add `snap_build.py`, `choco_build.py`; update CI workflows |
| `0d980d7` | feat | Add permission-aware warning in CLI when processes are found but port bindings are inaccessible |

---

## 🔄 Backward Compatibility

✅ **Fully backward compatible with kport 3.2.2.**

All existing commands, flags, and configurations continue to work without modification.

**One developer-facing rename:**
- `build.py` → `kport_orchestrate.py` — update any local scripts or CI references that called `python build.py` directly.

---

## 📦 Installation

| Platform | Command |
|---|---|
| **PyPI** | `pip install kport==3.2.3` |
| **Linux DEB** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.3) |
| **Linux RPM** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.3) |
| **Linux Snap** | `snap install kport` |
| **macOS** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.3) |
| **Windows EXE** | See [Releases](https://github.com/farman20ali/kport/releases/tag/v3.2.3) |
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
