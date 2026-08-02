#!/usr/bin/env python3
"""ward — the WARD command-line tool.

One command to verify ward0 code from any terminal or AI agent
(Claude Code, Cursor, VS Code, OpenCode, plain CLI):

    python ward.py check path/to/file.ward0      # transpile + prove + diagnose
    python ward.py emit  path/to/file.ward0      # transpile to Dafny only
    python ward.py proof path/to/file.ward0      # emit a .proof certificate
    python ward.py doctor                        # diagnose the install: which
                                                 # checkout `ward` resolves to,
                                                 # repo.txt, venv, toolchain,
                                                 # integrations
    python ward.py setup                         # install the Claude Code skill +
                                                 # Cursor rule + a global `ward`
                                                 # command; check venv + toolchain

After `ward.py setup`, a global `ward` command (from bin/ward / bin/ward.cmd)
is installed into ~/.ward/bin and added to PATH, so the CLI works from ANY
terminal and ANY project directory:

    ward setup
    ward check path/to/file.ward0
    ward proof path/to/file.ward0
    ward doctor

It resolves the WARD checkout via $WARD_HOME > ~/.ward/repo.txt > its own
repo > ~/WARD, then delegates to the venv python — no cd-ing into the repo
needed. `ward doctor` prints the resolution and integration state so 'why is
ward using that copy?' is answerable in one command.

`check` is the important one. It runs the real toolchain:
    1. elaborate (ward0 surface -> ward-core IR -> Dafny 4)
    2. prove (dafny verify + Z3, the SMT checker)
    3. diagnose (raw verifier output -> structured (location, obligation,
       counterexample) triples in ward0 surface terms — the model's repair
       input), plus the advisory Specification-Tightness (tau) report that
       flags a Proven fn whose contract is too weak to justify a proof.

Exit codes: 0 = verified clean; 1 = verification failed (triples printed);
2 = toolchain error (transpile/elaboration failure, dafny not found).

Requires: python 3.10+, lark, and `dafny` (4.11) + `z3` (4.12) on PATH for
live verification. The repo's phase0/venv has lark; Dafny lives separately.

    # from the WARD repo (Windows):
    python ward.py check phase0/benchmarks/w_tasks/w5_currency_roundtrip.ward0
    # or any standalone ward0 file with externs composed the harness way:
    python ward.py check path/to/your_module.ward0 --enforce

Use `--json` for machine-readable output (agents).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# The toolchain lives under phase0/ (harness/, wardcore/, transpiler/,
# grammar/). Make it importable no matter where this script is run from.
PHASE0 = Path(__file__).resolve().parent / "phase0"
sys.path.insert(0, str(PHASE0))


def _task_descriptor(path: Path) -> dict | None:
    """If the input is a benchmark task, return its JSON descriptor so extern
    declarations can be composed (the harness's `trust` arm). Benchmark w-task
    .ward0 files are caller-only — their externs live in the sibling .json."""
    import json

    candidates = [path.with_suffix(".json")]
    for d in (PHASE0 / "benchmarks" / "w_tasks", PHASE0 / "benchmarks" / "tasks"):
        if d.is_dir():
            candidates.append(d / f"{path.stem}.json")
    for c in candidates:
        if c.is_file():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _load_source(path: Path) -> str:
    """Read a .ward0 file, composing extern declarations from the sibling task
    descriptor when present (the exact way the harness verifies w-tasks).

    Composition delegates to the E5-real gate's `compose_module` — the single
    canonical composer that emits the deps header, each extern def with its
    `effect:`/`dep:` references and the mandatory T3 `trust:` line, then the
    multi-fn ward0 source with its per-fn `tier:`/`effects:` annotations. A
    hand-rolled composition would silently drop annotations and break the
    T5/E4b passes (observed with w11's effect check)."""
    src = path.read_text(encoding="utf-8")
    desc = _task_descriptor(path)
    # only delegate when the descriptor declares externs — t-task JSONs (pure
    # functions, no externs) lack the key and compose_module would KeyError
    if desc is not None and desc.get("externs"):
        from wardcore.e5_real_gate import compose_module

        src = compose_module(desc, src)
    return src


def _load_elaborator(enforce: bool):
    from wardcore.elaborator import Elaborator

    return Elaborator(enforce_boundary=enforce)


def _has_tested_tier(elab) -> bool:
    """True if any fn carries the Tested tier (its `{:verify false}` triggers
    Dafny's dev-only advisory warning — R10/E5 finding — so verification must
    pass --allow-warnings or a clean proof is wrongly rejected)."""
    from wardcore.ir import Tier

    return any(getattr(f, "tier", None) is Tier.TESTED for f in (elab.module.fns if elab.module else ()))


def _run_check(source: str, enforce: bool, emit: bool, verify_limit: int | None) -> dict:
    """Return a report dict: {ok, stages..., triples[], tightness{}}.

    Never raises for verification failures — they become structured triples.
    Raises only for toolchain errors (bad source, missing dafny).
    """
    from harness.dafny_runner import DafnyRunner

    report: dict = {"ok": False, "source": source}

    # 1. elaborate (typed pipeline: desugar -> typecheck -> extern pass ->
    #    tier/effects/dep/linearity passes -> I1 tightness -> emit)
    elab = _load_elaborator(enforce)
    try:
        emitted = elab.transpile(source)
    except Exception as exc:  # ElaborationError / parse errors
        report["stage"] = "elaborate"
        report["error"] = str(exc)
        report["exit"] = 2
        return report
    report["stage"] = "verify"
    report["emitted"] = emitted if emit else ""

    # 2. prove (dafny verify + Z3). Tested-tier modules need --allow-warnings
    #    (their {:verify false} triggers a dev-only advisory — R10/E5 finding).
    runner = DafnyRunner()
    ok, detail = runner.verify_dafny(
        emitted, timeout=120, verify_limit=verify_limit,
        extract_counterexample=True, allow_warnings=_has_tested_tier(elab),
    )
    report["verified"] = bool(ok)

    # 3. diagnose: raw verifier output -> structured triples + tau advisory
    triples = elab.diagnose(detail, emitted) if not ok else []
    report["triples"] = [t.to_dict() for t in triples]
    report["tightness"] = elab.tightness or {}
    report["exit"] = 0 if ok else 1
    report["ok"] = bool(ok)
    return report


def _add_user_path_entry(entry: str, platform: str | None = None) -> None:
    """Append `entry` to the user PATH, idempotently.

    Windows: reads/appends HKCU\\Environment\\Path via winreg (no setx
    1024-char truncation trap; the %USERPROFILE%-expanded form is stored, not
    the raw literal). POSIX: appends `export PATH=...` to ~/.bashrc /
    ~/.zshrc when the entry is absent (creating ~/.bashrc if neither rc file
    exists, so the command is actually reachable). Never duplicates; never
    touches the system PATH. Raises OSError on failure (caller reports it as a
    non-fatal setup problem).
    """
    plat = platform or sys.platform
    if plat == "win32":
        import winreg

        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                             winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE)
        try:
            current, _typ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current = ""
        parts = [p for p in current.split(";") if p]
        if entry not in parts:
            parts.append(entry)
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
        winreg.CloseKey(key)
        return
    # POSIX: append an export line to the first existing rc file that lacks it.
    # If neither rc exists, create ~/.bashrc so the command is reachable at all.
    line = f'\nexport PATH="{entry}:$PATH"  # ward global command\n'
    for rc in (Path.home() / ".bashrc", Path.home() / ".zshrc"):
        if rc.is_file():
            text = rc.read_text(encoding="utf-8", errors="replace")
            if entry not in text:
                with rc.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            return
    (Path.home() / ".bashrc").write_text(line, encoding="utf-8")


def _install_global_command(repo: Path, home: Path, dry_run: bool) -> tuple[bool, str]:
    """Install bin/ward (or bin/ward.cmd) into ~/.ward/bin and add it to PATH.

    Returns (ok, message). Idempotent: re-running setup refreshes the launcher
    and the PATH entry is de-duplicated. The launcher itself resolves the
    checkout at runtime ($WARD_HOME > its own repo > ~/WARD), so moving the
    repo doesn't break the command.
    """
    import shutil

    launcher = repo / "bin" / ("ward.cmd" if sys.platform == "win32" else "ward")
    if not launcher.is_file():
        return False, f"launcher source missing: {launcher}"
    bin_dir = home / ".ward" / "bin"
    dst = bin_dir / launcher.name
    if dry_run:
        return True, f"(dry-run) would install {dst} + add {bin_dir} to PATH"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(launcher, dst)
    if sys.platform != "win32":
        # copy2 preserves the SOURCE file's mode — the repo copy may have no
        # exec bit (git on Windows doesn't track it), so force 755 so the
        # installed launcher is directly executable.
        import os

        os.chmod(dst, 0o755)
    # Record which checkout setup ran from, so the installed launcher prefers
    # THIS repo over a stale ~/WARD clone left by the one-line installer.
    (home / ".ward" / "repo.txt").write_text(str(repo), encoding="utf-8")
    _add_user_path_entry(str(bin_dir))
    return True, f"global `ward` command -> {dst}"


def _merge_ward_hook(settings_path: Path, command: str) -> None:
    """Idempotently merge the WARD auto-verify hook into ~/.claude/settings.json.

    Only the hooks.PreToolUse / hooks.PostToolUse arrays are touched: any
    existing entry whose command mentions ward_hook.py is replaced; everything
    else in the file is preserved. Raises OSError/ValueError on unreadable or
    invalid JSON (including parseable-but-malformed shapes like a bare list or
    `hooks: []`) — the caller reports it and leaves the file untouched.
    """
    data: dict = {}
    if settings_path.is_file():
        raw = settings_path.read_text(encoding="utf-8")
        if raw.strip():
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                raise ValueError("settings.json is not a JSON object — left untouched")
            data = loaded
    hooks = data.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings.json 'hooks' is not an object — left untouched")
    entry = {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [{"type": "command", "command": command}],
    }
    for event in ("PreToolUse", "PostToolUse"):
        lst = hooks.setdefault(event, [])
        if not isinstance(lst, list):
            raise ValueError(f"settings.json hooks.{event} is not a list — left untouched")
        lst[:] = [e for e in lst if "ward_hook.py" not in str(e)]
        lst.append(entry)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _cmd_setup(args) -> int:
    """One-command setup — activate WARD in your AI tools.

    Installs (idempotently, never clobbers unrelated files):
      - Claude Code skill        -> ~/.claude/skills/ward/   (global, any project)
      - Cursor rule              -> ~/.cursor/rules/ward.mdc (global, any project)
      - Claude Code auto-verify  -> ~/.claude/settings.json (global; fires only
        hook                      for .ward0 files — check as they're written)
    then checks the phase0 venv + Dafny/Z3 toolchain and prints a per-tool
    'you're ready' summary. Needs only the stdlib — no lark, no Dafny.

    The Claude-skill refresh only replaces a destination it owns (a dir whose
    SKILL.md mentions WARD); any other directory is left untouched.
    """
    import shutil
    import subprocess

    repo = Path(__file__).resolve().parent
    home = Path.home()
    problems: list[str] = []

    def status(ok: bool, msg: str) -> None:
        print(f"  {'✓' if ok else '✗'} {msg}")

    print("ward setup")
    print("  installing agent skills (global)…")

    # ---- Claude Code skill: repo .claude/skills/ward -> ~/.claude/skills/ward ----
    claude_src = repo / ".claude" / "skills" / "ward"
    claude_dst = home / ".claude" / "skills" / "ward"
    if args.dry_run:
        print(f"  (dry-run) would copy {claude_src} -> {claude_dst}")
    elif claude_src.is_dir():
        try:
            claude_dst.parent.mkdir(parents=True, exist_ok=True)
            if claude_dst.exists():
                # Never delete a directory we don't own: only refresh when the
                # existing SKILL.md is recognizably WARD's (a user-customized
                # skill dir is left untouched and reported).
                marker = claude_dst / "SKILL.md"
                own = marker.is_file() and "WARD" in marker.read_text(
                    encoding="utf-8", errors="replace")[:200]
                if own:
                    shutil.rmtree(claude_dst)  # refresh to the repo's skill
                else:
                    raise OSError(
                        "existing ~/.claude/skills/ward is not a WARD skill — "
                        "left untouched (remove it yourself to replace)")
            shutil.copytree(claude_src, claude_dst)
            status(True, f"Claude Code skill -> {claude_dst}")
        except OSError as exc:
            problems.append(f"claude skill install: {exc}")
            status(False, f"Claude Code skill ({exc})")
    else:
        problems.append(f"skill source missing: {claude_src}")
        status(False, f"Claude Code skill (source missing: {claude_src})")

    # ---- Cursor rule: repo .cursor/rules/ward.mdc -> ~/.cursor/rules/ward.mdc ----
    cursor_src = repo / ".cursor" / "rules" / "ward.mdc"
    cursor_dst = home / ".cursor" / "rules" / "ward.mdc"
    if args.dry_run:
        print(f"  (dry-run) would copy {cursor_src} -> {cursor_dst}")
    elif cursor_src.is_file():
        try:
            cursor_dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cursor_src, cursor_dst)
            status(True, f"Cursor rule -> {cursor_dst}")
        except OSError as exc:
            problems.append(f"cursor rule install: {exc}")
            status(False, f"Cursor rule ({exc})")
    else:
        problems.append(f"cursor rule source missing: {cursor_src}")
        status(False, f"Cursor rule (source missing: {cursor_src})")

    # ---- venv check (optional creation with --create-venv) ----
    if sys.platform == "win32":
        venv_py = repo / "phase0" / ".venv" / "Scripts" / "python.exe"
        venv_hint = ("python -m venv phase0/.venv && "
                     "phase0/.venv/Scripts/python -m pip install lark z3-solver")
    else:
        venv_py = repo / "phase0" / ".venv" / "bin" / "python"
        venv_hint = ("python3 -m venv phase0/.venv && "
                     "phase0/.venv/bin/python -m pip install lark z3-solver")
    venv_ok = venv_py.exists()
    venv_reported = False  # avoid double-reporting one failure
    if args.create_venv and not venv_ok:
        print(f"  creating {venv_py.parent}…")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_py.parent)], check=True)
            # The Microsoft Store python app-execution alias can "succeed" at
            # creating a venv that has pip.exe but no python.exe (it cannot
            # copy its own reparse-point binary). Catch that specific case so
            # the user gets an actionable message instead of a WinError 2.
            if not venv_py.exists():
                raise OSError(
                    "venv was created but has no python.exe — the Microsoft Store "
                    "python app-execution alias can produce a broken venv; install "
                    "Python from python.org (or set PYTHON_BIN), then re-run")
            subprocess.run([str(venv_py), "-m", "pip", "install", "--quiet", "lark"], check=True)
            try:
                # z3-solver powers the standalone z3 backend (E10). Non-fatal:
                # check/proof run through Dafny's own Z3 without it.
                subprocess.run([str(venv_py), "-m", "pip", "install", "--quiet", "z3-solver"], check=True)
            except subprocess.CalledProcessError as exc:
                print(f"  note: z3-solver install failed ({exc}) — the standalone z3 "
                      "backend (E10) is unavailable; check/proof still work.")
            venv_ok = True
        except (OSError, subprocess.CalledProcessError) as exc:
            # pip needs network; a failed install must be a clean problem
            # report, never a raw traceback
            venv_reported = True
            problems.append(f"venv create/install failed: {exc}")
            status(False, f"venv create/install ({exc})")
    if venv_ok:
        status(True, f"venv python: {venv_py}")
    elif not venv_reported:
        problems.append("venv missing (run: " + venv_hint + ")")
        status(False, f"venv python (run: {venv_hint})")

    # ---- Claude Code auto-verify hook: ~/.claude/settings.json ----
    # PreToolUse/PostToolUse hooks on Write|Edit|MultiEdit that fire ONLY for
    # .ward0 files (ward_hook.py self-guards on the extension): nudge the model
    # to state the contract first, then auto-run `ward.py check --json` after
    # every write and inject the result back — verification as the code is
    # being written, not after. Idempotent merge; unrelated settings preserved.
    hook_src = repo / "ward_hook.py"
    settings_path = home / ".claude" / "settings.json"
    if args.dry_run:
        print(f"  (dry-run) would add WARD auto-verify hook to {settings_path}")
    elif hook_src.is_file():
        try:
            interpreter = venv_py if venv_py.exists() else sys.executable
            command = f'"{interpreter}" "{hook_src}"'
            _merge_ward_hook(settings_path, command)
            status(True, f"Claude Code auto-verify hook -> {settings_path}")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            problems.append(f"claude hook install: {exc}")
            status(False, f"Claude Code auto-verify hook ({exc})")
    else:
        problems.append(f"hook source missing: {hook_src}")
        status(False, "Claude Code auto-verify hook (source missing: ward_hook.py)")

    # ---- global `ward` command: ~/.ward/bin + PATH ----
    # The launcher makes the CLI usable from any terminal / any project dir,
    # so `ward setup` and `ward check` work without cd-ing into a checkout.
    if args.dry_run:
        ok, msg = _install_global_command(repo, home, dry_run=True)
        status(ok, msg)
    else:
        try:
            ok, msg = _install_global_command(repo, home, dry_run=False)
            status(ok, msg)
            if not ok:
                problems.append(f"global command: {msg}")
        except OSError as exc:
            problems.append(f"global command install: {exc}")
            status(False, f"global `ward` command ({exc})")

    # ---- toolchain check ----
    dafny = shutil.which("dafny")
    z3 = shutil.which("z3")
    status(bool(dafny), f"dafny on PATH{' -> ' + dafny if dafny else ' (needed for live verification)'}")
    status(bool(z3), f"z3 on PATH{' -> ' + z3 if z3 else ' (needed for live verification)'}")
    if not (dafny and z3):
        problems.append("dafny/z3 not both on PATH — live verification needs both")

    # ---- summary ----
    print()
    if args.dry_run:
        print("dry run — nothing written.")
        return 0
    if problems:
        print("WARD skills installed; notes:")
        for p in problems:
            print(f"  - {p}")
        print("  Fix the above, then re-run `ward setup` (or `python ward.py setup`).")
        return 1
    print("✓ You're ready. From any terminal (your_file.ward0 = a real path):")
    print("    ward check your_file.ward0    the global command — works in any directory")
    print("    ward proof your_file.ward0    emit a .proof certificate")
    print("    ward setup                    re-run any time to refresh")
    print("  Note: `your_file.ward0` is a placeholder — substitute a real path, e.g.")
    print("   `ward check C:\\path\\to\\file.ward0`.")
    print("  In any AI tool:")
    print("    Claude Code    ask to 'verify this with ward' — the skill auto-loads.")
    print("    Cursor         the rule auto-applies to .ward0 files.")
    print("    OpenCode/Cline AGENTS.md in the repo teaches the agent automatically.")
    print("    VS Code        the LSP squiggles on save (see README 'Wire up'), or:")
    print(f"                     {venv_py} ward.py check your_file.ward0")
    return 0


