"""E4b gate: dependency pinning (files/ward-phase2-scoping.md §6).

Pre-registered E4b: "A dependency reference outside its declared version range
(or unresolvable) fails elaboration; in-range reference passes. Oracle scenario
covers a version-drift probe."

Probes (all through the ward-core elaborator + dep pass):
  A. version drift — module pins `ledger@^2.0.0`, extern references
     `ledger@3.1.0` -> ElaborationError "outside the pinned range ^2.0.0"
  B. unresolved    — extern references `payments@1.0.0`, no range declared
     -> ElaborationError "unresolved"
  C. ambiguous     — two declared ranges for the same name (^2.0.0 and ^1.0.0)
     -> ElaborationError "ambiguous"
  D. correct code  — in-range references elaborate AND the emitted Dafny
     `dafny verify`s clean (dependency resolution is elaboration-time; it
     never changes the backend)
  E. plan exposure — a multi-dependency module's resolution plan is exposed
     on the elaborator with all records resolved

Usage:  python -m wardcore.e4b_gate
"""

from __future__ import annotations

import sys

from harness.dafny_runner import DafnyRunner

from wardcore.dep_pass import DepPass
from wardcore.elaborator import Elaborator, ElaborationError

# Module header: two pinned ranges. Extern refs are attached to their externs.
HEADER = """\
dep: ledger@^2.0.0
dep: auth@~1.2.0
"""

EXTERNS = """\
extern fn ledger_debit(balance: int, amount: int) -> Result<int, str>
  requires balance >= 0
  requires amount >= 0
  ensures is_ok(result) == (amount <= 100);
dep: ledger@2.4.1
trust: "oracle reference stub"

extern fn auth_check(user_id: int) -> Result<Unit, str>
  requires user_id > 0
  ensures is_ok(result) == (user_id < 1000);
dep: auth@1.2.0
trust: "oracle reference stub"
"""

CALLER = """\
fn pay(user_id: int, amount: int) -> Result<Unit, str>
  requires user_id > 0
  requires amount > 0
  ensures is_ok(result) == (user_id < 1000 and amount <= 100)
{
    var a: Result<Unit, str> = auth_check(user_id);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    var b: Result<int, str> = ledger_debit(1000, amount);
    if is_err(b) {
        return Err(unwrap_err(b));
    }
    return Ok(unwrap_ok(a));
}
"""

SRC = HEADER + EXTERNS + CALLER


def probe_a_version_drift() -> bool:
    # declared ^2.0.0, referenced 3.1.0 -> out of range = hard error
    src = SRC.replace("dep: ledger@2.4.1", "dep: ledger@3.1.0")
    try:
        Elaborator().transpile(src)
    except ElaborationError as exc:
        ok = "outside the pinned range ^2.0.0" in str(exc)
        print(f"  probe A (out-of-range = version drift): {'PASS' if ok else 'FAIL'} -> {str(exc)[:80]}")
        return ok
    print("  probe A (out-of-range = version drift): FAIL (no error raised)")
    return False


def probe_b_unresolved() -> bool:
    src = SRC.replace("dep: ledger@2.4.1", "dep: payments@1.0.0")
    try:
        Elaborator().transpile(src)
    except ElaborationError as exc:
        ok = "unresolved" in str(exc) and "payments" in str(exc)
        print(f"  probe B (undeclared dep = unresolved): {'PASS' if ok else 'FAIL'} -> {str(exc)[:80]}")
        return ok
    print("  probe B (undeclared dep = unresolved): FAIL (no error raised)")
    return False


def probe_c_ambiguous() -> bool:
    # a SECOND range for `ledger` in the module header -> resolution cannot
    # pick one. It must go in the header (before the first def) — appended
    # after the last def it would read as an extern reference instead.
    src = SRC.replace(
        "dep: ledger@^2.0.0", "dep: ledger@^2.0.0\ndep: ledger@^1.0.0"
    )
    try:
        Elaborator().transpile(src)
    except ElaborationError as exc:
        ok = "ambiguous" in str(exc)
        print(f"  probe C (two ranges = ambiguous): {'PASS' if ok else 'FAIL'} -> {str(exc)[:80]}")
        return ok
    print("  probe C (two ranges = ambiguous): FAIL (no error raised)")
    return False


def probe_d_correct_code() -> bool:
    elab = Elaborator(enforce_boundary=True)
    try:
        emitted = elab.transpile(SRC)
    except ElaborationError as exc:
        print(f"  probe D (in-range passes): FAIL (elaboration error) -> {str(exc)[:120]}")
        return False
    runner = DafnyRunner()
    ok, detail = runner.verify_dafny(emitted, timeout=120)
    good = ok and elab.dep_resolution.all_resolved
    print(f"  probe D (in-range passes + verifies): {'PASS' if good else 'FAIL'} (elaborated, dafny verify={ok}, all resolved={elab.dep_resolution.all_resolved})")
    if not ok:
        print(f"    {detail.strip()[:300]}")
    return good


def probe_e_plan_exposure() -> bool:
    module = Elaborator().desugar(SRC)
    resolution = DepPass().resolve(module)
    ok = (
        resolution.all_resolved
        and resolution.for_extern("ledger_debit").status == "resolved"
        and resolution.for_extern("ledger_debit").dep == "ledger"
        and resolution.for_extern("ledger_debit").version == "2.4.1"
        and resolution.for_extern("auth_check").ranges == ("~1.2.0",)
    )
    print(
        "  probe E (resolution plan per extern): "
        f"{'PASS' if ok else 'FAIL'} (ledger@2.4.1->^2.0.0, auth@1.2.0->~1.2.0)"
    )
    return ok


def main() -> int:
    print("E4b gate: dependency pinning")
    results = [
        probe_a_version_drift(),
        probe_b_unresolved(),
        probe_c_ambiguous(),
        probe_d_correct_code(),
        probe_e_plan_exposure(),
    ]
    if all(results):
        print("\nE4b GATE PASS: out-of-range/unresolved/ambiguous fail, in-range passes + verifies, plan exposed")
        return 0
    print(f"\nE4b GATE FAIL ({results.count(False)}/5 probes failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
