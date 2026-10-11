# 🚀 kport v5.0.0 — Release Notes

**Release Date:** 2026-08-22  
**Tag:** `v5.0.0`  
**Branch:** `features`  

---

## Overview

kport 5.0.0 is a major evolutionary release transforming `kport` from a port inspection and termination tool into a comprehensive local development diagnostic intelligence platform.

Major highlights include:
1. **Diagnostic Intelligence (`kport diagnose` / `diagnose_port` MCP)**: Rich structured analysis providing observations, root-cause inferences, operational risk warnings, and actionable remediation recommendations.
2. **Environment Diagnostics (`kport doctor` / `doctor` MCP)**: Aggregated health check reporting platform capabilities, backend status (psutil/fallback), listening sockets, active connection summary, process metadata, and Docker mappings.
3. **Active Connection Inspection (`kport connections` / `list_connections` MCP)**: High-performance connection enumeration and filtering across TCP/UDP with configurable result bounding.
4. **Managed Service Stop (`kport stop-service` / `stop_service` MCP)**: Cleanly stop process-manager controlled services (systemd, PM2, supervisord, Windows Services) occupying a port — with optional `--force` escalation (CLI only) when the service stop leaves the port occupied.
5. **Centralized Safety Policy (`kport.safety`)**: Consolidated safety checks and protected resource lists into a single source of truth across CLI, TUI, and MCP, featuring additive configuration semantics.
6. **Deep Context & Lineage**: Enriched process inspection with parent process lineage (`parent_name`, `ppid`), Git project/worktree resolution with credential sanitization, and Windows Service detection.

---

## 🌟 New Features & Capabilities

### 1. Structured Port Diagnostics (`kport diagnose` / `diagnose_port`)
Runs semantic diagnostic analysis on any port to answer not just *what* is using a port, but *why* and *how to safely fix it*.
- **Observations:** Local bindings, PIDs, active connections (bounded to 50), and Docker container mappings.
- **Inferences:** Automated detection of process managers (systemd, PM2, supervisord, Windows Services), Docker network isolation, and Git repository context.
- **Risks:** Highlights wildcard binding (`0.0.0.0` / `::`), auto-restart loops from service managers, and protected port/process flags.
- **Recommendations:** Suggests context-aware commands (e.g. `systemctl stop`, `Stop-Service`, `docker stop`, or `kport kill`).

### 2. Environment Doctor (`kport doctor` / `doctor`)
A full environment-wide audit tool:
- Summarizes inspection platform capabilities and active inspector backend.
- Reports all listening sockets and flags wildcard exposures.
- Aggregates TCP connection states (`LISTEN`, `ESTABLISHED`, `TIME_WAIT`, `CLOSE_WAIT`).
- Inspects and displays process owners, service managers, and Git project context for all active listeners.
- Queries Docker for published container port mappings.

### 3. Active Network Connection Enumeration (`kport connections` / `list_connections`)
- Lists and filters active host connections by PID, process name (case-insensitive substring), port (local or remote), and state.
- Hard-capped results (`max_results`, default 500) prevent payload explosion on busy development machines.
- Clean JSON output and table presentation without internal sentinel pollution.

### 4. Managed Service Stop (`kport stop-service` / `stop_service`)

Safely stops a process-manager controlled service that is occupying a port, without raw process termination.

```bash
# Stop whatever service manager owns port 8080
kport stop-service 8080

# Preview the stop command without running it
kport stop-service 8080 --dry-run

# Escalate to process kill if service stop leaves port occupied (CLI only)
kport stop-service 8080 --force

# Skip confirmation and output JSON
kport stop-service 8080 --yes --json
```

**Supported managers:** `systemd` (`systemctl stop`), `PM2` (`pm2 stop`), `supervisord` (`supervisorctl stop`), Windows Services (`Stop-Service`).

**Safety invariants:**
- Safety policy is always enforced before acting (protected ports/processes are blocked).
- A port with **no recognized process manager is rejected** — even with `--force`. Use `kport kill` for unmanaged processes.
- `--force` escalation **re-discovers** the current port owner and **re-runs the safety policy** before killing. It does not blindly kill.
- The MCP `stop_service` tool has **no `force` parameter** — force escalation is CLI-only.

### 5. Docker Conflict Detection (`conflicts` MCP)
- Detects host ports mapped to Docker containers that are concurrently occupied or bound by native host processes.

### 6. Centralized Safety Policy (`src/kport/safety.py`)
- Single source of truth for safety enforcement across CLI, TUI (`interactive.py`), and MCP (`mcp_server.py`).
- **Additive Configuration:** Configured `protected_ports` and `protected_processes` now extend the default safety shield rather than replacing it.
- **MCP Safety Guarantee:** MCP server strictly enforces safety policy on `kill_port` and `stop_service` with no dynamic bypass.