def _cmd_doctor(args) -> int:
    """Diagnose the WARD install — one command to answer 'why is ward using
    that copy?' forever.

    Prints, with the same resolution order the launchers use
    ($WARD_HOME > ~/.ward/repo.txt > its own repo > ~/WARD):
      - which checkout the `ward` launcher resolves to, and whether it differs
        from this CLI's own repo (the stale-clone-shadowing case)
      - ~/.ward/repo.txt contents (the recorded install source)
      - venv python presence, dafny + z3 on PATH
      - global `ward` launcher, Claude Code skill + auto-verify hook, Cursor
        rule install state

    Exit 0 = all healthy; 1 = at least one problem found (reports them).
    """
    import os
    import shutil

    repo = Path(__file__).resolve().parent
    home = Path.home()
    problems: list[str] = []

    def status(ok: bool, msg: str) -> None:
        print(f"  {'✓' if ok else '✗'} {msg}")

    print("ward doctor")

    # ---- checkout resolution (same order as bin/ward / bin/ward.cmd) ----
    print("  checkout — what the `ward` launcher resolves to (same order as the launchers):")
    candidates: list[tuple[str, Path]] = []
    env_home = os.environ.get("WARD_HOME")
    if env_home:
        candidates.append(("$WARD_HOME", Path(env_home)))
    repo_txt = home / ".ward" / "repo.txt"
    if repo_txt.is_file():
        try:
            raw = repo_txt.read_text(encoding="utf-8").strip().splitlines()
            candidates.append(("~/.ward/repo.txt", Path(raw[0]) if raw else Path()))
        except OSError as exc:
            candidates.append((f"~/.ward/repo.txt (unreadable: {exc})", Path()))
    candidates.append(("own repo (dev layout — only when run from inside a checkout)", repo))
    candidates.append(("~/WARD", home / "WARD"))
    resolved: Path | None = None
    for label, p in candidates:
        ok = bool(p) and (p / "ward.py").is_file()
        status(ok, f"{label} -> {p}")
        if ok and resolved is None:
            resolved = p
    if resolved is None:
        print("  ✗ no usable checkout found — run `ward setup` or set $WARD_HOME")
        problems.append("no usable checkout found")
    elif resolved != repo:
        print(f"  ⚠ `ward` resolves to {resolved}, but this CLI lives in {repo}")
        print("    (stale clone shadowing the dev checkout? re-run `ward setup` from")
        print("    the checkout you want, or set $WARD_HOME to pin it.)")
        problems.append("launcher resolves to a different checkout than this CLI")
    else:
        print(f"  ✓ `ward` uses this checkout: {repo}")

    # ---- repo.txt ----
    print(f"  ~/.ward/repo.txt (recorded install source):")
    if repo_txt.is_file():
        try:
            content = repo_txt.read_text(encoding="utf-8").strip()
            target = Path(content) if content else Path()
            ok = bool(target) and (target / "ward.py").is_file()
            status(ok, content or "(empty)")
            if not ok:
                problems.append("repo.txt points at a checkout without ward.py (stale)")
        except OSError as exc:
            status(False, f"unreadable: {exc}")
            problems.append(f"repo.txt unreadable: {exc}")
    else:
        status(False, "(missing — run `ward setup` to record the install source)")
        problems.append("repo.txt missing")

    # ---- venv + toolchain ----
    if sys.platform == "win32":
        venv_py = repo / "phase0" / ".venv" / "Scripts" / "python.exe"
    else:
        venv_py = repo / "phase0" / ".venv" / "bin" / "python"
    status(venv_py.is_file(), f"venv python: {venv_py}")
    if not venv_py.is_file():
        problems.append("phase0 venv missing (run `ward setup --create-venv`)")
    dafny = shutil.which("dafny")
    z3 = shutil.which("z3")
    status(bool(dafny), "dafny on PATH" if dafny else "dafny on PATH (needed for live verification)")
    status(bool(z3), "z3 on PATH" if z3 else "z3 on PATH (needed for live verification)")
    if not (dafny and z3):
        problems.append("dafny/z3 not both on PATH — live verification needs both")

    # ---- global launcher ----
    launcher = home / ".ward" / "bin" / ("ward.cmd" if sys.platform == "win32" else "ward")
    status(launcher.is_file(), f"global `ward` launcher: {launcher}")
    if not launcher.is_file():
        problems.append("global `ward` launcher not installed (run `ward setup`)")

    # ---- agent integrations ----
    skill = home / ".claude" / "skills" / "ward" / "SKILL.md"
    status(skill.is_file(), f"Claude Code skill: {skill}")
    if not skill.is_file():
        problems.append("Claude Code skill missing (run `ward setup`)")
    settings = home / ".claude" / "settings.json"
    hook_ok = False
    if settings.is_file():
        try:
            data = json.loads(settings.read_text(encoding="utf-8"))
            hooks = data.get("hooks", {})
            hook_ok = any(
                "ward_hook.py" in str(h)
                for ev in ("PreToolUse", "PostToolUse")
                for e in hooks.get(ev, [])
                for h in e.get("hooks", [])
            )
        except (json.JSONDecodeError, AttributeError, TypeError):
            hook_ok = False
    status(hook_ok, f"Claude Code auto-verify hook in {settings}")
    if not hook_ok:
        problems.append("Claude Code auto-verify hook not installed (run `ward setup`)")
    cursor = home / ".cursor" / "rules" / "ward.mdc"
    status(cursor.is_file(), f"Cursor rule: {cursor}")
    if not cursor.is_file():
        problems.append("Cursor rule missing (run `ward setup`)")

    # ---- summary ----
    print()
    if problems:
        print("✗ issues found:")
        for p in problems:
            print(f"    - {p}")
        print("  Run `ward setup` (or `python ward.py setup`) from the checkout you want.")
        return 1
    print("✓ All healthy.")
    return 0


