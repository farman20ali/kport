# 🚀 Quick Start Guide — kport

## Installation (Choose One Method)

### ✅ Run directly from source (no install needed)
```bash
python -m kport --help
python -m kport list
```

### 📦 Install globally (recommended — no admin needed)
```bash
pip install --user .
```

After installation:
```bash
kport --version   # kport 3.2.x
kport list        # show all listening ports
```

### 🌍 Install from PyPI
```bash
pip install kport
```

### 🪟 Windows — Chocolatey
```powershell
choco install kport
```

### 🐧 Linux — Debian/Ubuntu
```bash
sudo dpkg -i kport_*.deb          # from GitHub Releases
```

### 🐧 Linux — RHEL / Fedora
```bash
sudo rpm -i kport-*.rpm           # from GitHub Releases
```

---

## 🎯 Core Commands

```bash
# List all listening ports (local + Docker)
kport list

# Show Docker-published ports only
kport docker

# Inspect a specific port
kport inspect 8080

# Explain why a port is occupied (conflict analysis)
kport explain 8080

# Kill a process on a port (safety-shielded, Docker-aware)
kport kill 8080

# Dry-run: show what would happen without killing
kport kill 8080 --dry-run

# Skip confirmation prompt
kport kill 8080 --yes

# Output in JSON format (CI/automation)
kport kill 8080 --json

# Detect conflicts between Docker and local processes
kport conflicts
```

---

## 🛑 Stop Managed Services

Stop a service-manager controlled process occupying a port **without raw process termination**:

```bash
# Stop the service managing port 8080 (prompts for confirmation)
kport stop-service 8080

# Preview the stop command without running it
kport stop-service 8080 --dry-run

# Escalate to process kill if service stop leaves port occupied
kport stop-service 8080 --force

# Non-interactive JSON output
kport stop-service 8080 --yes --json
```

**Supported managers:** systemd, PM2, supervisord, Windows Services  
**Note:** If no recognized manager is detected, use `kport kill` instead.

---

## 👁️ Watch Mode

Monitor a port and automatically kill processes that start using it:

```bash
# Watch port 8080 — kill anything that binds to it
kport watch 8080

# Custom poll interval (seconds)
kport watch 8080 --interval 2.5

# Dry-run (print alerts, don't kill)
kport watch 8080 --dry-run

# JSON output for CI pipelines
kport watch 8080 --json
```

---

## 🛡️ Safety Shield

Critical ports and processes are protected by default:

```bash
# This will be BLOCKED (port 22 = SSH):
kport kill 22
# → 🛡️ Security Shield Active: Port 22 is a protected port.

# Override when you really need to:
kport kill 22 --bypass-safety
```

**Protected ports by default:** 22, 53, 80, 443, 3306, 5432, 6379, 6443

**Protected processes by default:** systemd, init, docker, sshd, lsass.exe, services.exe

---

## ⚙️ Config File

Create `.kport.json` in your project root (or `~/.kport.json` for global defaults):

```json
{
  "yes": true,
  "dry_run": false,
  "force": false,
  "graceful_timeout": 5,
  "docker_action": "stop",
  "protected_ports": [8080, 9090],
  "protected_processes": ["my-critical-app"]
}
```

---

## 🤖 MCP Server (AI Agent Integration)

Run kport as an MCP server for Claude, Copilot, Cursor, and other AI assistants:

```bash
kport --mcp
```

Add to your Claude Desktop / Cursor config:

```json
{
  "mcpServers": {
    "kport": {
      "command": "kport",
      "args": ["--mcp"]
    }
  }
}
```

**MCP tools available (8 total):** `list_ports`, `inspect_port`, `kill_port`, `diagnose_port`, `list_connections`, `conflicts`, `doctor`, `stop_service`  
All tools carry annotations (`readOnlyHint` / `destructiveHint`). Destructive tools are safety-shielded.

---

## 🔧 Legacy Flags (still supported)

```bash
python -m kport -l                  # list all ports
python -m kport -i 8080             # inspect port
python -m kport -im 3000 3001 8080  # inspect multiple
python -m kport -ir 3000-3010       # inspect range
python -m kport -ip node            # inspect by process name
python -m kport -k 8080             # kill port
python -m kport -kp node            # kill by process name
python -m kport -ka 3000 3001 3002  # kill multiple ports
python -m kport -kr 3000-3010       # kill port range
```

---

## 🛠️ Developer — Build & Package

All build operations use `manage.py`:

```bash
# Set up dev environment (installs all dependencies)
python manage.py setup

# Run the full test suite
python manage.py test

# Bump version across all files at once
python manage.py sync-version 3.2.4

# Build packages (use flag for your target):
python manage.py build --all       # everything for the current OS
python manage.py build --win       # Windows .exe installer
python manage.py build --deb       # Debian .deb
python manage.py build --rpm       # RHEL .rpm
python manage.py build --snap      # Snap .snap (requires snapcraft)
python manage.py build --pypi      # PyPI wheel + sdist

# Publish packages:
python manage.py publish --pypi
python manage.py publish --snap
python manage.py publish --choco
```

See [PACKAGING.md](PACKAGING.md) and [CONTRIBUTING.md](../CONTRIBUTING.md) for full details.

---

## 📚 Documentation Files

| File | Purpose |
|------|---------|
| `README.md` | Main documentation |
| `docs/INSTALL.md` | Detailed installation guide |
| `docs/PACKAGING.md` | Cross-platform packaging |
| `docs/RELEASE_GUIDE.md` | Release workflow |
| `CONTRIBUTING.md` | How to contribute |
