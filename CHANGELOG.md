# Changelog

All notable changes to `kport` will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [5.1.0] - 2026-09-10

### Added
- **`kport setup-sudo` Subcommand**: Automatically installs a system-wide `/usr/local/bin/kport` wrapper script pointing to the exact Python interpreter/virtualenv, permanently solving `sudo: kport: command not found` for pip and pipx users.
- **Port Holding & Reservation (`kport hold <port>`)**: Temporarily binds and listens on a port to reserve it, blocking other applications/daemons from snatching it until released or timed out (`--timeout N`).
- **Audit Log History CLI (`kport audit`)**: Inspect destructive operation logs from `~/.kport/audit.log` via CLI or `--json`.
- **Alternative Port Recommendations**: `suggest_next_free_port()` algorithm automatically finds and recommends available unassigned ports when target ports are occupied. Exposed in `diagnose` output and MCP responses.
- **New MCP Tools**: Added `find_alternative_port` and `get_audit_history` to the Model Context Protocol (MCP) server for AI coding agents.

### Fixed
- **Rootless Linux PID Resolution**: Fixed `/proc` native scanner to fall through to `ss -tlnp` / `lsof` when socket PIDs are unreadable by unprivileged users, resolving the "rootless cannot find PID" issue.
- **Sudo TTY Password Prompt Lockup**: Preserved interactive TTY handles (`sys.stdin`/`sys.stdout`) during `sudo kill` executions, allowing interactive sudo password prompts without hanging or timing out.
- **ANSI Table Padding Column Skew**: Applied column width truncation and `.ljust()` padding on raw unstyled strings prior to ANSI colorization across all table formatters.
- **Watch Mode Protocol Pass-Through**: Passed `--proto` selection down to `find_bindings_on_port` and `find_pids_on_port` inside watch mode polling loops.
- **Reachable `fuser` Fallback**: Restored `fuser -k` fallback invocation when forced and fixed protocol parameter binding (`fuser -k <port>/<proto>`).

### Performance
- **O(1) Single-Pass Batch Docker Query**: `docker_mappings_for_host_ports()` queries `docker ps` once for multi-port inspection and kill commands, eliminating $O(N)$ child process launches.
- **Windows PowerShell Query Consolidation**: Combined `Get-NetTCPConnection` and `Get-NetUDPEndpoint` into a single hashtable query when `--proto both` is passed on Windows.
- **Live Watch Mode TTL Caching**: 1.5-second TTL process metadata cache prevents CPU and disk churn during high-frequency watch mode polling ticks.

### Changed
- **JSON Envelope Harmonization**: Wrapped all 18 legacy flag `--json` outputs with versioned envelope `_json_out(command, data)` (`schema_version: 1`).

---

## [5.0.0] - 2026-08-22

### Added
- **Diagnostic Intelligence (`kport diagnose` / `diagnose_port` MCP)**: Structured port diagnostics outputting observations, inferences (process managers, project context, container isolation), risk assessments, and safe remediation recommendations.
- **Phase 10 — TUI Diagnose Integration (`kport interactive`)**: Press `d` on any selected port row in the interactive TUI to open a scrollable diagnostic overlay. The overlay presents port status, bound PIDs and process names, project/git context, process-manager inferences, risks, and recommended remediation. The overlay is strictly **read-only** — no process is terminated by opening it. Close with `Esc` or `q`; the TUI returns to the exact previous state and selection. The `d <num>` command is also supported in the non-curses text fallback picker.
- **Environment Doctor (`kport doctor` / `doctor` MCP)**: Aggregated health check reporting platform capabilities, backend status (psutil/fallback), listening sockets, connection state summary, process metadata, and Docker mappings.
- **Active Connection Inspection (`kport connections` / `list_connections` MCP)**: Cross-platform TCP/UDP connection enumeration and filtering by PID, process name, port, and connection state with bounded result caps.
- **Docker Conflict Detection (`conflicts` MCP)**: Identify host ports mapped to Docker containers that are concurrently occupied by native host processes.
- **Process Lineage & Context Enrichment**: Enriched `ProcessInfo` with `ppid`, `cwd`, `start_time`, and parent process name (`parent_name`) for deep lineage context.
- **Windows Service Detection**: Inspect Windows services running behind PIDs using `tasklist /SVC` to provide service names and prevent auto-restart loops.
- **Git Project & Worktree Context**: Resolve process working directories to Git repository root, current branch, worktree state, and credential-sanitized remote URLs.

### Changed
- **Centralized Safety Policy (`kport.safety`)**: Consolidated safety checks, configuration loading, and protected resource lists into a single source of truth used identically across CLI, TUI, and MCP.
- **Decoupled Kill Confirmation**: Refactored core process termination logic in `BaseInspector` to accept an injectable `confirm_fn` callback. Removed presentation-layer dependencies on `confirm_prompt` and console interactivity from the domain/infrastructure layer, allowing headless execution environments to safely refuse/abort when no bypass is given.
- **Relocated Port Polling Utility**: Moved the neutral socket polling helper `_poll_until_free` to a dedicated `port_utils.py` module. Maintained backward compatibility via a deprecated delegation alias in `cli_utils`.
- **Additive Configuration Semantics**: `protected_ports` and `protected_processes` defined in configuration files now extend the hard-coded safety defaults rather than overwriting them.
- **MCP Server Optimization**: Reuses a single inspector instance per server session with clean cache invalidation instead of instantiating new inspectors per RPC call.
- **Audit Log Completeness**: TUI (`kport interactive`) kill operations and MCP `kill_port` calls now emit `audit.log_kill_port` records, matching the existing CLI audit behavior. No CLI/MCP protocol behavior changes.