def _print_human(r: dict, path: str) -> None:
    print(f"ward check: {path}")
    if r.get("stage") == "elaborate":
        err = r["error"]
        print(f"  ✗ elaboration failed: {err}")
        if "trust annotation is mandatory" in err:
            print("  hint: every `extern fn` needs a `trust: \"...\"` line (T3).")
        elif "undefined callee" in err or "undefined name" in err:
            print("  hint: the fn calls a name that is not declared — check externs/params.")
        else:
            print("  hint: contracts are annotations, not statements — they take no")
            print("  trailing semicolon; the `{` follows the last requires/ensures directly.")
        return
    status = "✓ PROVED" if r["ok"] else "✗ NOT PROVED"
    print(f"  dafny verify: {status}")
    if r["ok"]:
        tau = r.get("tightness") or {}
        for fn, t in sorted(tau.items()):
            if t.get("action") == "demote":
                print(
                    f"  ⚠ {fn}: Proven but spec tightness tau={t.get('tau')} < TAU0 — "
                    "the contract is too weak to justify a proof. "
                    "Tighten the ensures clause(s) (anti-slop advisory)."
                )
        return
    for t in r.get("triples", []):
        print(f"  {t['kind']:14} {t['location']['surface']}")
        print(f"      {t['violated_obligation']}")
        if t.get("counterexample"):
            print(f"      counterexample: {t['counterexample']}")
        if t.get("tightness_advisory"):
            print(f"      {t['tightness_advisory']}")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252, which cannot encode ✓/✗/⚠. Reconfigure
    # to UTF-8 with replacement so the CLI never crashes on any terminal.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="ward",
        description="WARD — a verification language that proves AI-written code isn't slop.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="elaborate + prove + diagnose a .ward0 file")
    c.add_argument("input", help="path to a .ward0 file")
    c.add_argument("--enforce", action="store_true", help="generate extern contract-check wrappers")
    c.add_argument("--emit", action="store_true", help="also print the emitted Dafny")
    c.add_argument("--verify-limit", type=int, default=None, help="per-verification time limit (s)")
    c.add_argument("--json", action="store_true", help="machine-readable output")

    s = sub.add_parser("setup", help="install the Claude Code skill + Cursor rule globally; check venv + toolchain")
    s.add_argument("--dry-run", action="store_true", help="show what would be done, without writing")
    s.add_argument("--create-venv", action="store_true", help="create phase0/.venv + install lark if missing")

    d = sub.add_parser("doctor", help="diagnose the install: which checkout `ward` resolves to, repo.txt, venv, toolchain, integrations")

    e = sub.add_parser("emit", help="transpile a .ward0 file to Dafny only")
    e.add_argument("input")
    e.add_argument("--enforce", action="store_true")
    e.add_argument("-o", "--output", default=None)

    p = sub.add_parser("proof", help="emit a ward-cert .proof certificate for a .ward0 file")
    p.add_argument("input")
    p.add_argument("--enforce", action="store_true")
    p.add_argument("-o", "--output", default=None)

    args = ap.parse_args(argv)

    # Clean error for a missing input file — never a raw FileNotFoundError
    # traceback (the old behavior dumped pathlib internals to the user).
    if args.cmd in ("check", "emit", "proof") and not Path(args.input).is_file():
        print(f"ward: no such file: {args.input}", file=sys.stderr)
        return 2

    if args.cmd == "setup":
        return _cmd_setup(args)

    if args.cmd == "doctor":
        return _cmd_doctor(args)

    if args.cmd == "check":
        r = _run_check(_load_source(Path(args.input)), args.enforce,
                       args.emit, args.verify_limit)
        if args.json:
            print(json.dumps(r, indent=2))
        else:
            _print_human(r, args.input)
        return r.get("exit", 2)

    if args.cmd == "emit":
        from harness.dafny_runner import DafnyRunner

        src = _load_source(Path(args.input))
        try:
            out = DafnyRunner().transpile(src, enforce=args.enforce)
        except Exception as exc:
            print(f"transpile error: {exc}", file=sys.stderr)
            return 2
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out, end="")
        return 0

    if args.cmd == "proof":
        # Reuse the certificate emitter (Phase-A probe). The certificate
        # binds the composed ward0 source + emitted Dafny + per-fn tier +
        # tau + trust manifest. `.proof` artifacts are checked standalone by
        # harness/cert_check.py (no Dafny/Z3).
        # Integrity first: the per-fn proof outcome must be the ACTUAL verify
        # result, never a hardcoded "verified" — a VALID certificate must mean
        # the code really proved (the certificate's whole reason to exist).
        from harness.certificate import (
            OUT_DIR,
            _dafny_counts,
            build_trust_manifest,
            detect_toolchain,
            emit_certificate,
            sha256_text,
        )
        from harness.dafny_runner import DafnyRunner
        from wardcore.tightness_pass import measure_source

        src = _load_source(Path(args.input))
        elab = _load_elaborator(args.enforce)
        try:
            dafny_src = elab.transpile(src)
        except Exception as exc:
            print(f"transpile error: {exc}", file=sys.stderr)
            return 2
        tiers = {f.name: f.tier.value for f in elab.module.fns} if elab.module else {}
        runner = DafnyRunner()
        # verify_s is MODULE-wide (the whole module verifies as one unit) and
        # stamped on every fn — matches probe_one's posture, not per-fn timing.
        t_verify = time.perf_counter()
        ok, detail = runner.verify_dafny(
            dafny_src, timeout=120, extract_counterexample=True,
            allow_warnings=_has_tested_tier(elab),
        )
        verify_s = time.perf_counter() - t_verify
        counts = _dafny_counts(detail)
        trust_manifest = build_trust_manifest(
            _task_descriptor(Path(args.input)) or {"externs": [
                {"name": e.name, "contract": e.trust} for e in (elab.module.externs if elab.module else ())
            ]},
            args.enforce,
        )
        cert = emit_certificate(
            module=Path(args.input).stem,
            source=src,
            dafny_src=dafny_src,
            tiers=tiers,
            outcomes={
                fn: {
                    "proof": "verified" if ok else "failed",
                    "verified": counts["verified"],
                    "errors": counts["errors"],
                    "verify_s": round(verify_s, 3),
                }
                for fn in tiers
            },
            trust_manifest=trust_manifest,
            toolchain=detect_toolchain(args.enforce, 30),
            tightness=measure_source(src, tiers),
        )
        out_path = Path(args.output) if args.output else OUT_DIR / f"{Path(args.input).stem}.proof"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(cert, indent=2), encoding="utf-8")
        print(f"proof written to {out_path} (verdict={cert['verdict']}, "
              f"source_sha256={sha256_text(src)[:12]}…)")
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
