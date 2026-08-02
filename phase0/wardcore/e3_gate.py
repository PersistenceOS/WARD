"""E3 gate: the extern boundary is core-level (files/ward-phase2-scoping.md §6).

Pre-registered E3: 0 `boundary_okleak` on enforce-on runs; contract-less extern
= hard error (probe); direct-stub-call = hard error (probe). This runner
executes all three probes against the ward-core elaborator's extern pass.

Probes:
  A. contract-less extern -> hard ElaborationError ("contract is mandatory")
  B. direct stub call -> T4 validation flags it; the pass rewrites it away so no
     direct stub call survives elaboration; the emitted caller never calls the
     stub directly under enforce
  C. boundary_okleak across ALL SIX b-tasks (C2-style regression suite): each
     scenario compiled and RUN through the ELABORATOR's emitted Dafny (extern
     decls + `_checked` wrappers + caller) with a naive caller that passes the
     stub result straight through. Buggy scenarios (b1/b3/b5: the stub violates
     its contract on violation-flagged hidden tests) must leak enforce-off and
     show 0 OKLEAK enforce-on; conforming controls (b2/b4/b6: stub honours its
     contract, no violation-flagged cases) must pass every hidden test in both
     arms (the wrapper must never fire on a conforming stub — no false
     positives). Per-scenario leak table printed and dumped to
     experiments/runs/e3_probe_c_all_b.jsonl.

Usage:  python -m wardcore.e3_gate
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from harness.dafny_runner import DafnyRunner

from wardcore.elaborator import Elaborator, ElaborationError

PHASE0_DIR = Path(__file__).resolve().parent.parent
B_TASKS_DIR = PHASE0_DIR / "benchmarks" / "b_tasks"
RUNS_DIR = PHASE0_DIR / "experiments" / "runs"

# All six b-task scenarios. b1/b3/b5 are `buggy: true` (stub violates its
# contract on the violation-flagged hidden tests); b2/b4/b6 are conforming
# controls (0 violation-flagged cases) proving the wrapper never false-fires.
B_TASK_IDS = ["b1_payment", "b2_auth", "b3_db", "b4_rest", "b5_currency", "b6_transfer"]


def naive_caller(desc: dict) -> str:
    """A caller that passes the stub's result straight through the boundary
    (zero defense). Mirrors the trust-arm reference shape: the caller's
    contract is identical to the extern's, the body is
    `var r = stub(...); return Ok(unwrap_ok(r)) if is_ok(r) else Err(unwrap_err(r))`.
    """
    stub = DafnyRunner.externs_of(desc)[0]
    params = ", ".join(f"{n}: {t}" for n, t in stub["params"])
    args = ", ".join(n for n, _ in stub["params"])
    ret = stub["ret"]
    contract = "\n".join("  " + line for line in (stub.get("contract") or "").splitlines())
    return (
        f"fn {desc['fn']}({params}) -> {ret}\n"
        f"{contract}\n"
        "{\n"
        f"    var r: {ret} = {stub['name']}({args});\n"
        "    if is_ok(r) {\n"
        "        return Ok(unwrap_ok(r));\n"
        "    }\n"
        "    return Err(unwrap_err(r));\n"
        "}\n"
    )


def probe_a_contract_less_extern() -> bool:
    """E3 probe A: a contract-less extern is a hard elaboration error."""
    bad = (
        "extern fn db_get(key: int) -> Result<int, str>;\n"
        'trust: "oracle reference stub"\n\n'
        "fn f(key: int) -> Result<int, str>\n"
        "  requires key > 0\n"
        "  ensures is_ok(result)\n"
        "{\n"
        "    var r: Result<int, str> = db_get(key);\n"
        "    return r;\n"
        "}\n"
    )
    try:
        Elaborator(enforce_boundary=True).transpile(bad)
    except ElaborationError as exc:
        ok = "contract is mandatory" in str(exc)
        print(f"  probe A (contract-less extern = hard error): {'PASS' if ok else 'FAIL'} -> {str(exc)[:80]}")
        return ok
    print("  probe A (contract-less extern = hard error): FAIL (no error raised)")
    return False


def probe_b_direct_stub_call() -> bool:
    """E3 probe B: no direct stub call survives elaboration (T4)."""
    src = (
        "extern fn db_get(key: int) -> Result<int, str>\n"
        "  requires key > 0\n"
        "ensures is_ok(result) == (key < 1000);\n"
        'trust: "oracle reference stub"\n\n'
        "fn f(key: int) -> Result<int, str>\n"
        "  requires key > 0\n"
        "  ensures is_ok(result)\n"
        "{\n"
        "    var r: Result<int, str> = db_get(key);\n"
        "    return r;\n"
        "}\n"
    )
    # T4 validation alone flags the direct stub call ...
    module = Elaborator().desugar(src)
    from wardcore.ir import validate_module

    problems = validate_module(module, check_t4=True, check_t3_trust=True)
    flagged = any("not routed through _checked wrapper (T4)" in p for p in problems)
    # ... and the pass rewrites it away: emitted caller never calls the stub
    # directly under enforce.
    emitted = Elaborator(enforce_boundary=True).transpile(src)
    caller = emitted.split("method f(")[1]
    direct = [l for l in caller.splitlines() if "db_get(" in l and "db_get_checked" not in l]
    ok = flagged and not direct and "db_get_checked" in emitted
    print(
        f"  probe B (direct-stub-call = hard error): {'PASS' if ok else 'FAIL'} "
        f"(T4 flags={flagged}, direct calls in caller={len(direct)})"
    )
    return ok


def _run_scenario(runner: DafnyRunner, tid: str) -> dict:
    """Run one b-task through the elaborator's emitted Dafny in both arms.

    Returns a row dict: {id, buggy, n_violation, n_tests, leaks_off, leaks_on,
    pass_off, pass_on, markers_off, markers_on}.
    """
    desc = json.loads((B_TASKS_DIR / f"{tid}.json").read_text(encoding="utf-8"))
    violation_idx = [i for i, c in enumerate(desc["hidden_tests"]) if c.get("violation")]
    ext = runner.extern_ward0_of(desc, "trust") + '\ntrust: "oracle reference stub"\n'
    src = ext + "\n" + naive_caller(desc)
    fn_name = desc["fn"]

    off = Elaborator(enforce_boundary=False).transpile(src)
    markers_off = runner.run_emitted_dafny_b_marked(desc, off, fn_name, no_verify=True)
    on = Elaborator(enforce_boundary=True).transpile(src)
    markers_on = runner.run_emitted_dafny_b_marked(desc, on, fn_name, no_verify=True)

    return {
        "id": tid,
        "buggy": bool(violation_idx),
        "n_violation": len(violation_idx),
        "n_tests": len(desc["hidden_tests"]),
        "leaks_off": sum(1 for i in violation_idx if markers_off[i] == "OKLEAK"),
        "leaks_on": sum(1 for i in violation_idx if markers_on[i] == "OKLEAK"),
        "pass_off": markers_off.count("PASS"),
        "pass_on": markers_on.count("PASS"),
        "markers_off": markers_off,
        "markers_on": markers_on,
    }


def probe_c_boundary_okleak() -> bool:
    """E3 probe C across all six b-tasks: 0 boundary_okleak on enforce-on.

    Buggy scenario gate: leaks_off > 0 (probe is sensitive) AND leaks_on == 0
    AND every hidden test PASSes enforce-on (the wrapper fixes only the leak,
    never collateral behavior). Conforming control gate: leaks_on == 0 AND all
    hidden tests PASS in BOTH arms (the wrapper never fires on a conforming
    stub — no false positives).
    """
    runner = DafnyRunner()
    rows = [_run_scenario(runner, tid) for tid in B_TASK_IDS]
    ok = True
    print("  probe C (0 boundary_okleak on enforce-on, all six b-tasks):")
    print("    id            buggy  viol  tests  leaks-off  leaks-on  pass-off  pass-on  verdict")
    for r in rows:
        if r["buggy"]:
            good = (
                r["leaks_off"] > 0
                and r["pass_off"] == r["n_tests"] - r["leaks_off"]
                and r["leaks_on"] == 0
                and r["pass_on"] == r["n_tests"]
            )
        else:
            good = r["leaks_on"] == 0 and r["pass_off"] == r["n_tests"] and r["pass_on"] == r["n_tests"]
        ok = ok and good
        print(
            f"    {r['id']:<14s}  {'buggy ' if r['buggy'] else 'ctrl  '}"
            f"  {r['n_violation']:4d}  {r['n_tests']:5d}  {r['leaks_off']:8d}  "
            f"{r['leaks_on']:8d}  {r['pass_off']:8d}  {r['pass_on']:7d}  {'PASS' if good else 'FAIL'}"
        )
        if not good:
            print(f"      markers_off: {r['markers_off']}")
            print(f"      markers_on:  {r['markers_on']}")
    # evidence dump (C2-style regression suite log)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / "e3_probe_c_all_b.jsonl"
    with log.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"    evidence: {log.relative_to(PHASE0_DIR)}")
    print(f"  probe C: {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    print("E3 gate: extern boundary is core-level")
    results = [
        probe_a_contract_less_extern(),
        probe_b_direct_stub_call(),
        probe_c_boundary_okleak(),
    ]
    if all(results):
        print("\nE3 GATE PASS: contract-less extern hard error, direct-stub-call hard error, 0 boundary_okleak on enforce-on (all 6 b-tasks)")
        return 0
    print(f"\nE3 GATE FAIL ({results.count(False)}/3 probes failed)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
