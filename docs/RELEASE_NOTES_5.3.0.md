# 🚀 kport v5.3.0 — Release Notes

**Release Date:** October 25, 2026  
**Tag:** `v5.3.0`  
**Repository:** [farman20ali/kport](https://github.com/farman20ali/kport)  
**Previous Version:** [v5.2.0](./RELEASE_NOTES_5.2.0.md)

---

## 🚀 What's New in v5.3.0

kport 5.3.0 is a landmark release that delivers **all four phases of the strategic roadmap** (v5.3.0 – v6.0.0) in a single unified version. It transforms kport into a comprehensive, high-performance runtime port intelligence and automated remediation engine.

Key milestones delivered in this release:
1. **Full Roadmap Completion (Phases 1–4)**: Local binding intelligence, Docker/Podman Compose and Kubernetes visibility, service-aware remediation, atomic port swap, and a real-time event-driven core.
2. **MCP Server Expansion (21 Tools)**: Expands the Model Context Protocol surface from 15 to 21 tools, with MCP v2 rule-based safety shields and per-agent identity attribution (`agent_id`).
3. **High-Performance Acceleration**: Direct `/proc/net/tcp` parsing on Linux and native `GetExtendedTcpTable` Win32 API via ctypes on Windows — zero subprocess overhead.
4. **Enhanced Process Inspection & Windows Accuracy**: Rich process metadata cards (`--pid`), strict binary name matching (`-ip`), launcher detection, and clean `(terminated)` parent status.
5. **Test Suite Hardening**: Test suite expanded to **440 tests passing** (0 failures, 1 platform skip, 0 ruff lint errors) with certified Python 3.8 – 3.13 support.

---

## 🗺️ Roadmap Deliverables (Phases 1 – 4)

### Phase 1 — Local Binding Intelligence

- **Dual-Stack & Binding Scope Analysis**: `kport diagnose` detects socket listening scope:
  - `127.0.0.1` / `::1`: loopback only (safe for local development).
  - `0.0.0.0`: all IPv4 interfaces (flags security risk if exposed to public networks).
  - `::`: dual-stack IPv6 wildcard listener.
- **Loopback Leak Detection**: Surfaces when an intended local-only service is bound externally, offering actionable `netcheck` suggestions.
- **Stale Socket Detection**: Identifies sockets stuck in `TIME_WAIT`, `CLOSE_WAIT`, or `FIN_WAIT_2` states, providing automated `SO_REUSEADDR` guidance.

### Phase 2 — Container & Cloud Runtime Visibility

- **Unified Container Engine (`container_engine.py`)**: Seamless support for both Docker and rootless Podman daemon sockets. `docker_engine.py` is preserved as a backward-compatible alias.
- **Compose Project & Service Label Filtering**:
  ```bash
  kport list --compose-project my-app --compose-service web
  kport docker --compose-project my-app
  ```
  Parses `com.docker.compose.*` and `io.podman.compose.*` labels to isolate microservice port conflicts quickly.
- **Kubernetes Port-Forward & Proxy Detection (`k8s_detector.py`)**:
  - Automatically identifies `kubectl port-forward`, `oc`, `minikube tunnel`, `kind`, and `telepresence` proxies.
  - Enriches process display strings with target resource and namespace details (e.g. `kubectl (port-forward → svc/billing-api:80, ns: prod)`).

### Phase 3 — Advanced Remediation & Process Manager Coordination

- **Service-Aware Kill (`kport kill --service-aware`)**:
  Detects when a port is held by a managed process under `systemd`, `pm2`, `supervisord`, or Windows Services. Instead of killing the PID (which immediately triggers an auto-restart loop), it issues the proper service stop command (`systemctl stop`, `pm2 stop`, `Stop-Service`).
- **Atomic Port Swap (`kport swap <port> [--command <cmd>] [--timeout <sec>]`)**:
  Atomically frees an occupied port, binds an exclusive holding socket (`SO_EXCLUSIVEADDRUSE` / `SO_REUSEADDR`), and safely transfers ownership to the replacement process with zero race window.
- **SIEM & CI/CD Audit Export (`kport audit --format ... --export <file>`)**:
  Supports `table`, `json`, `ndjson`, and `siem` (Common Event Format / CEF) export for compliance auditing and telemetry pipelines.

### Phase 4 — High-Performance Core & Real-Time Port Ops

- **Event-Driven Port Watcher (`kport/watcher.py`)**:
  - `PortWatcher` background thread with thread-safe `poll()`, `drain_events()`, and callback support.
  - `kport watch` (with no port argument) enters global all-ports event-monitoring mode.
  - `kport watcher-info` queries active platform watcher strategy and status.
- **Zero-Overhead Kernel Inspection**:
  - **Linux**: Direct `/proc/net/tcp`, `/proc/net/tcp6`, and `/proc/net/udp` scanner (`fast_list_listening_linux()`) — sub-millisecond scans without subshells.
  - **Windows**: Win32 `GetExtendedTcpTable` ctypes integration (`fast_list_listening_windows()`) — bypasses `netstat` subprocess overhead entirely.
- **MCP v2 Safety Rulebuilder (`kport/mcp_rules.py`)**:
  - First-match rule engine supporting per-agent identities, action allow/deny lists, port ranges, and PID scopes.
  - Three built-in presets: `dev` (permissive), `ci` (ephemeral ports only), `enterprise` (deny-by-default).
  - Persistent storage in `~/.kport/mcp_rules.json` (overridable via `KPORT_MCP_RULES`).

---

## 🤖 MCP Server — 21 Tools (AI Agent Ecosystem)

The Model Context Protocol (MCP) server expands from 15 to **21 tools**, providing AI coding agents with safe, comprehensive control over network bindings and running processes.

| Category | Count | Tools |
|---|---|---|
| **Read-Only** | 12 | `list_ports`, `inspect_port`, `find_by_pid`, `explain_port`, `diagnose_port`, `doctor`, `list_connections`, `find_alternative_port`, `get_audit_history`, `inspect_process` *(new)*, `get_safety_rules` *(new)*, `watcher_info` *(new)* |
| **Destructive** | 7 | `kill_port`, `kill_pid`, `kill_process`, `stop_service`, `swap` *(new)* |
| **Write** | 2 | `hold_port`, `set_safety_rules` *(new)* |

### New MCP Capabilities
- **`inspect_process`**: Inspects all processes matching a binary name, returning active port listeners, command lines, users, parent PIDs, and launcher relationships.
- **`swap`**: Safely transfers a port from one service to another under agent control with automatic holding.
- **`get_safety_rules` / `set_safety_rules`**: Query and reconfigure MCP safety policies on the fly without server restarts.
- **`watcher_info` / `watch_ports_snapshot`**: Query background watcher events and platform capabilities.
- **Agent Attribution**: All destructive tools now accept an `agent_id` property, logging caller identity into the audit trail.
- **Dynamic Rule Enforcement**: Integrated `_check_mcp_safety()` hook evaluates active `RuleSet` constraints before any mutating action.

---

## 🔍 Process Inspection & Windows Accuracy Improvements

- **Rich Metadata Card (`kport --pid <pid>`)**:
  Displays formatted process information beneath the port bindings table:
  ```text
  🔍 Inspecting PID 15768

  PID:         15768
  Process:     explorer.exe
  Command:     C:\WINDOWS\Explorer.EXE
  User:        DESKTOP-VU7GUFM\HP
  Parent PID:  5104 (terminated)
  CWD:         C:\WINDOWS\System32

  Active Port Bindings:
    None
  ```
- **High-Accuracy Matching Engine (`matches_process_name`)**:
  `kport -ip <name>` and `inspect_process` now exclusively match the binary executable name and runtime target script. Workspace folder arguments (such as passing a project folder to an IDE) no longer cause unrelated processes (e.g. `language_server_windows_x64.exe`) to appear as false positives.
- **Launcher & Parent Detection**:
  Automatically identifies when an un-bound process is the launcher/parent of a listening service (e.g. `kport.exe — Launcher / Parent of PID 17764`).
- **Unified `(terminated)` Parent Status**:
  Processes whose parent process has already exited now uniformly display `Parent PID: <ppid> (terminated)` across both `--pid` and `-ip` output.
- **Windows Console Stub Recognition**:
  `enrich_process_name()` accurately identifies Windows pip `.exe` wrappers (e.g. `Scripts\kport.exe`) and labels them as `python (kport)` rather than a generic python executable.

---

## 🔧 Bug Fixes

| Area | Issue | Resolution |
|---|---|---|
| **Process Inspection** | `kport -ip kport` matched IDE / language servers passing repo path in arguments | Replaced substring search with `matches_process_name()` inspecting binary name and runtime script target. |
| **Windows Platform** | `psutil.AccessDenied` when querying system processes during scan | Guarded `cmdline()` calls to only run for processes in `RUNTIME_ENRICHMENT_NAMES`. |
| **Output Consistency** | `kport -ip` showed bare `Parent PID: 5104` when parent was dead | Unified `(terminated)` parent PID detection across `--pid` and `-ip` inspection paths. |
| **Packaging & CI** | Missing template fallback during manifest generation | Embedded default fallback templates in `publish_winget.py`. |

---

## 🏗️ Architecture & Refactoring

- **Modular CLI (`src/kport/cli_commands.py`)**:
  Separated command dispatching, formatting, and presentation logic out of `cli.py` into dedicated `cli_commands.py`, improving testability and code organization.
- **Python 3.13 Certification**:
  Added the Python 3.13 classifier to `pyproject.toml`. The test suite is fully verified and clean across Python 3.8 through 3.13.

---

## 🧪 Test Suite — 440 Tests Passing

| Layer | Test Files | Tests | Status |
|---|---|---|---|
| **CLI** | `test_cli_commands`, `test_cli_diagnose`, `test_cli_main_exit`, `test_cli_stop_service`, `test_cli_watch`, `test_pid_commands` | 102 | ✅ Passed |
| **MCP** | `test_mcp_server`, `test_mcp_tools`, `test_mcp_stop_service`, `test_mcp_safety`, `test_mcp_pid_tools` | 134 | ✅ Passed |
| **Unit** | `test_audit`, `test_audit_completeness`, `test_connections`, `test_container_engine`, `test_diagnostics`, `test_doctor`, `test_enrichment`, `test_inspectors`, `test_k8s_detector`, `test_kill_domain`, `test_mcp_features`, `test_mcp_rules`, `test_notify`, `test_process_manager`, `test_process_tree`, `test_profile`, `test_project`, `test_remediation`, `test_safety`, `test_service_actions`, `test_watcher` | 182 | ✅ 181 Passed, 1 Skipped |
| **TUI** | `test_interactive`, `test_tui_diagnose` | 18 | ✅ Passed |
| **Packaging** | `test_publish_winget`, `test_publish_pypi` | 4 | ✅ Passed |
| **Total** | **35 test modules** | **440 passed, 1 skipped** | **0 errors, 0 lints** |

---

## 📋 Full Changelog

For the complete, line-by-line changelog, see [CHANGELOG.md](../CHANGELOG.md#530---2026-10-25).

---

## ✅ Upgrade Instructions

### pip
```bash
pip install --upgrade kport
```

### WinGet (Windows Package Manager)
```powershell
winget upgrade AlienHub.kport
```

### pipx
```bash
pipx upgrade kport
```

### Snap (Linux)
```bash
sudo snap refresh kport
```

### Standalone Pre-built Packages
Native binaries are available on the [GitHub Releases page](https://github.com/farman20ali/kport/releases/tag/v5.3.0):

| Platform | Package |
|---|---|
| Windows | `kport-5.3.0-setup.exe` |
| macOS | `kport-5.3.0.pkg` |
| Debian / Ubuntu | `kport_5.3.0_amd64.deb` |
| Fedora / RHEL | `kport-5.3.0-1.x86_64.rpm` |

---

## 🔗 Quick Links

- [GitHub Repository](https://github.com/farman20ali/kport)
- [PyPI Package](https://pypi.org/project/kport/)
- [Changelog](../CHANGELOG.md)
- [MCP Documentation](../README.md#-mcp-server-ai-agent-integration)
