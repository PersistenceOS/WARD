"""E10 gate: the standalone SMT-backed checker (the '3+' roadmap row, first slice).

Pre-registered (files/ward-phase2-scoping.md, this session): Ward standing
alone for the proof-carrying slice of the language — the ward-core IR verifies
its own verification conditions DIRECTLY against Z3, with no Dafny in the
check leg:

    compose -> elaborate (typed pipeline) -> Z3ModuleVerifier -> verdict/cex

Gates (all four legs):

  A. w1-w8 all verify Z3-direct — the 8 real w-task oracles (Proven x6,
     Contracted x1, Tested x1) elaborate and every proof-carrying fn verifies
     through the Z3 backend with ZERO dafny invocations in the check leg (the
     leg is elaborator + z3 only; dafny appears ONLY in leg D's parity
     cross-check).
  B. negative probes fail with a counterexample — a buggy caller that ignores
     an extern's Err (boundary-escape shape) and an overclaimed contract
     (anti-slop shape) both FAIL, and the rendered counterexample names the
     violating inputs.
  C. effort metered per function (R7) — Proven/Contracted fns record
     verify_s > 0 and outcome verified; the Tested fn is never run
     (not_run, 0.0 s — T6: no proof obligation).
  D. parity cross-check vs `dafny verify` on the same emitted sources — the
     Z3 verdict and the dafny verdict agree task-for-task (the parity leg is
     the ONLY dafny user, so the check leg's 0-dafny claim is structural).
  E. t2/t3 extension table (informational, not a gate) — the Phase-0/1 corpus
     (loops + invariants, quantifiers, List<int>) runs through the Z3 backend
     to report verified / not_proved honestly (no silent pass).

Evidence: experiments/runs/e10_gate.jsonl.

Usage:  python -m wardcore.e10_gate
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.z3_backend import Z3ModuleVerifier

PHASE0_DIR = Path(__file__).resolve().parent.parent
W_TASKS_DIR = PHASE0_DIR / "benchmarks" / "w_tasks"
TASKS_DIR = PHASE0_DIR / "benchmarks" / "tasks"
RUNS_DIR = PHASE0_DIR / "experiments" / "runs"

W_TASK_IDS = [
    "w1_payment_chain",
    "w2_two_account_ledger",
    "w3_session_otp",
    "w4_order_placement",
    "w5_currency_roundtrip",
    "w6_crud_handler",
    "w7_idempotency",
    "w8_multi_currency_ledger",
]

# Phase-0/1 corpus for the extension table (exercises loops/quantifiers/lists).
T23_IDS = [
    "t2_last_occurrence", "t2_manhattan", "t2_product_list", "t2_sum_evens",
    "t2_sum_of_squares", "t2_transfer", "t2_try_divide", "t2_withdraw",
    "t3_count_divisors", "t3_count_primes_up_to", "t3_digit_count", "t3_fib",
    "t3_floor_sqrt", "t3_has_consecutive_duplicates", "t3_has_sum_pair",
    "t3_is_power_of_two", "t3_is_prime", "t3_longest_run", "t3_max_adjacent_sum",
    "t3_smallest_positive", "t3_sum_between", "t3_withdraw_twice",
]


def load_task(tid: str, dir_: Path) -> dict:
    return json.loads((dir_ / f"{tid}.json").read_text(encoding="utf-8"))


def _tier_line(desc: dict) -> str:
    """w1-w8 reference callers carry no `tier:` annotation (the JSON does) —
    inject it so the typed pipeline's tier routing sees the declared tier."""
    tiers = desc.get("tiers") or {}
    if not tiers:
        return ""
    # single-fn tasks: one fn, one tier
    return f"tier: {next(iter(tiers.values()))}\n"


