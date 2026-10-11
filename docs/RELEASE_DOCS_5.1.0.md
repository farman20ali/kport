# 🚀 `kport` v5.1.0 Release Documentation

**Release Date:** September 10, 2026  
**Target Release Tag:** `v5.1.0`  
**Package Version:** `5.1.0`  
**Repository:** [`farman20ali/kport`](https://github.com/farman20ali/kport)

---

## 📢 Release Overview

`kport` v5.1.0 is a major quality, experience, and performance release that eliminates real-world developer friction, expands AI agent capabilities, and optimizes subprocess resource consumption.

### Highlights

1. **Zero-Friction "Pip vs Sudo" Experience**:
   - New `kport setup-sudo` command installs a clean `/usr/local/bin/kport` launcher pointing to the exact Python interpreter/virtualenv.
   - Solves `sudo: kport: command not found` for pip and pipx users permanently.
   - Preserves interactive TTY handles so `sudo` password prompts never hang.

2. **Rootless Linux Inspection Engine**:
   - Fixed unprivileged PID resolution when `/proc` scanning encounters permission boundaries.
   - Automatically falls through to `ss -tlnp` and `lsof` before reporting PID visibility.

3. **Port Holding & Reservation (`kport hold <port>`)**:
   - Temporarily binds and listens on a target port to block other processes or background daemons from snatching it.
   - Releases on Enter, `Ctrl+C`, or when `--timeout N` expires.

4. **Audit Log History CLI (`kport audit`)**:
   - Inspect destructive action history logged in `~/.kport/audit.log` directly via terminal table or versioned JSON.

5. **AI Agent Tooling Expansion**:
   - Added `find_alternative_port` and `get_audit_history` MCP tools.
   - `suggest_next_free_port()` algorithm provides automatic alternative port recommendations when `EADDRINUSE` occurs.

6. **Performance & Subprocess Optimization**:
   - $O(1)$ batch Docker mapping queries eliminate repeated `docker ps` child process spawning.
   - Combined Windows PowerShell TCP + UDP queries reduce startup latency by 50%.
   - 1.5-second TTL process metadata cache prevents CPU/disk churn during live watch polling.

---

## 📋 Comprehensive Changelog Summary

### Added
- `kport setup-sudo`: System-wide `/usr/local/bin/kport` launcher installer.
- `kport hold <port>`: Port reservation subcommand with `--timeout` option.
- `kport audit`: CLI subcommand to view destructive action history logs.
- `find_alternative_port` & `get_audit_history`: Registered MCP tools in `mcp_server.py`.
- `suggest_next_free_port()`: Alternative free port discovery algorithm in `diagnostics.py`.

### Fixed
- Fixed `capture_output=True` lockup in `base.py::_escalate_kill_unix()` for interactive sudo password prompts.
- Fixed rootless `/proc` scanner early exit in `system_impl.py`.
- Fixed ANSI color byte skew in column width calculations across all table formatters in `formatter.py`.
- Fixed watch mode `--proto` option pass-through in `cli_commands.py`.
- Fixed reachable `fuser -k <port>/<proto>` fallback in `base.py`.

### Performance
- $O(1)$ single-pass batch Docker query `docker_mappings_for_host_ports()` in `docker_engine.py`.
- Consolidated Windows PowerShell query for `--proto both` in `system_impl.py`.
- Live watch mode 1.5s TTL process metadata cache in `system_impl.py`.

### Changed
- Wrapped all 18 legacy flag `--json` outputs with versioned envelope `_json_out(command, data)` (`schema_version: 1`).

---

## 🛠️ Package & Verification Checklist

- [x] PyPI wheel version bumped to `5.1.0` in `src/kport/__init__.py`.
- [x] `README.md` badge updated to `v5.1.0`.
- [x] `CHANGELOG.md` updated following Keep a Changelog guidelines.
- [x] Chocolatey template updated in `packaging/chocolatey/`.
- [x] Snapcraft manifest updated in `packaging/snapcraft/`.
- [x] All 326 unit, CLI, TUI, and MCP tests passing cleanly (`pytest`).

---

## 🚀 Release Instructions

```bash
# 1. Verify git status and test suite
pytest
git status

# 2. Commit and tag release
git add .
git commit -m "release: bump version to 5.1.0"
git tag -a v5.1.0 -m "Release v5.1.0 — Pip vs Sudo Fixes, Port Holding, Performance & MCP Tooling"

# 3. Push release branch and tags
git push origin main --tags
```
