# KPort VS Code Extension

Inspect and free ports from VS Code, and register the **kport MCP server** for AI assistants (GitHub Copilot, Cursor, Claude Desktop).

## Prerequisites

Install [kport](https://github.com/farman20ali/kport) on your machine:

```bash
pip install kport
# or download .deb / .exe / .pkg from GitHub Releases
```

Ensure `kport --version` works in your terminal.

## Commands

| Command | Description |
|---------|-------------|
| **KPort: Inspect Port...** | Show what is using a port (JSON output) |
| **KPort: Free Port...** | Safely free a port (respects Safety Shield) |
| **KPort: List Ports** | List active listening ports |
| **KPort: Configure MCP Server for AI** | Write `.vscode/mcp.json` for Copilot MCP |
| **KPort: Check Installation** | Verify `kport` is available |

## MCP Integration

Run **KPort: Configure MCP Server for AI** to add this to your workspace `.vscode/mcp.json`:

```json
{
  "servers": {
    "kport": {
      "type": "stdio",
      "command": "kport",
      "args": ["mcp"]
    }
  }
}
```

Available MCP tools: `list_ports`, `inspect_port`, `kill_port`.

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `kport.executablePath` | `kport` | Path to the kport binary |
| `kport.mcp.autoConfigure` | `false` | Auto-register MCP on startup |
| `kport.mcp.useLegacyArgs` | `false` | Use `--mcp` instead of `mcp` subcommand |

## Build from source

```bash
cd vscode-extension
npm install
npm run package
```

Produces `kport-vscode-*.vsix`.