def compose_w(desc: dict) -> str:
    """Compose the full ward0 surface for a w-task: extern defs (from the JSON,
    exactly as the Phase-1 harness prepends them) + the injected tier + the
    reference caller. Reuses the E5-real composition so the typed pipeline sees
    the same surface as the model does."""
    from wardcore.e5_real_gate import compose_module

    ref = (W_TASKS_DIR / f"{desc['id']}.ward0").read_text(encoding="utf-8")
    composed = compose_module(desc, ref)
    # inject the tier annotation before the first fn def
    tier = _tier_line(desc)
    if tier:
        lines = composed.splitlines()
        for i, line in enumerate(lines):
            if line.strip().startswith("fn ") or line.strip().startswith("extern fn "):
                lines.insert(i, tier.rstrip("\n"))
                break
        composed = "\n".join(lines) + "\n"
    return composed


def compose_t23(desc: dict) -> str:
    """t2/t3 tasks: no externs, no tiers — the plain reference ward0."""
    ward0 = (TASKS_DIR / f"{desc['id']}.ward0").read_text(encoding="utf-8")
    return ward0


# ---------------------------------------------------------------------------
# Leg A: the Z3-direct check leg (ZERO dafny calls — elaborator + z3 only)
# ---------------------------------------------------------------------------


def leg_a_z3_check() -> tuple[list[dict], int]:
    """Elaborate + Z3-verify every w-task. Returns (records, dafny_call_count)
    where dafny_call_count is structurally 0 (no DafnyRunner in this leg)."""
    records = []
    elab = Elaborator(enforce_boundary=True)
    for tid in W_TASK_IDS:
        desc = load_task(tid, W_TASKS_DIR)
        src = compose_w(desc)
        emitted = elab.transpile(src)
        verifier = Z3ModuleVerifier(elab.module, elab.tier_plan)
        fn_records = verifier.verify_all()
        t0 = time.monotonic()
        for fname, rec in fn_records.items():
            rec["task"] = tid
        records.append({"task": tid, "fns": fn_records, "elab_s": round(time.monotonic() - t0, 3)})
    return records, 0


def check_leg_a(records: list[dict]) -> tuple[bool, list[str]]:
    """Every Proven/Contracted fn -> verified; Tested -> not_run (never run)."""
    ok = True
    notes = []
    for rec in records:
        tid = rec["task"]
        for fname, fr in rec["fns"].items():
            tier = fr["tier"]
            if tier == "Tested":
                good = fr["outcome"] == "not_run" and fr["verify_s"] == 0.0
                notes.append(f"  {tid}/{fname} (Tested): not_run, verify_s 0.0 — {'PASS' if good else 'FAIL'}")
            else:
                good = fr["outcome"] == "verified"
                notes.append(f"  {tid}/{fname} ({tier}): {fr['outcome']} in {fr['verify_s']}s — {'PASS' if good else 'FAIL'}")
            ok = ok and good
    return ok, notes


# ---------------------------------------------------------------------------
# Leg B: negative probes — broken callers must FAIL with a counterexample
# ---------------------------------------------------------------------------


