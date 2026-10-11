import { execFile } from "child_process";
import { promisify } from "util";
import * as vscode from "vscode";
import { configureWorkspaceMcp, maybeAutoConfigureMcp } from "./mcpConfig";

const execFileAsync = promisify(execFile);

function getExecutable(): string {
  return vscode.workspace.getConfiguration("kport").get<string>("executablePath", "kport");
}

async function runKport(args: string[]): Promise<{ stdout: string; stderr: string }> {
  const executable = getExecutable();
  try {
    const result = await execFileAsync(executable, args, {
      maxBuffer: 10 * 1024 * 1024,
      windowsHide: true,
    });
    return { stdout: result.stdout, stderr: result.stderr };
  } catch (error) {
    const err = error as NodeJS.ErrnoException & { stdout?: string; stderr?: string; code?: number };
    if (err.code === "ENOENT") {
      throw new Error(
        `kport not found (${executable}). Install from https://github.com/farman20ali/kport/releases or set kport.executablePath.`
      );
    }
    const message = (err.stderr || err.stdout || err.message || "kport command failed").trim();
    throw new Error(message);
  }
}

async function promptPort(action: "inspect" | "kill"): Promise<number | undefined> {
  const value = await vscode.window.showInputBox({
    prompt: action === "inspect" ? "Port to inspect" : "Port to free",
    placeHolder: "8080",
    validateInput: (text) => {
      const port = Number(text);
      if (!Number.isInteger(port) || port < 1 || port > 65535) {
        return "Enter a valid port number (1-65535)";
      }
      return undefined;
    },
  });
  if (value === undefined) {
    return undefined;
  }
  return Number(value);
}

async function showOutput(title: string, body: string): Promise<void> {
  const doc = await vscode.workspace.openTextDocument({
    content: `# ${title}\n\n${body.trim()}\n`,
    language: "markdown",
  });
  await vscode.window.showTextDocument(doc, { preview: false });
}

async function promptMcpSetupOnFirstActivation(context: vscode.ExtensionContext): Promise<void> {
  const hasShownPrompt = context.globalState.get<boolean>("kport.mcpPromptShown");
  if (hasShownPrompt) {
    return;
  }

  // Only show prompt if workspace folder is open
  if (!vscode.workspace.workspaceFolders || vscode.workspace.workspaceFolders.length === 0) {
    return;
  }

  const choice = await vscode.window.showInformationMessage(
    "KPort MCP Extension: Configure the MCP server for AI-powered development?",
    { modal: true },
    "Yes, Configure",
    "No, Skip"
  );

  // Mark that we've shown the prompt
  await context.globalState.update("kport.mcpPromptShown", true);

  if (choice === "Yes, Configure") {
    const executable = getExecutable();
    const useLegacyArgs = vscode.workspace
      .getConfiguration("kport")
      .get<boolean>("mcp.useLegacyArgs", false);

    try {
      await runKport(["--version"]);
      const mcpPath = await configureWorkspaceMcp(executable, useLegacyArgs);
      if (mcpPath) {
        void vscode.window.showInformationMessage(
          `✅ KPort MCP server registered in ${mcpPath}. Reload the window if using Copilot.`,
          "Reload Window"
        );
      }
    } catch (error) {
      void vscode.window.showErrorMessage(`Failed to configure MCP: ${String(error)}`);
    }
  }
}

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("kport.checkInstallation", async () => {
      try {
        const { stdout } = await runKport(["--version"]);
        void vscode.window.showInformationMessage(`KPort is installed: ${stdout.trim()}`);
      } catch (error) {
        void vscode.window.showErrorMessage(String(error));
      }
    }),

    vscode.commands.registerCommand("kport.inspectPort", async () => {
      const port = await promptPort("inspect");
      if (port === undefined) {
        return;
      }
      try {
        const { stdout } = await runKport(["inspect", String(port), "--json"]);
        const parsed = JSON.parse(stdout);
        await showOutput(`Inspect port ${port}`, "```json\n" + JSON.stringify(parsed, null, 2) + "\n```");
      } catch (error) {
        void vscode.window.showErrorMessage(String(error));
      }
    }),

    vscode.commands.registerCommand("kport.freePort", async () => {
      const port = await promptPort("kill");
      if (port === undefined) {
        return;
      }
      const confirm = await vscode.window.showWarningMessage(
        `Free port ${port}? This may stop running processes or Docker containers.`,
        { modal: true },
        "Free Port"
      );
      if (confirm !== "Free Port") {
        return;
      }
      try {
        const { stdout } = await runKport(["kill", String(port), "--yes", "--json"]);
        const parsed = JSON.parse(stdout);
        await showOutput(`Freed port ${port}`, "```json\n" + JSON.stringify(parsed, null, 2) + "\n```");
        void vscode.window.showInformationMessage(`Port ${port} freed.`);
      } catch (error) {
        void vscode.window.showErrorMessage(String(error));
      }
    }),

    vscode.commands.registerCommand("kport.listPorts", async () => {
      try {
        const { stdout } = await runKport(["list", "--json"]);
        const parsed = JSON.parse(stdout);
        await showOutput("Active ports", "```json\n" + JSON.stringify(parsed, null, 2) + "\n```");
      } catch (error) {
        void vscode.window.showErrorMessage(String(error));
      }
    }),

    vscode.commands.registerCommand("kport.setupMcp", async () => {
      const executable = getExecutable();
      const useLegacyArgs = vscode.workspace
        .getConfiguration("kport")
        .get<boolean>("mcp.useLegacyArgs", false);

      try {
        await runKport(["--version"]);
      } catch (error) {
        void vscode.window.showErrorMessage(String(error));
        return;
      }

      const mcpPath = await configureWorkspaceMcp(executable, useLegacyArgs);
      if (!mcpPath) {
        void vscode.window.showWarningMessage(
          "Open a workspace folder first to write .vscode/mcp.json."
        );
        return;
      }

      const open = await vscode.window.showInformationMessage(
        `Registered kport MCP server in ${mcpPath}. Reload the window if Copilot MCP servers were already running.`,
        "Open MCP Config",
        "Reload Window"
      );
      if (open === "Open MCP Config") {
        const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(mcpPath));
        await vscode.window.showTextDocument(doc);
      } else if (open === "Reload Window") {
        await vscode.commands.executeCommand("workbench.action.reloadWindow");
      }
    })
  );

  // Show first-time MCP setup prompt
  void promptMcpSetupOnFirstActivation(context);

  // Also run auto-configure if setting is enabled
  void maybeAutoConfigureMcp();
}

export function deactivate(): void {}
