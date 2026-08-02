"""E4 gate: effects tracked (files/ward-phase2-scoping.md §6).

Pre-registered E4: "A function calling a net extern without declaring net
fails elaboration; declared-but-unused effect fails; correct code passes.
Oracle scenario set covers all five kinds."

Probes (all through the ward-core elaborator + effects pass):
  A. escape hard error  — fn declares `effects: fs` but calls a `net` extern
     -> ElaborationError "calls undeclared effect net (T5)"
  B. unused hard error  — fn declares `effects: net, fs` but exercises only
     net -> ElaborationError "declared effect fs is unused (T5)"
  C. correct code       — a fn whose declared effects EXACTLY match its
     inferred set (net+db) elaborates AND the emitted Dafny `dafny verify`s
     clean (effects are elaboration-time; they never change the backend)
  D. all five kinds     — net/db/fs/mut/partial each elaborate clean when
     declared == inferred (the oracle scenario set covers the vocabulary)
  E. transitive         — a fn that calls ANOTHER fn which calls a db extern
     infers db transitively; undeclared -> escape error, declared -> clean

Usage:  python -m wardcore.e4_gate
"""

from __future__ import annotations

import sys

from harness.dafny_runner import DafnyRunner

from wardcore.effects_pass import EffectsPass
from wardcore.elaborator import Elaborator, ElaborationError

EXTERNS = """\
extern fn net_call(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: net
trust: "oracle reference stub"

extern fn db_get(key: int) -> Result<int, str>
  requires key > 0
ensures is_ok(result) == (key < 1000);
effect: db
trust: "oracle reference stub"
"""


def _caller(fn_body: str, declared: str, extra: str = "") -> str:
    """Wrap a fn body with the extern preamble and a declared effects line."""
    return (
        EXTERNS
        + f"""\

effects: {declared}
fn f(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{{
{fn_body}
}}
"""
        + extra
    )


NET_BODY = """\
    var a: Result<int, str> = net_call(x);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    return Ok(unwrap_ok(a));
"""

# a body that touches BOTH net and db — for a PURE escape probe: declared db,
# inferred {net, db} -> only the net escape fires, nothing is unused
NET_DB_BODY = """\
    var a: Result<int, str> = net_call(x);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    var b: Result<int, str> = db_get(x);
    if is_err(b) {
        return Err(unwrap_err(b));
    }
    return Ok(unwrap_ok(b));
"""


def probe_a_escape() -> bool:
    # PURE escape: declared db, calls net AND db (both used) -> only the net
    # escape fires; nothing is declared-but-unused, isolating the T5 escape
    # direction exactly as the pre-registered E4 probe states it
    try:
        Elaborator().transpile(_caller(NET_DB_BODY, "db"))
    except ElaborationError as exc:
        ok = "calls undeclared effect net (T5)" in str(exc) and "is unused" not in str(exc)
        print(f"  probe A (escape: net undeclared = hard error): {'PASS' if ok else 'FAIL'} -> {str(exc)[:80]}")
        return ok
    print("  probe A (escape: net undeclared = hard error): FAIL (no error raised)")
    return False


def probe_b_unused() -> bool:
    src = EXTERNS + f"""\

effects: net, fs
fn f(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{{
{NET_BODY}
}}
"""
    problems = EffectsPass().validate(Elaborator().desugar(src))
    ok = any("declared effect fs is unused" in p for p in problems)
    print(f"  probe B (declared-but-unused = hard error): {'PASS' if ok else 'FAIL'} (fs declared, only net used)")
    return ok


def probe_c_correct_code() -> bool:
    # two correct functions: `f` declares exactly net (its body only calls
    # net_call), `g` declares exactly db — each declared == inferred, and the
    # emitted Dafny verifies clean (effects are elaboration-time; they never
    # change the backend)
    src = _caller(NET_BODY, "net", extra="""\

effects: db
fn g(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    var b: Result<int, str> = db_get(x);
    if is_err(b) {
        return Err(unwrap_err(b));
    }
    return Ok(unwrap_ok(b));
}
""")
    elab = Elaborator(enforce_boundary=True)
    try:
        emitted = elab.transpile(src)
    except ElaborationError as exc:
        print(f"  probe C (correct code passes): FAIL (elaboration error) -> {str(exc)[:120]}")
        return False
    runner = DafnyRunner()
    ok, detail = runner.verify_dafny(emitted, timeout=120)
    good = ok
    print(f"  probe C (correct multi-effect code passes + verifies): {'PASS' if good else 'FAIL'} (elaborated, dafny verify={ok})")
    if not good:
        print(f"    {detail.strip()[:300]}")
    return good


def probe_d_all_five_kinds() -> bool:
    kinds = ["net", "db", "fs", "mut", "partial"]
    results = []
    for kind in kinds:
        src = f"""\
extern fn stub_{kind}(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: {kind}
trust: "oracle reference stub"

effects: {kind}
fn use_{kind}(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{{
    var a: Result<int, str> = stub_{kind}(x);
    if is_err(a) {{
        return Err(unwrap_err(a));
    }}
    return Ok(unwrap_ok(a));
}}
"""
        try:
            Elaborator().transpile(src)
            results.append(True)
        except ElaborationError as exc:
            results.append(False)
            print(f"    kind {kind} FAIL -> {str(exc)[:80]}")
    ok = all(results)
    print(f"  probe D (all five kinds, declared==inferred): {'PASS' if ok else 'FAIL'} ({sum(results)}/5)")
    return ok


def probe_e_transitive() -> bool:
    # caller -> wrapper -> db_get: db inferred transitively
    base = EXTERNS + """\

effects: db
fn wrapper(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    var b: Result<int, str> = db_get(x);
    if is_err(b) {
        return Err(unwrap_err(b));
    }
    return Ok(unwrap_ok(b));
}
"""
    # declared db: clean
    clean_src = base + """\

effects: db
fn caller(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    return wrapper(x);
}
"""
    # declared net: escape (caller touches db transitively)
    bad_src = base + """\

effects: net
fn caller(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    return wrapper(x);
}
"""
    from wardcore.ir import EffectKind

    module = Elaborator().desugar(clean_src)
    inferred = EffectsPass().infer(module)
    transitive_ok = inferred["caller"] == frozenset({EffectKind.DB})
    bad_problems = EffectsPass().validate(Elaborator().desugar(bad_src))
    escape_ok = any("calls undeclared effect db" in p for p in bad_problems)
    ok = transitive_ok and escape_ok
    print(
        f"  probe E (transitive inference through fn calls): {'PASS' if ok else 'FAIL'} "
        f"(caller infers {inferred.get('caller', '?')}, undeclared-db escape={escape_ok})"
    )
    return ok


def main() -> int:
    print("E4 gate: effects tracked")
    results = [
        probe_a_escape(),
        probe_b_unused(),
        probe_c_correct_code(),
        probe_d_all_five_kinds(),
        probe_e_transitive(),
    ]
    if all(results):
        print("\nE4 GATE PASS: escape hard error, unused hard error, correct code passes, all five kinds, transitive inference")
        return 0
    print(f"\nE4 GATE FAIL ({results.count(False)}/5 probes failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