def leg_b_negatives() -> tuple[bool, list[str]]:
    """Two bug classes, both through the same Z3-direct leg:
    B1 buggy caller ignores an extern's Err (boundary-escape shape);
    B2 overclaimed contract (anti-slop shape). Both must fail with a cex."""
    notes = []
    ok = True

    # B1: w1 caller that charges and returns Ok unconditionally — a declined
    # charge (amount > 100) must NOT come back as Ok, but this caller lets it.
    desc = load_task("w1_payment_chain", W_TASKS_DIR)
    src = compose_w(desc)
    buggy_caller = (
        "fn pay(user_id: int, amount: int, token: str) -> Result<Unit, str>\n"
        "  requires user_id > 0\n"
        "  requires amount > 0\n"
        "  ensures is_ok(result) == (user_id < 1000 and amount <= 100)\n"
        "{\n"
        "    var a: Result<Unit, str> = auth_check(user_id);\n"
        "    if is_err(a) {\n"
        "        return Err(unwrap_err(a));\n"
        "    }\n"
        "    var r: Result<Unit, str> = rate_limit(amount);\n"
        "    if is_err(r) {\n"
        "        return Err(unwrap_err(r));\n"
        "    }\n"
        "    var c: Result<Unit, str> = stripe_charge(amount, token);\n"
        "    return Ok(());\n"
        "}\n"
    )
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("fn pay("):
            src = "\n".join(lines[:i]) + "\n" + buggy_caller
            break
    elab = Elaborator(enforce_boundary=True)
    elab.transpile(src)
    fr = Z3ModuleVerifier(elab.module, elab.tier_plan).verify_all()["pay"]
    b1_ok = fr["outcome"] == "failed" and fr["counterexample"]
    cex = fr["counterexample"] or {}
    # the cex must show a declined-but-ok'd amount: user_id < 1000 and amount > 100
    cex_ok = cex.get("user_id") is not None and (cex.get("amount") or 0) > 100
    notes.append(
        f"  B1 (caller ignores extern Err): {'PASS' if b1_ok and cex_ok else 'FAIL'} "
        f"outcome={fr['outcome']} cex={cex}"
    )
    ok = ok and b1_ok and cex_ok

    # B2: overclaimed contract — the caller claims the gateway accepts up to
    # 50 when the extern's contract says 100. amount in (50, 100] is a leak.
    overclaim = buggy_caller.replace(
        "ensures is_ok(result) == (user_id < 1000 and amount <= 100)",
        "ensures is_ok(result) == (user_id < 1000 and amount <= 50)",
    )
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("fn pay("):
            src = "\n".join(lines[:i]) + "\n" + overclaim
            break
    elab = Elaborator(enforce_boundary=True)
    elab.transpile(src)
    fr2 = Z3ModuleVerifier(elab.module, elab.tier_plan).verify_all()["pay"]
    cex2 = fr2["counterexample"] or {}
    # overclaim: the caller claims the gateway accepts up to 50 when its
    # contract says 100 — ANY amount > 50 with a user < 1000 is a violation
    # (the extern's Ok for 50 < amount <= 100, or the caller's unconditional
    # Ok for amount > 100, both prove the claim false)
    b2_ok = fr2["outcome"] == "failed" and (cex2.get("amount") or 0) > 50
    notes.append(
        f"  B2 (overclaimed contract): {'PASS' if b2_ok else 'FAIL'} "
        f"outcome={fr2['outcome']} cex={cex2}"
    )
    ok = ok and b2_ok
    return ok, notes


# ---------------------------------------------------------------------------
# Leg C: effort metering (R7) — folded into leg A records; checked separately
# ---------------------------------------------------------------------------


def check_leg_c(records: list[dict]) -> tuple[bool, list[str]]:
    ok = True
    notes = []
    proven_seen = 0
    for rec in records:
        for fname, fr in rec["fns"].items():
            if fr["tier"] == "Tested":
                good = fr["verify_s"] == 0.0 and fr["outcome"] == "not_run"
                notes.append(f"  {rec['task']}/{fname}: Tested verify_s 0.0 not_run — {'PASS' if good else 'FAIL'}")
            else:
                proven_seen += 1
                good = fr["verify_s"] > 0 and fr["outcome"] == "verified"
                notes.append(f"  {rec['task']}/{fname}: verify_s {fr['verify_s']} — {'PASS' if good else 'FAIL'}")
            ok = ok and good
    notes.append(f"  proof-carrying obligations metered: {proven_seen}")
    return ok, notes


# ---------------------------------------------------------------------------
# Leg D: parity cross-check vs `dafny verify` (the ONLY dafny user in E10)
# ---------------------------------------------------------------------------


