# WARD Language Server (VS Code)

Red-squiggle verification: every time you **save** a `.ward0` file, `ward.py
check` runs and the failing obligations — with their **counterexamples** — appear
as inline diagnostics under the offending contract line. This is the
as-you-type verification experience, beyond the Claude Code hooks: no agent
loop needed, the editor itself reports the proof failure.

## What's here

| Path | Purpose |
|---|---|
| `../ward_lsp.py` | The server: stdlib-only, JSON-RPC 2.0 over stdio. Runs `ward.py check --json` on `didOpen`/`didSave` and publishes diagnostics. Checks run in a background thread (never blocks the editor); concurrent saves collapse to the newest. |
| `vscode/` | A minimal VS Code extension: language registration (`ward0`), a small TextMate grammar, and an LSP client that spawns the server. |
| `test_ward_lsp.py` | Unit tests (line mapping, URIs) + an integration test that drives the real server over stdio. |

## Run it in VS Code (dev path)

```bash
cd lsp/vscode
npm install              # fetches vscode-languageclient (only the extension needs it)
```

Then open the `lsp/vscode` folder in VS Code and press **F5** (launches an
Extension Development Host with this extension active). Open any `.ward0` file,
**save** it, and the diagnostics appear. A broken contract is a red squiggle on
the `requires`/`ensures` line, message like:

```
postcondition of withdraw — ensures result == balance - amount — could not be proved on this return path — counterexample: amount=1, balance=1, result=1
```

A vacuous-spec advisory (`tau < TAU0`) shows as a yellow **warning**.

To package/install instead, first copy the server **into** the extension folder
(the packaged extension must be self-contained — it checks for a bundled
`ward_lsp.py` before the repo dev layout):

```bash
cp ../ward_lsp.py vscode/ward_lsp.py
cd vscode && npx @vscode/vsce package
code --install-extension ward-language-0.1.0.vsix
```

(Alternatively, skip packaging and set `WARD_LSP_SERVER` to point at the repo's
`ward_lsp.py`.)

## Requirements

- WARD installed (the server shells out to `ward.py check`): `ward.py setup`
  from a checkout, or the one-line installer (`~/WARD`).
- `dafny` + `z3` on PATH for live verification (same as the CLI).

## Environment variables

| Var | Default | Meaning |
|---|---|---|
| `WARD_HOME` | `~/WARD` | Where the WARD checkout lives (server also finds itself if run from the repo). |
| `WARD_LSP_VERIFY_LIMIT` | `30` | Per-verification solver budget (seconds) passed to `check --verify-limit`. |
| `WARD_LSP_CHECK_TIMEOUT` | `120` | Hard cap (seconds) on the whole `check` subprocess before a warning is shown. |
| `WARD_PY` | `python`/`python3` | Python used to run the *server* (any python works — it's stdlib-only). |
| `WARD_LSP_SERVER` | auto | Explicit path to `ward_lsp.py`. |

## Honest limits

- Diagnostics fire on **save and open** — not on every keystroke. A
  verification run is seconds (Dafny + Z3), so live-typing squiggles would be
  noisy; on-save is the right granularity.
- Diagnostics come from the on-disk file (what you saved).
- If WARD/venv/Dafny is missing, you get one yellow warning instead of
  silent nothing.
- It proves the stated contract — not style, elegance, or performance.
