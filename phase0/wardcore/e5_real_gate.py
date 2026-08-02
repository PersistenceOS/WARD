"""E5-real gate: per-function tiers/effects/deps on real multi-function w-task
oracles, end-to-end through the typed pipeline (files/ward-phase2-scoping.md
§6 E5 row, weeks 5–6: "per-function tier routing end-to-end on new
multi-function w-task oracles", gates E4b + E5-real).

Oracles (benchmarks/w_tasks): w9-w12 — the first multi-function ward0 modules,
each carrying per-fn `tier:`/`effects:` annotations, per-extern
`effect:`/`dep:` references, and a module-level `dep:` header:

  w9_inventory_order   place_order Proven, check_stock Proven, reserve_inventory Contracted
  w10_session_ledger   session_transfer Contracted, auth_session Proven, ledger_move Proven
  w11_idempotent_retry retry_payment Tested, dedup_check Proven, charge_once Proven
  w12_hold_release     hold_and_release Proven, hold_funds Contracted, release_hold Proven

All three tier gates get exercised by the oracle set (Proven ×4, Contracted
×3, Tested ×1 — plus the entry fn covers all three roles).

Probes per task (all through the elaborator's emitted Dafny, enforce ON):
  A. compose+elaborate — deps header + externs (with effect:/dep:) + multi-fn
     ward0 reference elaborates clean (deps/effects/tiers are core passes)
  B. E4b               — dep_resolution.all_resolved: every extern's dependency
     reference is inside its pinned range
  C. E5                — the tier plan routes every fn exactly as declared
     (Proven -> VERIFY_FULL, Contracted -> VERIFY_BOUNDED+fallback, Tested ->
     NO_PROOF) AND the inferred effect set equals the declared set per fn
  D. verify            — the emitted module `dafny verify`s clean (Tested
     bearing modules pass --allow-warnings: the Tested fn's `{:verify false}`
     is the tier's declared semantics, R10 — never a wrapper hack)
  E. boundary          — hidden tests through the emitted Dafny: 0
     boundary_okleak on violation-flagged cases and every hidden test PASSes
     (the reference callers + generated `_checked` wrappers catch every
     contract violation; no case over-grants across the boundary)
  F. effort            — per-fn solver seconds via `dafny verify
     --filter-symbol=<fn>` wall-clock into EffortMeter (R7): proof-carrying
     fns record actual_s > 0 and outcome verified; the Tested fn is never run
     (verify_s 0.0, outcome not_run)

Evidence dump: experiments/runs/e5_real_gate.jsonl.

Usage:  python -m wardcore.e5_real_gate
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from harness.dafny_runner import DafnyRunner

from wardcore.effects_pass import parse_effect_set
from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.tier_pass import CONTRACTED_VERIFY_LIMIT_S, EffortMeter, VerifyRoute

PHASE0_DIR = Path(__file__).resolve().parent.parent
W_TASKS_DIR = PHASE0_DIR / "benchmarks" / "w_tasks"
RUNS_DIR = PHASE0_DIR / "experiments" / "runs"

W_TASK_IDS = [
    "w9_inventory_order",
    "w10_session_ledger",
    "w11_idempotent_retry",
    "w12_hold_release",
]

ROUTE_FOR_TIER = {
    "Proven": VerifyRoute.VERIFY_FULL,
    "Contracted": VerifyRoute.VERIFY_BOUNDED,
    "Tested": VerifyRoute.NO_PROOF,
}


def load_task(tid: str) -> dict:
    return json.loads((W_TASKS_DIR / f"{tid}.json").read_text(encoding="utf-8"))


def reference_src(tid: str) -> str:
    return (W_TASKS_DIR / f"{tid}.ward0").read_text(encoding="utf-8")


def compose_module(desc: dict, ward0_src: str) -> str:
    """Compose the full annotated ward0 module a model is asked to write.

    deps header (module ranges, before the first def) + each extern def with
    its `effect:` and `dep:` reference after the terminating `;` + the multi-fn
    ward0 source (which carries the per-fn `tier:`/`effects:` annotations).
    This is exactly the surface the elaborator must accept end-to-end.
    """
    header = "\n".join(f"dep: {d}" for d in desc.get("deps", []))
    parts = [header] if header else []
    for stub in desc["externs"]:
        params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
        sig = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
        if stub.get("contract"):
            sig += "\n  " + stub["contract"]
        # the `;` terminates the extern def FIRST; `trust:` (T3 mandatory), then
        # the week-5 `effect:`/`dep:` toolchain annotations on their own lines
        # so the desugar regexes strip+attach them in declaration order.
        block = sig + ';\ntrust: "oracle reference stub"'
        if stub.get("effect"):
            block += f"\neffect: {stub['effect']}"
        if stub.get("dep"):
            block += f"\ndep: {stub['dep']}"
        parts.append(block)
    parts.append(ward0_src.strip())
    return "\n\n".join(parts) + "\n"


def probe_a_b_e_compose_e4b_effects(tid: str) -> tuple[bool, list[str], Elaborator | None, str]:
    """Compose + elaborate; check E4b resolution and E5 effects/tier routing."""
    desc = load_task(tid)
    src = reference_src(tid)
    composed = compose_module(desc, src)
    notes: list[str] = []
    try:
        elab = Elaborator(enforce_boundary=True)
        emitted = elab.transpile(composed)
    except ElaborationError as exc:
        return False, [f"    elaboration error: {str(exc)[:400]}"], None, ""

    # ---- E4b: every extern dependency reference resolved in-range ----
    dep = elab.dep_resolution
    dep_ok = dep.all_resolved and len(dep.records) == len(
        [s for s in desc["externs"] if s.get("dep")]
    )
    if dep_ok:
        notes.append(
            f"    E4b deps resolved: {', '.join(r.dep + '@' + r.version for r in dep.records)}"
        )

    # ---- E5: per-fn tier routing matches the declared tiers ----
    plan = elab.tier_plan
    tier_ok = True
    for fn_name, tier in desc["tiers"].items():
        route = plan.for_fn(fn_name).route
        if route is not ROUTE_FOR_TIER[tier]:
            tier_ok = False
            notes.append(f"    tier mismatch {fn_name}: declared {tier}, routed {route}")
    if tier_ok:
        notes.append(
            "    tiers routed as declared: "
            + ", ".join(f"{n}={t}" for n, t in desc["tiers"].items())
        )

    # ---- E5: inferred effect set == declared set per fn ----
    inferred = elab.effects_inferred
    eff_ok = True
    for fn_name, declared in desc["effects"].items():
        want = parse_effect_set(", ".join(declared))
        got = inferred.get(fn_name, frozenset())
        if got != want:
            eff_ok = False
            notes.append(
                f"    effects mismatch {fn_name}: declared {{{', '.join(declared)}}}, "
                f"inferred {{{', '.join(sorted(e.value for e in got))}}}"
            )
    if eff_ok:
        notes.append("    effects declared == inferred for every fn")

    ok = dep_ok and tier_ok and eff_ok
    if not ok:
        notes.insert(0, f"    E4b resolved={dep.all_resolved}")
    return ok, notes, elab, emitted


def probe_d_verify(tid: str, elab: Elaborator, emitted: str) -> tuple[bool, str]:
    """The emitted module verifies clean (allow_warnings iff a Tested fn)."""
    desc = load_task(tid)
    has_tested = any(t == "Tested" for t in desc["tiers"].values())
    runner = DafnyRunner()
    ok, detail = runner.verify_dafny(emitted, timeout=180, allow_warnings=has_tested)
    if not ok:
        return False, detail.strip()[:500]
    # the emitted caller must call the _checked wrappers, never the stubs (T4)
    has_wrapper = all(
        f"{s['name']}_checked(" in emitted for s in desc["externs"] if s.get("contract")
    )
    return has_wrapper, "emitted module dafny verify clean + wrappers present"


def probe_e_boundary(tid: str, emitted: str) -> tuple[bool, dict]:
    """Hidden tests through the emitted Dafny: 0 boundary_okleak, all PASS."""
    desc = load_task(tid)
    runner = DafnyRunner()
    # Tested-bearing modules: the Tested fn's `{:verify false}` (tier semantics,
    # R10 note) triggers Dafny's dev-only advisory warning — the translate step
    # needs --allow-warnings exactly like verify_dafny does (E5 gate finding).
    has_tested = any(t == "Tested" for t in desc["tiers"].values())
    markers = runner.run_emitted_dafny_b_marked(
        desc, emitted, desc["fn"], no_verify=True, allow_warnings=has_tested
    )
    violation_idx = [i for i, c in enumerate(desc["hidden_tests"]) if c.get("violation")]
    leaks = sum(1 for i in violation_idx if markers[i] == "OKLEAK")
    passed = markers.count("PASS")
    good = leaks == 0 and passed == len(desc["hidden_tests"])
    return good, {
        "leaks": leaks,
        "passed": passed,
        "total": len(desc["hidden_tests"]),
        "markers": markers,
    }


def probe_f_effort(tid: str, elab: Elaborator, emitted: str) -> tuple[bool, list[str]]:
    """Per-fn solver seconds via --filter-symbol into EffortMeter (R7)."""
    desc = load_task(tid)
    runner = DafnyRunner()
    plan = elab.tier_plan
    meter = EffortMeter(plan)
    notes: list[str] = []
    ok = True
    for fn_name, tier in desc["tiers"].items():
        if tier == "Tested":
            continue  # NO_PROOF: never reaches a verify call
        t0 = time.monotonic()
        v_ok, _ = runner.verify_dafny(
            emitted,
            timeout=120,
            verify_limit=CONTRACTED_VERIFY_LIMIT_S if tier == "Contracted" else None,
            filter_symbol=fn_name,
            allow_warnings=any(t == "Tested" for t in desc["tiers"].values()),
        )
        dt = time.monotonic() - t0
        meter.record(fn_name, dt, outcome="verified" if v_ok else "failed")
        if not v_ok:
            ok = False
    # the Tested fn must be recorded not_run with 0 verify_s
    tested_fns = [n for n, t in desc["tiers"].items() if t == "Tested"]
    for n in tested_fns:
        rec = meter.for_fn(n)
        if rec.outcome != "not_run" or rec.actual_s != 0.0:
            ok = False
    report = {r["fn"]: r for r in meter.report()}
    for fn_name, tier in desc["tiers"].items():
        if tier == "Tested":
            notes.append(
                f"    {fn_name} (Tested): never run — verify_s 0.0, outcome not_run"
            )
        else:
            r = report[fn_name]
            notes.append(
                f"    {fn_name} ({tier}): {r['verify_s']}s — {r['outcome']}"
            )
    return ok, notes


def run_task(tid: str) -> dict:
    print(f"[{tid}]")
    desc = load_task(tid)
    ok_ab, notes, elab, emitted = probe_a_b_e_compose_e4b_effects(tid)
    for n in notes:
        print(n)
    if not ok_ab:
        print("  verdict: FAIL (compose/E4b/effects/tiers)")
        return {"id": tid, "pass": False, "fail": "compose/E4b/effects/tiers"}

    ok_d, detail_d = probe_d_verify(tid, elab, emitted)
    print(f"  verify: {'PASS' if ok_d else 'FAIL'} — {detail_d[:120]}")

    ok_e, stats_e = probe_e_boundary(tid, emitted)
    print(
        f"  boundary: {'PASS' if ok_e else 'FAIL'} — {stats_e['passed']}/{stats_e['total']} "
        f"hidden tests pass, {stats_e['leaks']} boundary_okleak on violation cases"
    )
    if not ok_e:
        print(f"    markers: {stats_e['markers']}")

    ok_f, notes_f = probe_f_effort(tid, elab, emitted)
    for n in notes_f:
        print(n)
    print(f"  effort: {'PASS' if ok_f else 'FAIL'}")

    all_ok = ok_ab and ok_d and ok_e and ok_f
    print(f"  verdict: {'PASS' if all_ok else 'FAIL'}")
    return {
        "id": tid,
        "pass": all_ok,
        "compose_e4b_effects": ok_ab,
        "verify": ok_d,
        "boundary_okleak": stats_e["leaks"],
        "hidden_passed": stats_e["passed"],
        "hidden_total": stats_e["total"],
        "effort": ok_f,
    }


def main() -> int:
    print("E5-real gate: multi-function w-task oracles through the typed pipeline (E4b + E5-real)")
    rows = [run_task(tid) for tid in W_TASK_IDS]
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / "e5_real_gate.jsonl"
    with log.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"\nevidence: {log.relative_to(PHASE0_DIR)}")
    if all(r["pass"] for r in rows):
        print(
            "\nE5-real GATE PASS: all 4 oracles — E4b deps resolved, tiers routed per "
            "function, effects declared==inferred, dafny verify clean, 0 boundary_okleak, "
            "effort metered per function (Tested never run)"
        )
        return 0
    print(f"\nE5-real GATE FAIL ({sum(1 for r in rows if not r['pass'])}/4 tasks failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
