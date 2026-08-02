"""E5 gate: per-function tiers inside modules (files/ward-phase2-scoping.md §6).

Pre-registered E5: "A module with one Proven fn + one Tested fn routes per
function: Tested fn never blocks on proof, Proven fn verifies, effort metered
per function."

Probes (all through the ward-core elaborator's emitted Dafny):
  A. routing table     — the multi-tier module's IR tiers produce the T6 plan
                         (Proven -> VERIFY_FULL, Contracted -> VERIFY_BOUNDED
                         with 30 s budget + fallback, Tested -> NO_PROOF)
  B. Tested never      — the Tested fn carries `{:verify false}` (its tier's
     blocks on proof     declared semantics, R10-note: NOT a wrapper hack; the
                         `_checked` wrapper stays verified) so the module's
                         `dafny verify` passes even though the Tested fn's
                         contract is deliberately unprovable; a control module
                         with the same fn but NO tier annotation fails verify
                         (proving the tier routing is what unblocks it)
  C. effort metered    — per-function solver seconds via `dafny verify
     per function        --filter-symbol=<fn>` wall-clock into an EffortMeter
                         (R7): Proven actual_s > 0, Tested actual_s == 0
                         (never run), verify_s floats in the Phase-0/1 schema
  D. cross-tier        — a Proven fn calling a Tested fn is a hard
     rule                ElaborationError (T6: no un-checked path into a
                         proof-carrying function)

Usage:  python -m wardcore.e5_gate
"""

from __future__ import annotations

import sys
import time

from harness.dafny_runner import DafnyRunner

from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.ir import Tier
from wardcore.tier_pass import CONTRACTED_VERIFY_LIMIT_S, EffortMeter, TierPass, VerifyRoute

# A multi-function module (R6): Proven entry fn + Contracted helper + Tested
# helper. The Tested fn's contract is DELIBERATELY UNPROVABLE (returns
# x + 2 but promises x + 1) — if verification ever touched it, the module
# would fail; only `{:verify false}` (the Tested tier's semantics) keeps it
# from blocking the module's proof. That is exactly the E5 claim.
MULTI_MODULE = """\
extern fn db_get(key: int) -> Result<int, str>
  requires key > 0
ensures is_ok(result) == (key < 1000);
trust: "oracle reference stub"

tier: Proven
fn entry(user_id: int) -> Result<int, str>
  requires user_id > 0
  ensures is_ok(result) == (user_id < 1000)
{
    var v: Result<int, str> = db_get(user_id);
    if is_ok(v) != (user_id < 1000) {
        return Err("contract violation");
    }
    if is_err(v) {
        return Err(unwrap_err(v));
    }
    return Ok(unwrap_ok(v));
}

tier: Contracted
fn bounded(x: int) -> int
  requires x > 0
  ensures result > 0
{
    return x;
}

tier: Tested
fn tested(x: int) -> int
  ensures result == x + 1
{
    return x + 2;
}
"""

# Control: same functions, NO tier annotations -> every fn is Proven and the
# unprovable Tested contract now blocks `dafny verify`.
CONTROL_NO_TIERS = MULTI_MODULE.replace("tier: Proven\n", "").replace("tier: Contracted\n", "").replace("tier: Tested\n", "")


def probe_a_routing_table() -> bool:
    module = Elaborator().desugar(MULTI_MODULE)
    plan = TierPass().plan(module)
    checks = []
    o_entry = plan.for_fn("entry")
    checks.append(o_entry.route is VerifyRoute.VERIFY_FULL)
    checks.append(o_entry.verify_limit_s is None)
    o_b = plan.for_fn("bounded")
    checks.append(o_b.route is VerifyRoute.VERIFY_BOUNDED)
    checks.append(o_b.verify_limit_s == CONTRACTED_VERIFY_LIMIT_S and o_b.fallback_allowed)
    o_t = plan.for_fn("tested")
    checks.append(o_t.route is VerifyRoute.NO_PROOF)
    checks.append(o_t.verify_limit_s is None and not o_t.fallback_allowed)
    ok = all(checks)
    print(
        f"  probe A (T6 routing table): {'PASS' if ok else 'FAIL'} "
        f"(Proven=full, Contracted={CONTRACTED_VERIFY_LIMIT_S}s+fallback, Tested=no_proof)"
    )
    return ok


