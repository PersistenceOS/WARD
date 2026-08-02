// WARD VS Code extension — minimal LSP client.
//
// Spawns ward_lsp.py (stdlib-only, no pip deps) over stdio and wires LSP
// diagnostics for .ward0 files: on save, `ward.py check` runs and the failing
// obligations + counterexamples appear as inline squiggles.
//
// Env overrides:
//   WARD_PY          python to run the server (default: python on Windows, python3 elsewhere)
//   WARD_LSP_SERVER  explicit path to ward_lsp.py (default: bundled, else repo dev layout)
//   WARD_HOME        where the WARD checkout lives (read by the server itself)

const fs = require("fs");
const path = require("path");
const vscode = require("vscode");
const { LanguageClient, TransportKind } = require("vscode-languageclient/node");

function resolvePython() {
  if (process.env.WARD_PY) return process.env.WARD_PY;
  return process.platform === "win32" ? "python" : "python3";
}

function resolveServer() {
  if (process.env.WARD_LSP_SERVER) return process.env.WARD_LSP_SERVER;
  const bundled = path.join(__dirname, "ward_lsp.py");
  if (fs.existsSync(bundled)) return bundled;
  // dev layout: lsp/vscode/ -> repo root ward_lsp.py
  return path.join(__dirname, "..", "..", "ward_lsp.py");
}

function activate(context) {
  const serverOptions = {
    command: resolvePython(),
    args: [resolveServer()],
    transport: TransportKind.stdio,
    options: {
      cwd: (vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0])
        ? vscode.workspace.workspaceFolders[0].uri.fsPath
        : undefined,
    },
  };
  const client = new LanguageClient(
    "wardLsp",
    "WARD Language Server",
    serverOptions,
    { documentSelector: [{ language: "ward0" }] }
  );
  context.subscriptions.push(client.start());
}

function deactivate() {}

module.exports = { activate, deactivate };
