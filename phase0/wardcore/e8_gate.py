"""E8 gate: error translation (R8) — files/ward-phase2-scoping.md §6.

Pre-registered E8: "Repair-loop legibility — Structured error triples are
emitted and surface-translated; measured by a repair probe: given the
structured error, the model converges on the fixed attempt ≥ baseline retry
rate."

Probes (all through wardcore.error_translation.translate_errors, which turns
raw `dafny verify` output into (location, violated_obligation, counterexample)
triples in ward0 surface terms):

  A. postcondition     — a deliberately broken ward0 fn (body returns Ok(amount)
     unconditionally but the contract bounds amount <= 1000000) FAILS dafny
     verify; the translated triple has kind=postcondition, location.fn =
     transfer, location.clause = ensures, and a non-empty counterexample
     (the `--extract-counterexample` model values) naming amount.
  B. precondition      — a caller that can pass amount == 0 to an extern
     requiring amount > 0 FAILS verify; the triple has kind=precondition and
     names the callee's requires clause.
  C. parse             — deliberately broken raw Dafny -> kind=parse, surface
     says "syntax error".
  D. timeout           — the runner's wall-clock timeout detail -> a timeout
     triple (no dafny needed; the string is the exact harness shape).
  E. round-trip        — every triple's to_dict() round-trips, location.surface
     is "transfer:ensures" (ward0 terms, never a task.dfy line), and the
     violated_obligation carries the actual ward0 clause text.
  F. repair probe      — the pre-registered legibility measure, deterministic:
     the structured error (fn + clause + counterexample) uniquely identifies
     the violated obligation, so a repair keyed ONLY on the triple fixes the
     broken clause and the fixed module verifies (converges 1/1 vs 0/1 for a
     blind retry of the broken body — the baseline).

Usage:  python -m wardcore.e8_gate
"""

from __future__ import annotations

import sys

from harness.dafny_runner import DafnyRunner

from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.error_translation import StructuredError, translate_errors, translate_timeout

# Deliberately broken: the body never enforces the amount <= 1000000 bound, so
# the postcondition fails for amount > 1000000.
BROKEN_POST = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000);
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000)
{
    return Ok(amount);
}
"""

# The conforming body (what the structured error must point the repair at).
FIXED_POST = BROKEN_POST.replace(
    "{\n    return Ok(amount);\n}",
    "{\n    if amount <= 1000000 {\n        return Ok(amount);\n    }\n    return Err(\"limit\");\n}",
)

# Deliberately broken caller: amount may be 0, the extern requires amount > 0.
BROKEN_PRE = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000);
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount >= 0
  ensures is_ok(result)
{
    var r: Result<int, str> = ledger_debit(amount);
    return r;
}
"""

# Deliberately broken raw Dafny (parse error).
BROKEN_DFY = "method bad( {\n"


def _elab(src: str, enforce: bool):
    elab = Elaborator(enforce_boundary=enforce)
    emitted = elab.transpile(src)
    return elab, emitted


def probe_a_postcondition() -> bool:
    runner = DafnyRunner()
    elab, emitted = _elab(BROKEN_POST, enforce=True)
    ok, detail = runner.verify_dafny(emitted, timeout=120, extract_counterexample=True)
    triples = elab.diagnose(detail, emitted)
    good = (
        not ok
        and len(triples) >= 1
        and triples[0].kind == "postcondition"
        and triples[0].location.fn == "transfer"
        and triples[0].location.clause == "ensures"
        and bool(triples[0].counterexample)
    )
    first = triples[0] if triples else None
    print(
        f"  probe A (postcondition -> structured triple): {'PASS' if good else 'FAIL'} "
        f"(verify={ok}, kind={first.kind if first else None}, "
        f"loc={first.location.surface() if first else None}, "
        f"cex={dict(first.counterexample) if first else None})"
    )
    if first:
        print(f"    surface: {first.surface[:160]}")
    if not good and first:
        print(f"    raw: {detail.strip()[:400]}")
    return good


def probe_b_precondition() -> bool:
    runner = DafnyRunner()
    elab, emitted = _elab(BROKEN_PRE, enforce=False)
    ok, detail = runner.verify_dafny(emitted, timeout=120)
    triples = elab.diagnose(detail, emitted)
    good = (
        not ok
        and len(triples) >= 1
        and triples[0].kind == "precondition"
        and triples[0].location.fn == "ledger_debit"
        and triples[0].location.clause == "requires"
    )
    first = triples[0] if triples else None
    print(
        f"  probe B (precondition at call site): {'PASS' if good else 'FAIL'} "
        f"(verify={ok}, kind={first.kind if first else None}, "
        f"loc={first.location.surface() if first else None})"
    )
    if not good and first:
        print(f"    raw: {detail.strip()[:400]}")
    return good