def probe_b_tested_never_blocks() -> bool:
    runner = DafnyRunner()
    emitted = Elaborator(enforce_boundary=True).transpile(MULTI_MODULE)
    has_attr = "method {:verify false} tested(" in emitted
    # allow_warnings: the Tested fn's `{:verify false}` (its tier's declared
    # semantics, T6) triggers Dafny's development-only advisory warning; proof
    # obligations verify clean (3 verified) but the module is rejected without
    # the flag (R10 friction). The `_checked` wrapper stays fully verified.
    ok, detail = runner.verify_dafny(emitted, timeout=120, allow_warnings=True)
    # control: without tier annotations the unprovable contract must FAIL
    # (no `{:verify false}`, no warnings — a real postcondition error)
    ctrl = Elaborator(enforce_boundary=True).transpile(CONTROL_NO_TIERS)
    ctrl_ok, _ = runner.verify_dafny(ctrl, timeout=120)
    good = has_attr and ok and not ctrl_ok
    print(
        f"  probe B (Tested never blocks on proof): {'PASS' if good else 'FAIL'} "
        f"(module verifies={ok}, Tested has {{:verify false}}={has_attr}, no-tier control fails={not ctrl_ok})"
    )
    if not good:
        print(f"    module verify detail: {detail.strip()[:300]}")
    return good


def probe_c_effort_metered_per_function() -> bool:
    runner = DafnyRunner()
    emitted = Elaborator(enforce_boundary=True).transpile(MULTI_MODULE)
    plan = TierPass().plan(Elaborator().desugar(MULTI_MODULE))
    meter = EffortMeter(plan)
    # measure solver seconds per proof-carrying fn via --filter-symbol
    # (allow_warnings: the file also carries the Tested fn's `{:verify false}`)
    for fn, limit in (("entry", None), ("bounded", CONTRACTED_VERIFY_LIMIT_S)):
        t0 = time.monotonic()
        ok, _ = runner.verify_dafny(emitted, timeout=120, verify_limit=limit, filter_symbol=fn, allow_warnings=True)
        dt = time.monotonic() - t0
        meter.record(fn, dt, outcome="verified" if ok else "failed")
    report = {r["fn"]: r for r in meter.report()}
    # check RAW actual_s (not the 2-decimal-rounded dict) so a fast proof can't
    # spuriously round to 0.0, and require the outcome to be verified
    entry = meter.for_fn("entry")
    entry_ok = entry.outcome == "verified" and entry.actual_s > 0.0
    tested_zero = report["tested"]["verify_s"] == 0.0 and report["tested"]["outcome"] == "not_run"
    ok = entry_ok and tested_zero
    print(
        f"  probe C (effort metered per function): {'PASS' if ok else 'FAIL'} "
        f"(entry verify_s={report['entry']['verify_s']}s outcome={entry.outcome}, tested verify_s={report['tested']['verify_s']}s never run)"
    )
    return ok


def probe_d_cross_tier_rule() -> bool:
    bad = """\
tier: Proven
fn caller(x: int) -> int
  requires x > 0
  ensures result > 0
{
    var r: int = helper(x);
    return r;
}

tier: Tested
fn helper(x: int) -> int
  ensures result > 0
{
    return x;
}
"""
    try:
        Elaborator(enforce_boundary=True).transpile(bad)
    except ElaborationError as exc:
        ok = "must not depend on an unverified callee (T6)" in str(exc)
        print(f"  probe D (cross-tier rule): {'PASS' if ok else 'FAIL'} -> {str(exc)[:80]}")
        return ok
    print("  probe D (cross-tier rule): FAIL (no error raised)")
    return False


def main() -> int:
    print("E5 gate: per-function tiers inside modules")
    results = [
        probe_a_routing_table(),
        probe_b_tested_never_blocks(),
        probe_c_effort_metered_per_function(),
        probe_d_cross_tier_rule(),
    ]
    if all(results):
        print("\nE5 GATE PASS: Tested never blocks on proof, Proven verifies, effort metered per function, cross-tier rule enforced")
        return 0
    print(f"\nE5 GATE FAIL ({results.count(False)}/4 probes failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
