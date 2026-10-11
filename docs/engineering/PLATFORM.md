# kport — Platform Capability Matrix

**Version:** v5.0.0
**Date:** 2026-08-22
**Source:** Derived from direct code inspection of `src/kport/`

> **Legend:**
> - ✅ Implemented and tested
> - ⚠️ Implemented but not tested on this platform in CI (best-effort)
> - ❌ Not implemented
> - 🔬 Implemented; relies on external tool availability

---

## 1. Port & Connection Inspection

| Capability | Linux | macOS | Windows | Code Path |
|---|---|---|---|---|
| TCP port → PID (psutil) | ✅ | ✅ | ✅ | `psutil_impl.py` — `psutil.net_connections` |
| TCP port → PID (fallback) | ✅ | ⚠️ `lsof` | ✅ ctypes IPHLPAPI | `system_impl.py` |
| UDP port → PID (psutil) | ✅ | ✅ | ✅ | `psutil_impl.py` |
| UDP port → PID (fallback) | ✅ `/proc/net/udp` | ⚠️ `lsof` | ✅ IPHLPAPI | `system_impl.py` |
| IPv4 binding | ✅ | ✅ | ✅ | All backends |
| IPv6 binding | ✅ `/proc/net/tcp6` | ⚠️ psutil | ✅ psutil | `system_impl.py`, `psutil_impl.py` |
| State field (LISTEN/ESTABLISHED) | ✅ | ✅ | ✅ | psutil + fallback |
| Local address (`laddr`) | ✅ | ✅ | ✅ | All backends |
| Active connection enumeration | ✅ | ✅ | ✅ | `list_connections()` in psutil + fallback |
| Connection filtering & bounding | ✅ | ✅ | ✅ | `diagnostics.py` `filter_connections` |

---

## 2. Process Information & Context

| Capability | Linux | macOS | Windows | Code Path |
|---|---|---|---|---|
| PID, process name | ✅ | ✅ | ✅ | `psutil_impl.py`, `system_impl.py` |
| Executable path (`exe`) | ✅ | ✅ | ✅ | `psutil.Process.exe()` / `/proc/<pid>/exe` |
| Command line (`cmdline`) | ✅ | ✅ | ✅ | `psutil.Process.cmdline()` / `/proc/<pid>/cmdline` |
| Process owner (`user`) | ✅ | ✅ | ✅ | `psutil.Process.username()` / `/proc/<pid>/status` |
| Runtime name enrichment | ✅ | ✅ | ✅ | `constants.py` `RUNTIME_ENRICHMENT_NAMES` |
| Parent PID (`ppid`) | ✅ | ✅ | ✅ | `ProcessInfo.ppid` |
| Working directory (`cwd`) | ✅ | ✅ | ✅ | `ProcessInfo.cwd` |
| Start time (`start_time`) | ✅ | ✅ | ✅ | `ProcessInfo.start_time` |
| Parent process lineage (`parent_name`) | ✅ | ✅ | ✅ | `diagnostics.py` `diagnose_port` |
| Git repository & worktree context | ✅ | ✅ | ✅ | `project.py` `resolve_project` |

---

## 3. Kill Operations

| Capability | Linux | macOS | Windows | Code Path |
|---|---|---|---|---|
| Graceful termination (SIGTERM) | ✅ | ✅ | ✅ (via `TerminateProcess`) | `base.py` `kill_pid()` |
| Force kill (SIGKILL / `TerminateProcess`) | ✅ | ✅ | ✅ | `base.py` `kill_pid()` |
| Poll-until-dead after SIGTERM | ✅ | ✅ | ✅ | `base.py` graceful timeout loop |
| Process tree kill | ✅ | ✅ | ✅ | `base.py` `kill_process_tree()` |
| sudo escalation | ✅ | ✅ | ❌ (no sudo) | `base.py` `_escalate_kill_unix()` |
| UAC elevation (Windows) | ❌ | ❌ | ⚠️ `ShellExecuteEx` requested | `base.py` `_escalate_kill_windows()` |
| `--kill-tree` flag | ✅ | ✅ | ✅ | `cli.py` + `base.py` |
| `--dry-run` mode | ✅ | ✅ | ✅ | `cli.py` all kill paths |

---

## 4. Docker Integration

| Capability | Linux | macOS | Windows | Code Path |
|---|---|---|---|---|
| Detect Docker CLI presence | ✅ | ✅ | ✅ | `docker_engine.py` `shutil.which("docker")` |
| List container port mappings | ✅ | ✅ | ✅ | `docker_engine.py` `docker ps` |
| Detect host/container port conflict | ✅ | ✅ | ✅ | `cli.py` `conflicts` command |
| Docker stop/restart/rm | ✅ | ✅ | ✅ | `docker_engine.py` |
| Snap sandbox isolation (Docker unreachable) | ✅ partial | ❌ N/A | ❌ N/A | `cli.py` `explain` `/proc` raw fallback |

---

## 5. Service Manager Detection

| Capability | Linux | macOS | Windows | Code Path |
|---|---|---|---|---|
| systemd unit detection | ✅ `/proc/<pid>/cgroup` | ❌ N/A | ❌ N/A | `process_manager.py` |
| PM2 detection (env var + binary) | ✅ | ⚠️ | ❌ | `process_manager.py` |
| Supervisor detection (`supervisorctl`) | ✅ | ⚠️ | ❌ | `process_manager.py` |
| Windows Service detection (SCM) | ❌ N/A | ❌ N/A | ✅ `tasklist /SVC` | `process_manager.py` |
| launchd detection (macOS) | ❌ | ❌ | ❌ | **Not implemented** |

---

## 6. Safety System

