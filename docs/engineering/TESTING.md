# kport Testing Architecture

This document defines the testing architecture, naming conventions, directory structure, fixture usage guidelines, and workflow commands for the `kport` codebase.

---

## 1. Directory Structure & Responsibilities

The test suite is located in the [tests/](file:///d:/workspace/dev/kport/tests/) directory and is organized into responsibility-driven layers:

```text
tests/
├── conftest.py                   # Shared pytest fixtures and stub definitions
│
├── unit/                         # Unit tests (Isolated domain logic, subsystem tests)
│   ├── test_safety.py            # Safety policy, custom config overrides, bypass behavior
│   ├── test_diagnostics.py       # Port diagnosis, conflict checks, connection filtering
│   ├── test_doctor.py            # Doctor report data aggregators
│   ├── test_project.py           # Git repo, detached head, worktree, credentials sanitization
│   ├── test_process_manager.py   # Windows Service, systemd, PM2, supervisord detection
│   ├── test_process_tree.py      # Child process resolution, process tree termination
│   ├── test_connections.py       # ConnectionInfo parsing, state mapping
│   ├── test_inspectors.py        # Inspector selection logic, psutil fallback
│   ├── test_enrichment.py        # ProcessInfo metadata enrichment
│   ├── test_profile.py           # Named port profile resolution
│   ├── test_notify.py            # Desktop notification dispatching
│   ├── test_audit.py             # NDJSON audit log generation and rotation
│   ├── test_audit_completeness.py # Audit record emission from TUI/MCP kill paths
│   └── test_service_actions.py   # Managed service stop commands (systemd, PM2, supervisord, Windows)
│
├── cli/                          # Command-line interface tests
│   ├── test_cli_commands.py      # Subcommand routing, exit codes, JSON envelopes
│   ├── test_cli_diagnose.py      # diagnose CLI rendering and format checks
│   ├── test_cli_doctor.py        # doctor CLI report rendering
│   ├── test_cli_stop_service.py  # Service stop CLI integration tests
│   └── test_cli_watch.py         # watch mode state transitions and timeouts
│
├── mcp/                          # Model Context Protocol stdio server tests
│   ├── test_mcp_server.py        # JSON-RPC framing, initialize, tools/list, isError consistency
│   ├── test_mcp_tools.py         # All MCP tools call verification
│   ├── test_mcp_safety.py        # MCP safety shield enforcement and config additivity
│   └── test_mcp_stop_service.py  # stop_service MCP tool validation
│
├── tui/                          # Curses TUI and text fallback picker tests
│   ├── test_interactive.py       # Selection picker, numbered menu, confirmation gates
│   └── test_tui_diagnose.py      # TUI diagnose overlays and key handling
│
└── packaging/                    # Packaging validation
    └── test_publish_pypi.py      # License metadata check in PyPI script
```

---

## 2. Naming Conventions

### Test Files
Tests must be named after their target domain or responsibility, not the release phase or feature batch.
- **Bad:** `test_phase4.py`, `test_new_features.py`, `test_phase5.py`
- **Good:** `test_safety.py`, `test_diagnostics.py`, `test_mcp_tools.py`

### Test Functions & Classes
Test names must explicitly communicate:
1. **WHAT** behavior is tested.
2. **UNDER WHAT** condition.
3. **EXPECTED** outcome.

- **Bad:** `test_safety_rule_1()`, `test_max()`
- **Good:** `test_kill_port_blocks_default_protected_port()`, `test_list_connections_clamps_max_results_to_2000()`

---

## 3. Fixtures & Test Helpers

All shared test fixtures and helpers are defined centrally in [tests/conftest.py](file:///d:/workspace/dev/kport/tests/conftest.py).

### Core Helpers
- **`FakeInspector`**: A simulated, in-memory implementation of `BaseInspector` that permits custom configuration of PIDs, port bindings, process info, active connections, and listening sockets.
- **`_binding(...)`**: Constructs a mock `PortBinding` dataclass instance.
- **`_conn(...)`**: Constructs a mock `ConnectionInfo` dataclass instance.
- **`_args(...)`**: Constructs an `argparse.Namespace` with standard CLI argument defaults.
- **`_send_mcp_messages(...)`**: Simulates sending a sequence of JSON-RPC protocol messages directly to the MCP server stdin and collects parsed response payloads from stdout.

---

## 4. How to Run Tests

### Running the Entire Suite
To run all tests:
```bash
python -m pytest tests/ -q
```

### Running Specific Layers via Pytest Markers
Pytest markers are registered in `pyproject.toml` to support targeted verification:
```bash
# Run unit tests only
python -m pytest tests/ -m unit

# Run CLI tests only
python -m pytest tests/ -m cli

# Run MCP server tests only
python -m pytest tests/ -m mcp

# Run TUI picker tests only
python -m pytest tests/ -m tui
```

### Generating Coverage Reports
To generate terminal reports with line-by-line coverage gaps highlighted:
```bash
python -m pytest tests/ --cov=kport --cov-report=term-missing
```

---

## 5. Platform-Specific Limitations & Skipped Tests

- **Curses/TTY Tests**: Tests that require a live curses interactive terminal session (`test_curses_main_key_handling`, `test_curses_main_reload_handling` in `test_interactive.py`) are skipped unless a physical TTY is active (which is typical for CI nodes or Windows shell runners without pseudo-terminal redirection).
- **Windows Service Detection**: Mocked `tasklist /SVC` command outputs are utilized to test service resolution in a platform-neutral way, ensuring these tests can pass on macOS and Linux.

---

## 6. Guidelines: Unit vs. Integration Tests

- **Unit Tests**: Must be fast, clean, and isolated. Use mock objects, `FakeInspector`, and monkeypatched attributes to inspect functions in isolation.
- **Integration Tests**: Should target complete round-trip workflows (e.g. MCP JSON-RPC requests feeding into tool call handlers and safety shields) to assert that boundaries integrate correctly without relying on machine-specific state.
