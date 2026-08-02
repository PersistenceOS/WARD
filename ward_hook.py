#!/usr/bin/env python3
"""WARD auto-verify hook for Claude Code (PreToolUse + PostToolUse).

Installed by `ward.py setup` (user level, any project): merges a PreToolUse +
PostToolUse hook into ~/.claude/settings.json. (A repo-level .claude/settings.json
is deliberately NOT shipped — it would double-fire alongside the user-level hook.)

What it does, for .ward0 files ONLY:
  - PreToolUse  (before Write/Edit/MultiEdit): injects a short nudge — state the
    contract (requires/ensures, edges included) BEFORE the body, and note that
    WARD auto-verifies after every write.
  - PostToolUse (after  Write/Edit/MultiEdit): runs `ward.py check --json
    --verify-limit 30` on the touched file and injects the structured result
    (✓ PROVED, or failing obligations + counterexamples, or a tightness
    advisory) back into the conversation — verification as the code is being
    written, not after.

Design constraints (must never hurt the agent):
  - stdlib only — any python works; the heavy lifting happens in ward.py's venv
  - every failure path emits valid, empty hook output (a broken hook must
    never error the tool call)
  - fast no-op for non-.ward0 events: exits before any subprocess

Hook protocol (Claude Code):
  stdin:  JSON {hook_event_name, tool_name, tool_input:{file_path,...}, ...}
  stdout: {"hookSpecificOutput": {"hookEventName": <event>, "additionalContext": <text>}}

WARD location resolution: env WARD_HOME > this script's own repo > ~/WARD.

Opt-outs:
  WARD_HOOK_VERIFY=0  keep the PreToolUse nudge but skip the PostToolUse
                      check (for slow machines / large modules).
  WARD_HOME=<path>    point at a WARD checkout other than ~/WARD.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

MAX_CTX = 1800          # additionalContext character cap (keep the agent lean)
VERIFY_LIMIT_S = 30     # per-verification solver budget; bounded hook latency
HOOK_TIMEOUT_S = 90     # hard cap on the whole check subprocess


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


def touched_file(payload: dict) -> str | None:
    ti = payload.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("path")
    return fp if isinstance(fp, str) else None


def emit(event: str, context: str = "") -> None:
    json.dump(
        {"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}},
        sys.stdout,
    )


def run_check(repo: Path, venv_py: Path, path: str) -> dict | None:
    cmd = [str(venv_py), "ward.py", "check", path, "--json",
           "--verify-limit", str(VERIFY_LIMIT_S)]
    try:
        # stdin=DEVNULL: capture_output leaves stdin inherited, and if the
        # parent's stdin pipe stays open, dafny (.NET) blocks on it on Windows
        # (the LSP hang). ward.py never reads stdin.
        r = subprocess.run(
            cmd, cwd=str(repo), capture_output=True,
            stdin=subprocess.DEVNULL, text=True,
            timeout=HOOK_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def format_check(report: dict, path: str) -> str:
    lines = [f"[WARD auto-verify] {path}"]
    if report.get("stage") == "elaborate":
        lines.append(f"  ✗ doesn't parse/elaborate yet: {str(report.get('error'))[:200]}")
        return "\n".join(lines)
    if report.get("ok"):
        lines.append("  ✓ PROVED (contracts verified)")
        tau = report.get("tightness") or {}
        for fn, t in sorted(tau.items()):
            if t.get("action") == "demote":
                lines.append(
                    f"  ⚠ {fn}: tau={t.get('tau')} < TAU0 — vacuous spec; "
                    "tighten the ensures clause")
        return "\n".join(lines)
    triples = report.get("triples") or []
    lines.append(f"  ✗ NOT PROVED — {len(triples)} failing obligation(s):")
    for t in triples[:3]:
        loc = (t.get("location") or {}).get("surface", "?")
        lines.append(f"    {loc}: {str(t.get('violated_obligation'))[:140]}")
        if t.get("counterexample"):
            lines.append(f"      counterexample: {t['counterexample']}")
    if len(triples) > 3:
        lines.append(f"    … and {len(triples) - 3} more")
    lines.append("  Fix the named fn/contract and keep writing — this re-runs on "
                 "your next write.")
    return "\n".join(lines)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    event = payload.get("hook_event_name", "")
    fp = touched_file(payload)

    # No-op fast for anything that isn't a ward0 file.
    if not fp or not fp.endswith(".ward0"):
        emit(event)
        return

    repo = find_ward()
    if repo is None:
        emit(event)
        return

    if event == "PreToolUse":
        emit(event, (
            "[WARD] You're about to write a .ward0 file. State the contract FIRST "
            "(requires/ensures — include the edge cases: bounds, empty inputs, "
            "negative amounts), then the body. WARD auto-verifies after every "
            "write; `ensures true` is flagged as vacuous (tau < TAU0)."
        ))
        return

    # PostToolUse — WARD_HOOK_VERIFY=0 keeps the nudge but skips the check.
    if os.environ.get("WARD_HOOK_VERIFY") == "0":
        emit(event)
        return
    # file_path is relative to the AGENT's project; resolve it against this
    # hook's cwd (the project dir) so the absolute path survives the check
    # subprocess, which runs with cwd=<WARD repo>.
    path = Path(fp).resolve()
    if not path.is_file():
        emit(event)
        return
    venv_py = venv_python(repo)
    if venv_py is None:
        emit(event)
        return
    report = run_check(repo, venv_py, str(path))
    if not report:
        emit(event)
        return
    emit(event, format_check(report, fp)[:MAX_CTX])


if __name__ == "__main__":
    main()