| Capability | Linux | macOS | Windows | Code Path |
|---|---|---|---|---|
| Centralized safety policy | ✅ | ✅ | ✅ | `safety.py` `check_safety_policy` |
| Protected ports list | ✅ | ✅ | ✅ | `constants.py` `PROTECTED_PORTS` |
| Protected process names | ✅ | ✅ | ✅ | `constants.py` `PROTECTED_PROCESS_NAMES` |
| Bypass safety (`--bypass-safety`) | ✅ | ✅ | ✅ (CLI only) | `safety.py`, `cli.py` |
| Additive config safety overrides | ✅ | ✅ | ✅ | `safety.py` `resolve_protected_sets` |
| Strict MCP safety enforcement | ✅ | ✅ | ✅ | `mcp_server.py` `handle_kill_port` |

---

## 7. Audit Logging

| Capability | Linux | macOS | Windows | Code Path |
|---|---|---|---|---|
| NDJSON audit log writes | ✅ | ✅ | ✅ | `audit.py` `~/.kport/audit.log` |
| Log rotation (10 MiB threshold) | ✅ | ✅ | ✅ | `audit.py` `_rotate_if_needed()` |
| Non-fatal on write failure | ✅ | ✅ | ✅ | `audit.py` broad exception catch |
| Log directory auto-creation | ✅ | ✅ | ✅ | `audit.py` `mkdir(parents=True)` |

---

## 8. CLI & Shell Completion

| Capability | Linux | macOS | Windows | Notes |
|---|---|---|---|---|
| bash completion | ✅ | ✅ | ❌ | `cli.py` `completion bash` |
| zsh completion | ✅ | ✅ | ❌ | `cli.py` `completion zsh` |
| fish completion | ✅ | ✅ | ❌ | `cli.py` `completion fish` |
| PowerShell completion | ❌ | ❌ | ✅ | `cli.py` `completion powershell` |
| ANSI colour output | ✅ | ✅ | ✅ (Windows Terminal) | `formatter.py` — disabled for non-ANSI consoles |
| `--json` output (machine-readable) | ✅ | ✅ | ✅ | `cli.py` `_json_out()` `schema_version: 1` |

---

## 9. MCP Server

| Capability | Linux | macOS | Windows | Notes |
|---|---|---|---|---|
| Stdio JSON-RPC server | ✅ | ✅ | ✅ | `mcp_server.py` |
| Protocol version `2024-11-05` | ✅ | ✅ | ✅ | Verified via test suite |
| `list_ports` tool | ✅ | ✅ | ✅ | Local + Docker listening ports |
| `inspect_port` tool | ✅ | ✅ | ✅ | Port detail lookup |
| `kill_port` tool | ✅ | ✅ | ✅ | Safe termination (`force=False` default) |
| `diagnose_port` tool | ✅ | ✅ | ✅ | Semantic observations, inferences, risks, recommendations |
| `list_connections` tool | ✅ | ✅ | ✅ | Filterable active connection inspection (bounded) |
| `conflicts` tool | ✅ | ✅ | ✅ | Docker vs native host conflict detection |
| `doctor` tool | ✅ | ✅ | ✅ | Environment-wide health check |
| Safety shield in MCP | ✅ | ✅ | ✅ | Strict enforcement, no bypass |

---

## 10. Inspection Backends (Decision Matrix)

The backend is selected at runtime by `src/kport/inspectors/__init__.py`:

```
psutil installed AND accessible?
    YES → PsutilInspector    (preferred, cross-platform, accurate)
    NO  → FallbackInspector  (OS-native: /proc, ctypes, lsof)
```

### FallbackInspector per-platform strategy

| Operation | Linux | macOS | Windows |
|---|---|---|---|
| List listening TCP | `/proc/net/tcp` + `/proc/net/tcp6` | `lsof -nP -iTCP -sTCP:LISTEN` | ctypes `GetExtendedTcpTable` (IPHLPAPI) |
| List listening UDP | `/proc/net/udp` + `/proc/net/udp6` | `lsof -nP -iUDP` | ctypes `GetExtendedUdpTable` (IPHLPAPI) |
| PID → process name | `/proc/<pid>/comm` | `ps -o comm= -p <pid>` | `tasklist /FI "PID eq <pid>"` |
| PID → exe path | `/proc/<pid>/exe` (symlink) | `lsof -p <pid>` | PowerShell `Get-Process` |
| PID → cmdline | `/proc/<pid>/cmdline` | `ps -o args= -p <pid>` | WMI or PowerShell |
| Kill (no psutil) | `os.kill(pid, signal)` | `os.kill(pid, signal)` | `ctypes.windll.kernel32.TerminateProcess` |

---

## 11. Known Limitations

| Limitation | Affected Platform(s) | Severity |
|---|---|---|
| launchd detection absent | macOS | Low — PM2/Supervisor detection still works |
| lsof fallback reliability | macOS | Low — lsof is bundled with macOS; generally reliable |
| Snap sandbox Docker isolation | Linux (snap-installed kport) | Low — `/proc` raw fallback exists |
| UAC elevation | Windows | Medium — ShellExecuteEx is called but UAC prompt behavior untested in CI |
| IPv6 state reporting | All | Low — state field may be `None` in fallback IPv6 path |

---

## 12. CI Coverage

| Platform | CI Job | Notes |
|---|---|---|
| Linux (ubuntu-latest) | ✅ `ci.yml` | Primary test environment |
| macOS | ❌ | No macOS CI job; macOS paths are best-effort |
| Windows | ✅ | `ci.yml` Windows runner |
| Snap | ⚠️ build only | Built in `build-packages.yml`; not integration-tested |
