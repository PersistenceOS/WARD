#!/usr/bin/env python3
"""ward — the WARD command-line tool.

One command to verify ward0 code from any terminal or AI agent
(Claude Code, Cursor, VS Code, OpenCode, plain CLI):

    python ward.py check path/to/file.ward0      # transpile + prove + diagnose
    python ward.py emit  path/to/file.ward0      # transpile to Dafny only
    python ward.py proof path/to/file.ward0      # emit a .proof certificate
    python ward.py setup                         # install the Claude Code skill +
                                                 # Cursor rule globally; check venv + toolchain

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


def _cmd_setup(args) -> int:
    """One-command setup — activate WARD in your AI tools.

    Installs (idempotently, never clobbers unrelated files):
      - Claude Code skill  -> ~/.claude/skills/ward/   (global, any project)
      - Cursor rule        -> ~/.cursor/rules/ward.mdc (global, any project)
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
                     "phase0/.venv/Scripts/python -m pip install lark")
    else:
        venv_py = repo / "phase0" / ".venv" / "bin" / "python"
        venv_hint = ("python3 -m venv phase0/.venv && "
                     "phase0/.venv/bin/python -m pip install lark")
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
        print("  Fix the above, then re-run `python ward.py setup`.")
        return 1
    print("✓ You're ready. In any AI tool:")
    print("    Claude Code    ask to 'verify this with ward' — the skill auto-loads.")
    print("    Cursor         the rule auto-applies to .ward0 files.")
    print("    OpenCode/Cline AGENTS.md in the repo teaches the agent automatically.")
    print("    VS Code        run the CLI from the terminal, or add the README task:")
    print(f"                     {venv_py} ward.py check <file>.ward0")
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

    e = sub.add_parser("emit", help="transpile a .ward0 file to Dafny only")
    e.add_argument("input")
    e.add_argument("--enforce", action="store_true")
    e.add_argument("-o", "--output", default=None)

    p = sub.add_parser("proof", help="emit a ward-cert .proof certificate for a .ward0 file")
    p.add_argument("input")
    p.add_argument("--enforce", action="store_true")
    p.add_argument("-o", "--output", default=None)

    args = ap.parse_args(argv)

    if args.cmd == "setup":
        return _cmd_setup(args)

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
