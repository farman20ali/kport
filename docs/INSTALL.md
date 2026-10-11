# Installation Guide

## 🚀 Quick Install

### For End Users

#### Option 1: Install from PyPI (Once Published)
```bash
pip install kport
```

#### Option 2: Install from GitHub
```bash
pip install git+https://github.com/farman20ali/kport.git
```

#### Option 3: Install from Source
```bash
# Clone the repository
git clone https://github.com/farman20ali/kport.git
cd kport

# Install
pip install .
```

### Verify Installation
After installation, verify it works:
```bash
kport --version
kport -h

# Docker-aware command style
kport list
kport docker
kport inspect 8080
```

---

## 🔧 For Developers

### Development Installation
```bash
# Clone the repository
git clone https://github.com/farman20ali/kport.git
cd kport

# Install in editable mode
pip install -e .
```

### Testing Your Installation
```bash
# Test basic commands
kport -l
kport -i 8080
```

---

## 🐧 Linux Installation

### Method 1: Native Packages (.deb & .rpm)

Pre-compiled packages are available on the [GitHub Releases](https://github.com/farman20ali/kport/releases) page.

**Debian/Ubuntu (.deb):**
```bash
sudo dpkg -i kport_*.deb
# Fix any missing dependencies:
sudo apt-get install -f
```

**RHEL/Fedora/CentOS (.rpm):**
```bash
sudo rpm -i kport-*.rpm
# or via dnf:
sudo dnf install kport-*.rpm
```

**Snap (Ubuntu and other snap-enabled distros):**
```bash
sudo snap install kport_*.snap --dangerous
# After publishing to the Snap Store:
# sudo snap install kport

# Connect necessary interface plugs for process inspection and signaling:
sudo snap connect kport:process-control
sudo snap connect kport:system-observe
sudo snap connect kport:network-observe
```

### Method 2: Using pip
```bash
# Install for current user (RECOMMENDED - avoids permission issues)
pip install --user kport

# Or install system-wide (requires sudo)
sudo pip install kport
```

> 💡 **Tip:** If the `kport` command doesn't work after installation, see the "command not found" section below.

> Note: if you install with `--user`, root may not find `kport` via `sudo`.
> Use `sudo -E` or run the full path (usually `~/.local/bin/kport`).

### Method 3: From Source
```bash
git clone https://github.com/farman20ali/kport.git
cd kport
sudo pip install .
```

### Make it Globally Accessible
If installed with `--user` and command not found, add to PATH:
```bash
# For bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# For zsh
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

> 💡 **Verify:** Check if it's already in PATH with `echo $PATH`

---

## 🪟 Windows Installation

### Method 1: Setup Installer .exe (Recommended)

1. Download the latest `kport-<version>-setup.exe` from [GitHub Releases](https://github.com/farman20ali/kport/releases).
2. Run the installer wizard. It will:
   - Install `kport.exe` to `C:\Program Files\kport\`.
   - Register it on the System `PATH` automatically.
   - Create Start Menu shortcuts and register an uninstaller.
3. Open a **new** terminal window and run `kport --version` to verify.

**Silent/Automated Install:**
```powershell
kport-setup.exe /S
```

### Method 2: Chocolatey
```powershell
choco install kport
```
*(Requires the package to be published to community.chocolatey.org, or install from a local `.nupkg` built by `choco_build.py`.)*

### Method 3: Using pip
```powershell
# Install to user directory (RECOMMENDED - no admin needed)
pip install --user kport

# If command not found, add to PATH:
$userScriptsPath = "$env:APPDATA\Python\Python311\Scripts"
[Environment]::SetEnvironmentVariable("Path", "$env:Path;$userScriptsPath", "User")
$env:Path += ";$userScriptsPath"

# Or install system-wide (requires admin)
pip install kport
```

> 💡 **Troubleshooting:** If `kport` command doesn't work, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

### Method 4: From Source
```powershell
git clone https://github.com/farman20ali/kport.git
cd kport
pip install .
```

### Method 5: Run Without Installing
```powershell
python -m kport -h
```

---

## 🍎 macOS Installation

### Method 1: Setup Installer .pkg (Recommended)

1. Download the latest `kport-<version>.pkg` from [GitHub Releases](https://github.com/farman20ali/kport/releases).
2. Open the `.pkg` file and follow the installer wizard screens.
3. Open a Terminal and run `kport --version` to verify.

### Method 2: Using pip
```bash
# Install for current user
pip3 install --user kport

# Or install system-wide
sudo pip3 install kport
```

### Method 3: Using Homebrew (If you create a formula)
```bash
brew tap yourusername/kport
brew install kport
```

---

## 🧩 VS Code Extension

Install the **KPort - Port Killer & MCP** extension for port commands and one-click MCP server setup:

1. Download `kport-vscode-*.vsix` from [GitHub Releases](https://github.com/farman20ali/kport/releases), or build locally:
   ```bash
   cd vscode-extension && npm install && npm run package
   ```
2. In VS Code: **Extensions → … → Install from VSIX…**
3. Run **KPort: Configure MCP Server for AI** to register `kport mcp` in `.vscode/mcp.json`.

---

## 🗑️ Uninstallation

```bash
pip uninstall kport
```

---

## ⚠️ Troubleshooting

### "kport: command not found" after installation

**This is the most common issue!** The executable is installed but not in your PATH.

**Linux/macOS:**
```bash
# Add pip's bin directory to PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

**Windows:**
Make sure Python's Scripts directory is in your PATH:
- `C:\Users\YourUsername\AppData\Local\Programs\Python\Python3X\Scripts\`

### Permission errors

**Linux/macOS:**
```bash
# Use --user flag
pip install --user kport

# Or use sudo
sudo pip install kport
```

**Windows:**
Run PowerShell or Command Prompt as Administrator

### Python not found

Make sure Python 3.6+ is installed:
```bash
python --version
# or
python3 --version
```

Download Python from: https://www.python.org/downloads/