def leg_d_parity() -> tuple[bool, list[str]]:
    from harness.dafny_runner import DafnyRunner

    runner = DafnyRunner()
    notes = []
    ok = True
    for tid in W_TASK_IDS:
        desc = load_task(tid, W_TASKS_DIR)
        src = compose_w(desc)
        elab = Elaborator(enforce_boundary=True)
        emitted = elab.transpile(src)
        has_tested = any(t == "Tested" for t in (desc.get("tiers") or {}).values())
        v_ok, detail = runner.verify_dafny(emitted, timeout=120, allow_warnings=has_tested)
        frs = Z3ModuleVerifier(elab.module, elab.tier_plan).verify_all()
        # Z3 verdict: every proof-carrying fn verified (Tested fns are not_run)
        z3_ok = all(
            (f["outcome"] == "verified") if f["tier"] != "Tested" else (f["outcome"] == "not_run")
            for f in frs.values()
        )
        agree = (v_ok == z3_ok)
        notes.append(
            f"  {tid}: dafny={'PASS' if v_ok else 'FAIL'} z3={'PASS' if z3_ok else 'FAIL'} "
            f"agree={'PASS' if agree else 'FAIL'}"
        )
        ok = ok and agree
    return ok, notes


# ---------------------------------------------------------------------------
# Leg E: t2/t3 extension table (informational — no gate, no dafny)
# ---------------------------------------------------------------------------


def leg_e_extension() -> list[dict]:
    rows = []
    elab = Elaborator(enforce_boundary=True)
    for tid in T23_IDS:
        desc = load_task(tid, TASKS_DIR)
        src = compose_t23(desc)
        try:
            elab.transpile(src)
            frs = Z3ModuleVerifier(elab.module, elab.tier_plan).verify_all()
            outcomes = {f: r["outcome"] for f, r in frs.items()}
        except (ElaborationError, Exception) as exc:  # noqa: BLE001 — honest table
            outcomes = {"elaboration_error": str(exc)[:80]}
        rows.append({"task": tid, "outcomes": outcomes})
    return rows


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    print("E10 gate: standalone SMT-backed checker (Z3-direct, no Dafny in the check leg)")
    t0 = time.monotonic()

    records, dafny_calls = leg_a_z3_check()
    print(f"\n[leg A] Z3-direct check leg over w1-w8 (elaborator + z3 only, "
          f"{dafny_calls} dafny invocations):")
    a_ok, a_notes = check_leg_a(records)
    for n in a_notes:
        print(n)

    c_ok, c_notes = check_leg_c(records)
    print("\n[leg C] per-function effort (R7):")
    for n in c_notes:
        print(n)

    b_ok, b_notes = leg_b_negatives()
    print("\n[leg B] negative probes (must fail with a counterexample):")
    for n in b_notes:
        print(n)

    print("\n[leg D] parity vs `dafny verify` on the same emitted sources:")
    d_ok, d_notes = leg_d_parity()
    for n in d_notes:
        print(n)

    ext_rows = leg_e_extension()
    verified = sum(1 for r in ext_rows if any(v == "verified" for v in r["outcomes"].values()))
    not_proved = sum(1 for r in ext_rows if any(v == "not_proved" for v in r["outcomes"].values()))
    print(f"\n[leg E] t2/t3 extension table (informational): {len(ext_rows)} tasks, "
          f"{verified} verified, {not_proved} not_proved via z3 (no silent pass)")

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    log = RUNS_DIR / "e10_gate.jsonl"
    with log.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps({"leg": "A", **rec}) + "\n")
        for row in ext_rows:
            fh.write(json.dumps({"leg": "E", **row}) + "\n")
    print(f"\nevidence: {log.relative_to(PHASE0_DIR)}")

    all_ok = a_ok and b_ok and c_ok and d_ok
    print(f"\nE10 gate {'PASS' if all_ok else 'FAIL'} in {time.monotonic() - t0:.1f}s "
          f"(legs A/B/C/D: {a_ok}/{b_ok}/{c_ok}/{d_ok})")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
