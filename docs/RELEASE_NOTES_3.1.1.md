# kport v3.1.1 Release Notes

**Release Date:** February 3, 2026

Bug fix release with enhanced kill strategies for stubborn processes.

---

## 🆕 What's New in 3.1.1

### Enhanced Process Termination
**The #1 requested improvement**: kport now reliably kills stubborn processes that previously wouldn't die!

- **Multi-tier kill strategy**: Automatically escalates from SIGTERM → SIGKILL → fuser
- **Automatic `fuser` fallback**: When `--force` is used and standard signals fail, kport automatically tries `fuser -k <port>/tcp` on Linux
- **Perfect for Java apps**: Solves the common problem of Java processes that ignore SIGTERM/SIGKILL
- **Zero configuration**: Works automatically when `fuser` is installed (from `psmisc` package)

**Example:**
```bash
# This now works reliably for stubborn Java/Node/Python processes!
kport kill 8081 --force
```

**Before:** `✗ Failed to kill PID 2993165 (Still running after graceful timeout)`  
**After:** `✓ Killed process(es) using port 8081 with fuser`

---

## 🔧 Changes from 3.1.0

### Bug Fixes
- Fixed issue where Java and other stubborn processes wouldn't terminate with standard kill signals
- Enhanced SIGKILL implementation with verification step
- Added automatic fallback to `fuser -k` on Linux when other methods fail

### Improvements
- Better process termination verification
- Clearer error messages when kill fails
- Updated help text to mention fuser support

### New Features
- Added `kill_port()` method to BaseInspector for port-based killing
- Added `_kill_port_with_fuser()` helper for Linux systems
- Automatic fuser fallback when `--force` flag is used

---

## 🐧 Installation

### PyPI (pip)
```bash
pip install kport==3.1.1
# or user install (recommended)
pip install --user kport==3.1.1
```

### Upgrade from 3.1.0
```bash
pip install --upgrade kport
```

### Debian/Ubuntu
```bash
wget https://github.com/farman20ali/kport/releases/download/v3.1.1/kport_3.1.1-1_all.deb
sudo dpkg -i kport_3.1.1-1_all.deb
```

### From Source
```bash
pip install git+https://github.com/farman20ali/kport.git@v3.1.1
```

---

## 📋 Usage Examples

```bash
# Kill stubborn processes (Java, Node, Python) - now works reliably!
kport kill 8081 --force

# Install fuser for best results (one-time setup)
sudo apt-get install psmisc  # Ubuntu/Debian
sudo yum install psmisc      # RHEL/CentOS/Fedora

# Regular usage still works as before
kport list
kport inspect 8080
kport kill 5000 --yes
```

---

## 🔧 Technical Details

### Dependencies
- **Python:** 3.6 or higher
- **Required:** `psutil >= 5.9.0` (for best cross-platform support)
- **Optional:** Docker CLI (for Docker features)
- **Optional:** `fuser` command (from `psmisc` package on Linux, for enhanced kill capability)

### Platform Support
- **Linux** - Full support with enhanced kill (uses `psutil` + `fuser` fallback, or `lsof`/`ss`/`netstat`)
- **macOS** - Full support (uses `psutil`, falls back to `lsof`)
- **Windows** - Full support (uses `psutil`, falls back to PowerShell/`netstat`)

### Exit Codes
- `0` - Success
- `1` - General error
- `2` - Invalid input
- `3` - Permission denied
- `4` - Port used by Docker container
- `5` - Port is free

---

## 📚 Documentation

- **Installation Guide:** [INSTALL.md](https://github.com/farman20ali/kport/blob/main/INSTALL.md)
- **Quick Start:** [QUICKSTART.md](https://github.com/farman20ali/kport/blob/main/QUICKSTART.md)
- **Kill Improvements:** [KILL_IMPROVEMENT.md](https://github.com/farman20ali/kport/blob/main/KILL_IMPROVEMENT.md)
- **Contributing:** [CONTRIBUTING.md](https://github.com/farman20ali/kport/blob/main/CONTRIBUTING.md)

---

## 🐛 Known Issues

- On some Linux systems, detecting PIDs for system services requires `sudo` or root privileges
- Docker detection requires Docker CLI to be installed and accessible in PATH
- Color output may not work on older Windows versions (< Windows 10)
- `fuser` fallback requires the `psmisc` package on Linux (install with `apt-get install psmisc`)

---

## 📝 Changelog

**v3.1.1** (Feb 3, 2026)
- Enhanced kill mechanism with automatic fuser fallback
- Fixed stubborn process termination issues
- Improved SIGKILL verification
- Better error messages for kill failures

**v3.1.0** (Jan 20, 2026)
- Docker-aware port detection
- Interactive confirmations
- JSON output support
- Config file support

---

## 🙏 Credits

Thanks to all users who reported issues with stubborn Java processes not being killed. This release directly addresses that feedback!

---

## 📝 License

GNU Affero General Public License v3.0 (AGPL-3.0) - see [LICENSE](https://github.com/farman20ali/kport/blob/main/LICENSE) for details.

**This license requires sharing source code modifications, even for network/SaaS use.**

---

**Full Changelog:** https://github.com/farman20ali/kport/compare/v3.1.0...v3.1.1