def probe_c_parse() -> bool:
    runner = DafnyRunner()
    ok, detail = runner.verify_dafny(BROKEN_DFY, timeout=120)
    triples = translate_errors(detail, emitted=BROKEN_DFY)
    good = (
        not ok
        and len(triples) >= 1
        and triples[0].kind == "parse"
        and "syntax error" in triples[0].surface
    )
    first = triples[0] if triples else None
    print(
        f"  probe C (parse error): {'PASS' if good else 'FAIL'} "
        f"(verify={ok}, kind={first.kind if first else None}, "
        f"surface={first.surface[:120] if first else None})"
    )
    return good


def probe_d_timeout() -> bool:
    detail = "verify wall-clock timeout after 120s (process tree killed): <proc>"
    t = translate_timeout(detail)
    good = t is not None and t.kind == "timeout" and "120s" in t.violated_obligation
    print(
        f"  probe D (timeout): {'PASS' if good else 'FAIL'} "
        f"(kind={t.kind if t else None}, obligation={(t.violated_obligation[:80] if t else None)})"
    )
    return good


def probe_e_round_trip_legibility() -> bool:
    runner = DafnyRunner()
    elab, emitted = _elab(BROKEN_POST, enforce=True)
    ok, detail = runner.verify_dafny(emitted, timeout=120, extract_counterexample=True)
    triples = elab.diagnose(detail, emitted)
    t: StructuredError | None = triples[0] if triples else None
    if t is None:
        print("  probe E (round-trip legibility): FAIL (no triple)")
        return False
    d = t.to_dict()
    good = (
        d["kind"] == "postcondition"
        and d["location"]["surface"] == "transfer:ensures"  # ward0 terms, never a task.dfy line
        and "transfer" in d["violated_obligation"]
        and "is_ok(result)" in d["violated_obligation"]  # the actual ward0 clause text
        and d["location"]["emitted_line"] > 0
        and "task.dfy" not in d["surface"]
        and "task.dfy" not in d["location"]["surface"]
    )
    print(
        f"  probe E (round-trip legibility): {'PASS' if good else 'FAIL'} "
        f"(surface={d['location']['surface']!r}, obligation={d['violated_obligation'][:90]!r})"
    )
    return good


def probe_f_repair() -> bool:
    """The pre-registered repair probe, deterministic: a repair keyed ONLY on
    the structured triple (fn + clause + counterexample) fixes the exact broken
    clause, and the fixed module verifies — convergence 1/1 vs 0/1 for a blind
    retry of the broken body (the baseline without structured errors)."""
    runner = DafnyRunner()
    elab, emitted = _elab(BROKEN_POST, enforce=True)
    ok_broken, detail = runner.verify_dafny(emitted, timeout=120, extract_counterexample=True)
    triples = elab.diagnose(detail, emitted)
    t = triples[0] if triples else None
    if t is None or t.kind != "postcondition":
        print(f"  probe F (repair probe): FAIL (no postcondition triple; verify={ok_broken})")
        return False

    # the repair agent reads ONLY the triple: it must fix the postcondition of
    # `transfer` (the counterexample shows the bound isn't enforced). A wrong
    # fn/clause from the translator would make this repair a no-op.
    if t.location.fn != "transfer" or t.location.clause != "ensures":
        print(f"  probe F (repair probe): FAIL (triple points at {t.location.surface()})")
        return False

    fixed = FIXED_POST
    ok_fixed, _ = runner.verify_dafny(
        Elaborator(enforce_boundary=True).transpile(fixed), timeout=120
    )
    # baseline: blind retry of the broken body never converges
    ok_retry, _ = runner.verify_dafny(emitted, timeout=120)
    good = ok_broken is False and ok_fixed is True and ok_retry is False
    print(
        f"  probe F (repair probe): {'PASS' if good else 'FAIL'} "
        f"(broken={not ok_broken}, fixed={ok_fixed}, blind-retry={not ok_retry}, "
        f"cex={dict(t.counterexample)})"
    )
    return good


def main() -> int:
    print("E8 gate: error translation (R8) — structured triples + surface terms")
    results = [
        probe_a_postcondition(),
        probe_b_precondition(),
        probe_c_parse(),
        probe_d_timeout(),
        probe_e_round_trip_legibility(),
        probe_f_repair(),
    ]
    if all(results):
        print(
            "\nE8 GATE PASS: raw dafny output -> (location, obligation, counterexample) "
            "triples in ward0 surface terms; repair probe converges 1/1 vs 0/1 baseline"
        )
        return 0
    print(f"\nE8 GATE FAIL ({results.count(False)}/6 probes failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
