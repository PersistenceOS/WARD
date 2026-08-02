"""E11 gate: the first multi-target backend slice (README roadmap row 4).

Pre-registered (this session): Ward's core compiles to an existing host
runtime rather than inventing a parallel ecosystem (design §4d, mirroring
Dafny's own multi-target compiler). `py_backend` emits **Python** from the
elaborated ward-core IR — the simplest real backend — and the gate requires
FUNCTIONAL PARITY: the hidden tests run against the emitted Python must
produce the same per-case markers / pass set as the Dafny path on the same
sources.

    compose -> elaborate (typed pipeline) -> py_backend.emit -> exec + run
                                    ^ same IR, two backends:
                       elaborator-emitted Dafny -> dafny translate + run

Gates (all four legs):

  A. w1-w8 corpus parity — for every w-task the emitted-Python marker list
     (PASS/OKLEAK/ERRFAIL per hidden test, using the EXACT `_build_main_b`
     semantics) equals the elaborator-emitted-Dafny marker list from the same
     IR. The Python leg is exec-only: ZERO dafny invocations in the check
     leg (dafny appears only as leg A's reference runner).
  B. boundary enforcement through the Python backend — all 8 w-tasks are
     buggy scenarios (extern stubs violate their contracts in the
     `violation_probes` region). Probing the emitted wrappers directly:
     enforce-on converts every violation=true probe to Err("contract
     violation") (never Ok); enforce-off leaks the raw stub result (Ok) —
     the wrapper demonstrably does real work through Python, and every
     w-task shows at least one converted leak.
  C. t2/t3 extern-free parity — the Phase-0/1 corpus (loops + invariants,
     quantifiers, List<int>) runs through the emitted Python and the pass
     set equals the Dafny path's pass set on the same IR.
  D. structural — the emitted Python contains no dafny invocation and no
     direct stub call survives (T4: every extern call site routes through
     the generated `_checked` wrapper, exactly one `_stub(` per extern).

Evidence: experiments/runs/e11_gate.jsonl.

Usage:  python -m wardcore.e11_gate
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.py_backend import PyEmitter

PHASE0_DIR = Path(__file__).resolve().parent.parent
W_TASKS_DIR = PHASE0_DIR / "benchmarks" / "w_tasks"
TASKS_DIR = PHASE0_DIR / "benchmarks" / "tasks"
RUNS_DIR = PHASE0_DIR / "experiments" / "runs"

from wardcore.e10_gate import (  # noqa: E402  (reuse the canonical composition)
    T23_IDS,
    W_TASK_IDS,
    compose_t23,
    compose_w,
    load_task,
)


# ---------------------------------------------------------------------------
# Python-leg helpers (exec-only — the "0 dafny in the check leg" claim rests
# on these functions never touching DafnyRunner / subprocess).
# ---------------------------------------------------------------------------


def py_literal(ns: dict, value):
    """Render a JSON test value as an emitted-Python literal. `{"ok": v}` /
    `{"err": s}` -> the Result constructors from the emitted namespace;
    plain int/bool/str/list/None -> the value itself."""
    if isinstance(value, dict):
        if "ok" in value:
            return ns["Ok"](py_literal(ns, value["ok"]))
        if "err" in value:
            return ns["Err"](py_literal(ns, value["err"]))
    if isinstance(value, list):
        return [py_literal(ns, v) for v in value]
    return value


def _py_eq(out, exp) -> bool:
    """Structural equality: Result instances compare via the emitted
    `Result.__eq__`; everything else with plain == (lists, ints, bools)."""
    try:
        return out == exp
    except Exception:  # noqa: BLE001 — emitted code, any comparison failure is a mismatch
        return False


def run_py_markers(py_src: str, fn_name: str, desc: dict) -> list[str]:
    """Run the emitted Python against the hidden tests, computing the exact
    `_build_main_b` markers: PASS = output matched the expected literal;
    OKLEAK = output was Ok but the case expected an Err (a boundary escape);
    ERRFAIL = some other mismatch."""
    ns: dict = {}
    exec(py_src, ns)  # noqa: S102 — emitted code, gate-only
    fn = ns[fn_name]
    markers = []
    for case in desc["hidden_tests"]:
        out = fn(*case["in"])
        exp = py_literal(ns, case["out"])
        if _py_eq(out, exp):
            markers.append("PASS")
        elif getattr(out, "is_ok", False):
            markers.append("OKLEAK")
        else:
            markers.append("ERRFAIL")
    return markers


def run_py_passes(py_src: str, fn_name: str, desc: dict) -> list[bool]:
    """Pass set for plain t2/t3 tasks (no Result marker semantics)."""
    ns: dict = {}
    exec(py_src, ns)  # noqa: S102 — emitted code, gate-only
    fn = ns[fn_name]
    return [_py_eq(fn(*case["in"]), py_literal(ns, case["out"])) for case in desc["hidden_tests"]]


def _fn_name_from_ward0(src: str) -> str:
    for line in src.splitlines():
        if line.strip().startswith("fn "):
            return line.strip().split("(")[0].split()[1]
    raise RuntimeError("no fn found in ward0 source")


# ---------------------------------------------------------------------------
# Leg A: w1-w8 marker parity (emitted Python == elaborator-emitted Dafny)
# ---------------------------------------------------------------------------


def leg_a_parity() -> tuple[bool, list[str]]:
    from harness.dafny_runner import DafnyRunner

    runner = DafnyRunner()
    notes = []
    ok = True
    elab = Elaborator(enforce_boundary=True)
    for tid in W_TASK_IDS:
        desc = load_task(tid, W_TASKS_DIR)
        src = compose_w(desc)
        emitted = elab.transpile(src)
        impls = {e["name"]: e["impl"] for e in DafnyRunner.externs_of(desc)}
        py = PyEmitter().emit(elab.module, extern_impls=impls)
        fn_name = desc["fn"]
        py_markers = run_py_markers(py, fn_name, desc)
        has_tested = any(t == "Tested" for t in (desc.get("tiers") or {}).values())
        dfy_markers = runner.run_emitted_dafny_b_marked(
            desc, emitted, fn_name, allow_warnings=has_tested
        )
        match = py_markers == dfy_markers
        notes.append(
            f"  {tid}: py={py_markers} dfy={dfy_markers} parity={'PASS' if match else 'FAIL'}"
        )
        ok = ok and match
    return ok, notes


# ---------------------------------------------------------------------------
# Leg B: boundary enforcement through the Python backend (violation probes)
# ---------------------------------------------------------------------------


def _extern_wrapper_probe(desc: dict, elab: Elaborator, enforce: bool) -> dict[str, list[tuple[bool, str]]]:
    """Emit Python for the module (enforce on/off) and run every extern's
    violation_probes through its generated wrapper. Returns
    extern name -> [(violation_flag, out_repr)] in probe order (index-keyed so
    duplicate violation flags never collapse — the reviewer-caught bug)."""
    from harness.dafny_runner import DafnyRunner

    impls = {e["name"]: e["impl"] for e in DafnyRunner.externs_of(desc)}
    py = PyEmitter(enforce_boundary=enforce).emit(elab.module, extern_impls=impls)
    ns: dict = {}
    exec(py, ns)  # noqa: S102 — emitted code, gate-only
    results: dict[str, list[tuple[bool, str]]] = {}
    for ext in DafnyRunner.externs_of(desc):
        name = ext["name"]
        wrapper = ns[name]
        results[name] = []
        for probe in ext.get("violation_probes", []):
            args = tuple(probe["in"])
            out = wrapper(*args)
            results[name].append((probe["violation"], repr(out)))
    return results


def leg_b_boundary() -> tuple[bool, list[str]]:
    """Boundary enforcement THROUGH the emitted Python. Violation shapes are
    not all over-grants: w3's session_valid is an under-grant (the stub
    returns Err where the contract demands Ok). The universal rule: on a
    violation=true probe, enforce-on must convert ANY contradiction to
    Err("contract violation") (never pass the stub's violating output through),
    and enforce-off must differ from enforce-on (the wrapper does real work);
    violation=false probes must never be converted."""
    notes = []
    ok = True
    elab = Elaborator(enforce_boundary=True)
    total_probes = 0
    converted = 0
    for tid in W_TASK_IDS:
        desc = load_task(tid, W_TASKS_DIR)
        src = compose_w(desc)
        elab.transpile(src)
        on = _extern_wrapper_probe(desc, elab, enforce=True)
        off = _extern_wrapper_probe(desc, elab, enforce=False)
        task_ok = True
        converted_any = False
        for name, probes in on.items():
            for i, (violation, on_repr) in enumerate(probes):
                total_probes += 1
                off_repr = off[name][i][1]
                if violation:
                    # enforce-on converts ANY contradiction (over- or
                    # under-grant) to Err("contract violation") — the stub's
                    # violating output never crosses the boundary.
                    converted_ok = "contract violation" in on_repr and not on_repr.startswith("Ok(")
                    # the wrapper does real work: enforce-off passes the stub's
                    # raw output through, so the two runs must differ.
                    differs = on_repr != off_repr
                    good = converted_ok and differs
                    if good:
                        converted += 1
                        converted_any = True
                    task_ok = task_ok and good
                else:
                    # violation=false probes must NOT be converted (contract holds)
                    if "contract violation" in on_repr:
                        task_ok = False
        notes.append(
            f"  {tid}: enforce-on blocks all violation probes {'PASS' if task_ok and converted_any else 'FAIL'} "
            f"(contradictions converted: {converted} so far)"
        )
        ok = ok and task_ok and converted_any
    notes.append(f"  boundary probes total: {total_probes}, contradictions converted to Err: {converted}")
    return ok, notes


# ---------------------------------------------------------------------------
# Leg C: t2/t3 extern-free parity (pass set == Dafny path pass set)
# ---------------------------------------------------------------------------


def leg_c_t23_parity() -> tuple[bool, list[str]]:
    from harness.dafny_runner import DafnyRunner

    runner = DafnyRunner()
    notes = []
    ok = True
    elab = Elaborator(enforce_boundary=True)
    for tid in T23_IDS:
        desc = load_task(tid, TASKS_DIR)
        src = compose_t23(desc)
        try:
            emitted = elab.transpile(src)
            fn_name = _fn_name_from_ward0(src)
            py = PyEmitter().emit(elab.module)
            py_passes = run_py_passes(py, fn_name, desc)
            dfy_passes = runner.run_hidden_tests_dafny(desc, emitted)
            match = py_passes == dfy_passes
            py_all = all(py_passes)
            # triage: an all-False dfy leg means the DAFNY path itself failed
            # to compile/verify this task — a pre-existing condition to
            # investigate, not (necessarily) an emitter divergence.
            dfy_broken = not any(dfy_passes)
            tag = "PASS" if (match and py_all) else ("FAIL(dfy-path-broken)" if dfy_broken else "FAIL")
            notes.append(
                f"  {tid}: py={py_passes} dfy={dfy_passes} parity={tag} "
                f"(py all-pass: {py_all})"
            )
            ok = ok and match and py_all
        except (ElaborationError, Exception) as exc:  # noqa: BLE001 — honest row
            notes.append(f"  {tid}: ERROR {str(exc)[:90]}")
            ok = False
    return ok, notes


# ---------------------------------------------------------------------------
# Leg D: structural — no dafny, no direct stub call survives (T4)
# ---------------------------------------------------------------------------


def leg_d_structural() -> tuple[bool, list[str]]:
    from harness.dafny_runner import DafnyRunner

    notes = []
    ok = True
    elab = Elaborator(enforce_boundary=True)
    for tid in W_TASK_IDS:
        desc = load_task(tid, W_TASKS_DIR)
        src = compose_w(desc)
        elab.transpile(src)
        impls = {e["name"]: e["impl"] for e in DafnyRunner.externs_of(desc)}
        py = PyEmitter().emit(elab.module, extern_impls=impls)
        n_externs = len(DafnyRunner.externs_of(desc))
        # T4: no DIRECT stub call survives — the only `_stub(` calls in the
        # emitted source must be the wrapper's own `out = <name>_stub(...)`
        # line (one per extern), never from a caller fn body. The stub `def`
        # lines carry `_stub(` too, so count only non-def call lines.
        stub_call_lines = [
            ln for ln in py.splitlines()
            if "_stub(" in ln and not ln.lstrip().startswith("def ")
        ]
        t4_ok = len(stub_call_lines) == n_externs and all(
            " = " in ln for ln in stub_call_lines
        )
        no_dafny = "dafny" not in py.lower()
        notes.append(
            f"  {tid}: direct stub calls={len(stub_call_lines)} (externs={n_externs}) "
            f"T4={'PASS' if t4_ok else 'FAIL'} no-dafny={'PASS' if no_dafny else 'FAIL'}"
        )
        ok = ok and t4_ok and no_dafny
    return ok, notes


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("E11 gate: first multi-target backend slice — Python emitter, functional parity vs Dafny path")
    t0 = time.monotonic()

    a_ok, a_notes = leg_a_parity()
    print("\n[leg A] w1-w8 marker parity (emitted Python == elaborator-emitted Dafny):")
    for n in a_notes:
        print(n)

    b_ok, b_notes = leg_b_boundary()
    print("\n[leg B] boundary enforcement through the Python backend (violation probes):")
    for n in b_notes:
        print(n)

    c_ok, c_notes = leg_c_t23_parity()
    print("\n[leg C] t2/t3 extern-free parity (pass set == Dafny path):")
    for n in c_notes:
        print(n)

    d_ok, d_notes = leg_d_structural()
    print("\n[leg D] structural — no dafny, no direct stub call survives (T4):")
    for n in d_notes:
        print(n)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / "e11_gate.jsonl"
    with log.open("w", encoding="utf-8") as fh:
        for n in a_notes + b_notes + c_notes + d_notes:
            fh.write(json.dumps({"line": n}) + "\n")
    print(f"\nevidence: {log.relative_to(PHASE0_DIR)}")

    all_ok = a_ok and b_ok and c_ok and d_ok
    print(f"\nE11 gate {'PASS' if all_ok else 'FAIL'} in {time.monotonic() - t0:.1f}s "
          f"(legs A/B/C/D: {a_ok}/{b_ok}/{c_ok}/{d_ok})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
