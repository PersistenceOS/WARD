#!/usr/bin/env python3
"""ward_lsp — a minimal Language Server for the WARD ward0 language.

On didOpen / didSave it runs `ward.py check --json` and publishes the failing
obligations + counterexamples as inline diagnostics — the red-squiggle
as-you-type verification experience in VS Code, beyond the Claude Code hooks.

Design:
  - stdlib only; JSON-RPC 2.0 over stdio (the LSP transport)
  - checks run in a background thread, so the server never blocks the editor
  - concurrent saves collapse: a busy server runs the newest pending file next
  - the check subprocess is bounded (WARD_LSP_VERIFY_LIMIT, default 30s)
  - WARD resolution matches ward_hook.py: WARD_HOME > this repo > ~/WARD

Run directly (stdio server — the VS Code extension spawns this):
    python ward_lsp.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

VERIFY_LIMIT_S = int(os.environ.get("WARD_LSP_VERIFY_LIMIT", "30"))
CHECK_TIMEOUT_S = int(os.environ.get("WARD_LSP_CHECK_TIMEOUT", "120"))
DIAG_MAX_CHARS = 600

# LSP DiagnosticSeverity: 1 = Error, 2 = Warning, 3 = Information
SEV_ERROR = 1
SEV_WARNING = 2


# ---------------------------------------------------------------------------
# WARD / venv resolution (mirrors ward_hook.py)
# ---------------------------------------------------------------------------

def find_ward() -> Path | None:
    """Locate the WARD repo root (env WARD_HOME > script's repo > ~/WARD)."""
    candidates: list[Path] = []
    env_home = os.environ.get("WARD_HOME")
    if env_home:
        candidates.append(Path(env_home))
    script_dir = Path(__file__).resolve().parent
    if (script_dir / "ward.py").is_file():
        candidates.append(script_dir)
    candidates.append(Path.home() / "WARD")
    for c in candidates:
        if (c / "ward.py").is_file():
            return c
    return None


def venv_python(repo: Path) -> Path | None:
    for p in (
        repo / "phase0" / ".venv" / "Scripts" / "python.exe",  # Windows
        repo / "phase0" / ".venv" / "bin" / "python",          # POSIX
    ):
        if p.is_file():
            return p
    return None


def uri_to_path(uri: str) -> Path:
    from urllib.parse import unquote, urlparse
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return Path(uri)
    path = unquote(parsed.path)
    if len(path) >= 3 and path[0] == "/" and path[1].isalpha() and path[2] == ":":
        path = path[1:]  # /C:/x -> C:/x (Windows file URIs)
    return Path(path)


# ---------------------------------------------------------------------------
# source -> line mapping (diagnostic ranges)
# ---------------------------------------------------------------------------

def _fn_header_line(lines: list[str], fn_name: str) -> int | None:
    """0-based index of the `fn <name>(` / `extern fn <name>(` header line."""
    pat = re.compile(r"\b(?:extern\s+)?fn\s+" + re.escape(fn_name) + r"\s*\(")
    for i, line in enumerate(lines):
        if pat.search(line):
            return i
    return None


def _clause_line(lines: list[str], header: int, clause: str) -> int | None:
    """0-based index of the requires/ensures line inside the fn's contract block."""
    if clause not in ("requires", "ensures"):
        return None
    for i in range(header + 1, len(lines)):
        s = lines[i].strip()
        if s.startswith("{"):
            break
        if re.match(r"\b" + clause + r"\b", s):
            return i
    return None


def _range_for(triple: dict, source: str) -> dict:
    lines = source.splitlines()
    loc = triple.get("location") or {}
    fn = loc.get("fn") or ""
    clause = loc.get("clause") or "body"
    line = _fn_header_line(lines, fn)
    if line is None:
        target = 0  # fn not in this file (e.g. composed extern) — point at the top
    else:
        target = _clause_line(lines, line, clause)
        if target is None:
            target = line
    end_col = len(lines[target]) if 0 <= target < len(lines) else 0
    return {"start": {"line": target, "character": 0},
            "end": {"line": target, "character": end_col}}


def _fn_line(lines: list[str], fn_name: str) -> int:
    line = _fn_header_line(lines, fn_name)
    return line if line is not None else 0


# ---------------------------------------------------------------------------
# check execution
# ---------------------------------------------------------------------------

def run_check(file_abs: str) -> tuple[dict | None, str | None]:
    """Run `ward.py check --json`; return (report, reason-or-None)."""
    repo = find_ward()
    if repo is None:
        return None, "WARD not found — run `ward.py setup` or set WARD_HOME"
    vpy = venv_python(repo)
    if vpy is None:
        return None, "WARD venv missing — run `ward.py setup --create-venv`"
    cmd = [str(vpy), "ward.py", "check", file_abs, "--json",
           "--verify-limit", str(VERIFY_LIMIT_S)]
    try:
        # stdin=DEVNULL is critical: capture_output leaves stdin inherited, so
        # the check would inherit the LSP server's stdin pipe — which the
        # client keeps open — and dafny (.NET) blocks on a redirected-but-open
        # pipe on Windows, hanging every check. ward.py never reads stdin.
        r = subprocess.run(cmd, cwd=str(repo), capture_output=True,
                           stdin=subprocess.DEVNULL, text=True,
                           timeout=CHECK_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None, "ward.py check did not finish in time"
    try:
        report = json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None, "ward.py check produced unreadable output"
    return report, None


# ---------------------------------------------------------------------------
# diagnostics
# ---------------------------------------------------------------------------

def _triple_diag(triple: dict, source: str) -> dict:
    msg = str(triple.get("violated_obligation") or "verification failed")
    cex = triple.get("counterexample") or {}
    if cex:
        pairs = ", ".join(f"{k}={v}" for k, v in sorted(cex.items()))
        msg += f" — counterexample: {pairs}"
    adv = triple.get("tightness_advisory")
    if adv:
        msg += f" — {adv}"
    return {
        "range": _range_for(triple, source),
        "severity": SEV_ERROR,
        "source": "ward",
        "message": msg[:DIAG_MAX_CHARS],
    }


def _warn_diag(line: int, message: str) -> dict:
    return {
        "range": {"start": {"line": line, "character": 0},
                  "end": {"line": line, "character": 0}},
        "severity": SEV_WARNING,
        "source": "ward",
        "message": message[:DIAG_MAX_CHARS],
    }


def diagnostics_for(uri: str, source: str) -> list[dict]:
    path = uri_to_path(uri)
    if path.suffix != ".ward0":
        return []
    report, reason = run_check(str(path))
    if report is None:
        return [_warn_diag(0, reason or "ward.py check unavailable")]
    if report.get("stage") == "elaborate":
        return [{
            "range": {"start": {"line": 0, "character": 0},
                      "end": {"line": 0, "character": 0}},
            "severity": SEV_ERROR,
            "source": "ward",
            "message": f"ward0 does not parse/elaborate: "
                       f"{str(report.get('error'))[:DIAG_MAX_CHARS]}",
        }]
    if report.get("ok"):
        tau = report.get("tightness") or {}
        out: list[dict] = []
        for fn, t in sorted(tau.items()):
            if t.get("action") == "demote":
                out.append(_warn_diag(
                    _fn_line(source.splitlines(), fn),
                    f"{fn}: Proven but spec tightness tau={t.get('tau')} < TAU0 — "
                    "contract too weak to justify a proof; tighten the ensures clause"))
        return out
    return [_triple_diag(t, source) for t in (report.get("triples") or [])]


# ---------------------------------------------------------------------------
# JSON-RPC transport (Content-Length framing over stdio)
# ---------------------------------------------------------------------------

def read_message() -> dict | None:
    length: int | None = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None  # EOF
        if line in (b"\r\n", b"\n", b""):
            break
        if line.lower().startswith(b"content-length:"):
            try:
                length = int(line.split(b":", 1)[1].strip())
            except ValueError:
                return None
    if length is None:
        return None
    body = sys.stdin.buffer.read(length)
    try:
        return json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def write_message(obj: dict, lock: threading.Lock) -> None:
    data = json.dumps(obj).encode("utf-8")
    with lock:
        sys.stdout.buffer.write(
            f"Content-Length: {len(data)}\r\n\r\n".encode("utf-8") + data)
        sys.stdout.buffer.flush()


# ---------------------------------------------------------------------------
# background worker (checks never block the server loop)
# ---------------------------------------------------------------------------

_qlock = threading.Lock()
_pending: tuple[str, str] | None = None
_busy = False


def _schedule(uri: str, source: str, out_lock: threading.Lock) -> None:
    global _busy, _pending
    with _qlock:
        if _busy:
            _pending = (uri, source)  # collapse concurrent saves to the newest
            return
        _busy = True
    threading.Thread(target=_run, args=(uri, source, out_lock), daemon=True).start()


def _run(uri: str, source: str, out_lock: threading.Lock) -> None:
    global _busy, _pending
    try:
        try:
            diags = diagnostics_for(uri, source)
        except Exception as exc:  # never let a crash silently drop diagnostics
            diags = [{
                "range": {"start": {"line": 0, "character": 0},
                          "end": {"line": 0, "character": 0}},
                "severity": SEV_ERROR,
                "source": "ward",
                "message": f"WARD LSP internal error: {exc}"[:DIAG_MAX_CHARS],
            }]
        write_message(
            {"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
             "params": {"uri": uri, "diagnostics": diags}},
            out_lock)
    finally:
        with _qlock:
            if _pending is not None:
                nxt, _pending = _pending, None
                threading.Thread(
                    target=_run, args=(nxt[0], nxt[1], out_lock), daemon=True).start()
            else:
                _busy = False


# ---------------------------------------------------------------------------
# server loop
# ---------------------------------------------------------------------------

def main() -> int:
    out_lock = threading.Lock()
    while True:
        msg = read_message()
        if msg is None:
            break
        method = msg.get("method")
        if method == "initialize":
            write_message(
                {"jsonrpc": "2.0", "id": msg.get("id"),
                 "result": {
                     "capabilities": {
                         "textDocumentSync": {"openClose": True, "change": 0, "save": True},
                     },
                     "serverInfo": {"name": "ward-lsp", "version": "0.1.0"},
                 }},
                out_lock)
        elif method == "textDocument/didOpen":
            td = (msg.get("params") or {}).get("textDocument") or {}
            _schedule(td.get("uri", ""), td.get("text", ""), out_lock)
        elif method == "textDocument/didSave":
            td = (msg.get("params") or {}).get("textDocument") or {}
            uri = td.get("uri", "")
            try:
                src = uri_to_path(uri).read_text(encoding="utf-8")
            except OSError:
                src = ""
            _schedule(uri, src, out_lock)
        elif method == "shutdown":
            write_message({"jsonrpc": "2.0", "id": msg.get("id"), "result": None}, out_lock)
        elif method == "exit":
            break
        else:
            if "id" in msg:
                write_message(
                    {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32601, "message": "method not found"}},
                    out_lock)
    return 0


if __name__ == "__main__":
    sys.exit(main())