### Fixed
- **Corrected non-exact process-name lookup**: Modified `find_pids_by_name()` in both `PsutilInspector` and `FallbackInspector` to search process executables/names only, ensuring command-line arguments do not cause false-positive matches (such as kport matching itself or parent shell processes matching search terms).
- **Added self-process exclusion**: Ensured kport's own PID (`os.getpid()`) is consistently excluded from all process name search results across all backend environments.
- **Hardened TUI Ctrl+C handling**: Captured `KeyboardInterrupt` at the TUI's `curses.wrapper()` call site, ensuring that canceling the TUI with Ctrl+C cleanly restores the terminal and prints "Cancelled." instead of bubbling up a traceback.

### Security
- **Strict MCP Safety Shield**: The MCP server strictly enforces safety shields on destructive actions (`kill_port`) and cannot be requested to bypass safety.
- **Credential Sanitization**: Git remote origin URLs resolved during process inspection automatically strip user/token credentials.

### Testing
- Comprehensive test coverage across all new modules: `test_diagnose.py`, `test_doctor.py`, `test_connections.py`, `test_project.py`, `test_process_manager_win.py`, `test_audit.py`, `test_mcp.py`, and `test_phase4.py` bringing the test suite to 193 passing tests.
- Phase 9: Added `test_audit_completeness.py` with 10 targeted tests verifying audit record emission from the TUI and MCP kill paths (success, failure, dry-run, safety-blocked, docker-row, and pid-None cases).
- Phase 10: Added `tests/tui/test_tui_diagnose.py` with 6 focused tests covering curses `d` key handling, overlay close via `Esc`/`q`, scrolling, empty-row guard, error modal on diagnosis failure, and the non-curses fallback `d <num>` flow. Added `TestDiagnosticsArchitecture.test_diagnostics_imports_boundary_regression` (AST-level check) to `tests/unit/test_diagnostics.py` to enforce that `diagnostics.py` never imports `interactive`, `formatter`, `cli`, `cli_commands`, `cli_utils`, or `mcp_server`. Test suite total: **311 passed, 1 skipped**.


---

## [4.0.3] - 2026-07-31

### Fixed
- **Chocolatey uninstall**: Added `Uninstall-BinFile -Name "kport"` to `chocolateyuninstall.ps1` to clean up the shim created by `Install-BinFile`, resolving Chocolatey community reviewer rejection.
- **Windows elevation hint**: Improved elevation hint messages in CLI to show Windows-specific guidance (`Run as Administrator`) rather than Linux-only `sudo` instructions.
- **FallbackInspector proto parameter**: Added missing `proto: str = "tcp"` parameter to `FallbackInspector.find_ports_by_process_name()`, aligning the signature with `BaseInspector` and `PsutilInspector`.

### Changed
- **CI**: Bumped `actions/setup-python` from `v6` to `v7` across all GitHub Actions workflows, incorporating the Dependabot PR #17 upgrade.
- **Code quality**: Removed unused imports (`sys`, `USING_PSUTIL`) from `benchmarks/bench_list.py` and replaced the unused `import pytest` with `importlib.util.find_spec` in `manage.py`, resolving all Ruff F401 violations.

---

## [4.0.0] - 2026-07-21

### Added
- **Interactive TUI Picker Mode** (`kport -I` / `kport interactive`): Live interactive curses interface with fuzzy search, multi-selection, and bulk kill, with graceful text menu fallback.
- **Process Manager Awareness**: `explain` now cgroup-detects systemd units (`systemd:unit.service`), PM2 worker processes (`pm2:app-name`), and supervisorctl tasks.
- **Advanced Watch Mode**: `--until free` and `--until occupied` flags with optional `--timeout <seconds>`.
- **UDP Protocol Support**: `--proto tcp|udp|both` across all commands (`list`, `inspect`, `kill`, `conflicts`, `watch`).
- **Recursive Process Tree Kill**: `--kill-tree` flag on all kill commands (traverses children depth-first).
- **Wait for Exit**: `--wait-for-exit <seconds>` option to block script execution until the target port socket is fully closed.
- **Optional High Performance**: `fast` installation extra (`pip install kport[fast]`) allowing users to choose high-performance psutil backend while keeping the core package zero-dependency.

### Changed
- Moved `psutil` package from required dependencies to optional dependencies.
- Standardized shell autocompletions for all shells (`bash`, `zsh`, `fish`, `powershell`) with support for the new flags.

---

## [3.2.6] - 2026-07-11

### Added
- Root `NOTICE` file per Apache 2.0 license best practices.
- Stable, versioned JSON output envelope contract (`schema_version: 1`).
- Named Port Profiles support (`--profile <name>`) read from `.kport.json` config.
- Desktop notification dispatch mechanism via `src/kport/notify.py` module.
- NDJSON-formatted audit logging (`~/.kport/audit.log`) for all destructive operations.
- CLI-driven autocompletion script generation (`kport completion <shell>`).
- License drift guard to check and prevent AGPL/GPL dependencies in CI.

### Changed
- Upgraded `vscode-extension/LICENSE` from AGPL-3.0 to Apache 2.0.
- Hard-gated Docker container removal (`docker_action=rm`) to require both `--yes` and `--force`.

---

## [3.2.5] - 2026-06-18

### Changed
- Standardized Apache 2.0 licensing headers on code files.
- Improved Linux native netstat parser to handle container edge-cases.

---

## [3.2.0] - 2026-05-10

### Added
- Live watch mode monitoring port ownership.
- Core safety shields block list preventing accidental termination of critical system ports (SSH, K8s, DB, DNS).
- Config file loading defaults via `.kport.json`.
- Stable Model Context Protocol (MCP) server integration.