### 7. Process Lineage & Project Detection
- Enriched `ProcessInfo` with `ppid`, `cwd`, `start_time`, and parent process name (`parent_name`).
- Windows Service resolution via `tasklist /SVC`.
- Native Git root, branch, worktree, and credential-sanitized remote URL resolution via `project.py`.

---

## 🤖 MCP Server Capabilities

kport's Model Context Protocol (MCP) server provides 8 native tools for AI coding assistants:

| Tool | Purpose | Read-Only | Safety Protected |
|---|---|---|---|
| `list_ports` | List active listening ports (local + Docker) | Yes | N/A |
| `inspect_port` | Detailed metadata for a single port | Yes | N/A |
| `kill_port` | Safely terminate process or stop container | No | Enforced |
| `diagnose_port` | Structured observations, inferences, risks, recommendations | Yes | N/A |
| `list_connections` | Enumerate and filter active network connections | Yes | N/A |
| `conflicts` | Detect Docker vs native port collisions | Yes | N/A |
| `doctor` | Environment-wide health check and listener summary | Yes | N/A |
| `stop_service` | Stop a process-manager controlled service by port | No | Enforced |

All tools carry MCP `annotations` (`readOnlyHint` / `destructiveHint`) for AI assistant tooling awareness.  
Protocol version is locked to `"2024-11-05"`.

---

## 📊 Test Verification

| Test Module | Tests | Status |
|---|---|---|
| `test_cli_commands.py` | 24 | ✅ Passed |
| `test_cli_diagnose.py` | 7 | ✅ Passed |
| `test_cli_stop_service.py` | 10 | ✅ Passed |
| `test_cli_watch.py` | 4 | ✅ Passed |
| `test_mcp_safety.py` | 5 | ✅ Passed |
| `test_mcp_server.py` | 11 | ✅ Passed |
| `test_mcp_stop_service.py` | 13 | ✅ Passed |
| `test_mcp_tools.py` | 44 | ✅ Passed |
| `test_publish_pypi.py` | 1 | ✅ Passed |
| `test_interactive.py` | 12 | ✅ Passed |
| `test_tui_diagnose.py` | 6 | ✅ Passed |
| `test_audit.py` | 6 | ✅ Passed |
| `test_audit_completeness.py` | 10 | ✅ Passed |
| `test_connections.py` | 7 | ✅ Passed |
| `test_diagnostics.py` | 26 | ✅ Passed |
| `test_doctor.py` | 28 | ✅ Passed |
| `test_enrichment.py` | 5 | ✅ Passed |
| `test_inspectors.py` | 12 | ✅ Passed |
| `test_kill_domain.py` | 9 | ✅ Passed |
| `test_notify.py` | 1 | ✅ Passed |
| `test_process_manager.py` | 7 | ✅ Passed |
| `test_process_tree.py` | 3 | ✅ Passed |
| `test_profile.py` | 3 | ✅ Passed |
| `test_project.py` | 31 | ✅ 30 Passed, 1 Skipped |
| `test_safety.py` | 12 | ✅ Passed |
| `test_service_actions.py` | 12 | ✅ Passed |
| **Total** | **311 passed, 1 skipped** | ✅ |

---

## 🏗️ Architecture Changes

### New modules
- **`src/kport/service_actions.py`** — Pure domain layer for managed service stop execution. No CLI/MCP imports; returns `ServiceActionResult`. Mockable `subprocess.run`.

### Modified modules
- **`src/kport/audit.py`** — Added `log_service_stop()` for NDJSON audit trail of service stop actions.
- **`src/kport/cli.py`** — Added `stop-service` subcommand with `handle_stop_service` orchestrator. Force escalation logic (re-inspect → re-safety-check → kill) is CLI-only.
- **`src/kport/mcp_server.py`** — Added `stop_service` tool to TOOLS catalog (with `destructiveHint` annotation); implemented `handle_stop_service` handler; added `readOnlyHint`/`destructiveHint` annotations to all 8 tools.

---

## ⚠️ Known Limitations

- `--force` process-kill escalation is **CLI-only**. The MCP `stop_service` tool will return `requires_force: true` in the response when the port remains occupied after service stop; the calling agent should inform the user to run `kport kill <port>` manually.
- macOS `launchd` is not yet a supported manager (deferred to Phase 5.1).
- TUI `[S]` stop-service action is deferred to Phase 5.1.
- Framework detection and dependency-graph analysis are out of scope for this release.
