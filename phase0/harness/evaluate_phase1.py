"""Phase-1 evaluator: w-tasks (mid-size modules, multi-extern) across four arms.

Pre-registered arms (files/ward-phase1-experiment-design.md §4):
  W          = ward0 + enforce_boundary + tiers          (the treatment)
  D          = raw Dafny                                  (competitor baseline)
  W-enforce  = ward0, enforce OFF, tiers ON               (isolates C2: boundary)
  W-tiers    = ward0, enforce ON, tiers OFF (all Proven)  (isolates C1: cost containment)

Tier semantics (§5 C1, per-entry-function tier from the descriptor):
  Proven    : verification required (hard gate; failure => verify_fail)
  Contracted: bounded proof search (verify_limit=30); on fail/timeout -> fall
              back to running the hidden tests with --no-verify (test-based
              confidence), recorded as tier_fallback
  Tested    : no proof obligation; hidden tests run with --no-verify directly

Metrics per task-arm (B-style + Phase-1):
  escape_total / escape_passed   (violation cases)
  boundary_escape                (OKLEAK count on violation cases — C2)
  tier_fallback                  (Contracted tier fell back to tests)
  verify_s                       (verification effort metering — C1/C3b)
"""

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from harness.dafny_runner import DafnyRunner
from harness.models import DAFNY_GUIDE, FakeModel, OpenCodeModel, WARD0_GUIDE, clean_dafny

ARMS = ("W", "D", "W-enforce", "W-tiers")

TIER_VERIFY_LIMIT = {"Proven": 60, "Contracted": 30}  # Tested never reaches a verify call


@dataclass
class WTaskResult:
    task_id: str
    arm: str
    tier: str
    attempts: list[dict] = field(default_factory=list)
    solved: bool = False
    escape_total: int = 0
    escape_passed: int = 0
    boundary_escape: int = 0
    tier_fallback: bool = False
    verify_s: float = 0.0
    total_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "arm": self.arm,
            "tier": self.tier,
            "solved": self.solved,
            "escape_total": self.escape_total,
            "escape_passed": self.escape_passed,
            "boundary_escape": self.boundary_escape,
            "tier_fallback": self.tier_fallback,
            "verify_s": round(self.verify_s, 2),
            "attempts": self.attempts,
            "seconds": round(self.total_seconds, 2),
        }


def load_w_tasks(tasks_dir: Path, task_ids: set[str] | None = None) -> list[dict]:
    out = []
    for jpath in sorted(tasks_dir.glob("*.json")):
        desc = json.loads(jpath.read_text(encoding="utf-8"))
        if desc.get("arm_kind") != "w":
            continue
        if task_ids is not None and desc["id"] not in task_ids:
            continue
        out.append(desc)
    return out


def reference_for(desc: dict, tasks_dir: Path) -> str:
    return (tasks_dir / f"{desc['id']}.ward0").read_text(encoding="utf-8")


def dafny_reference_for(desc: dict, tasks_dir: Path) -> str:
    return (tasks_dir / f"{desc['id']}.dfy").read_text(encoding="utf-8")


def module_tier(desc: dict, arm: str) -> str:
    """The module's verification tier: the entry function's declared tier, or
    Proven when tiers don't apply (W-tiers arm; D arm is the raw-Dafny baseline
    and always verifies fully, per the design doc's arms table)."""
    if arm in ("W-tiers", "D"):
        return "Proven"
    entry = desc["fn"]
    return desc.get("tiers", {}).get(entry, "Proven")


def make_w_prompt(desc: dict, reference_source: str, enforce: bool) -> str:
    """Build the ward0 prompt for a w-task (externs prepended by the harness)."""
    parts = []
    for stub in desc["externs"]:
        params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
        sig = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
        if stub.get("contract"):
            sig += "\n  " + stub["contract"]
        parts.append(sig + ";")
    stub_text = "\n".join(parts)
    sig_line = next(
        (l.strip() for l in reference_source.splitlines() if l.strip().startswith("fn ")),
        None,
    )
    lines = [
        "Implement the following function in ward0.",
        "",
        f"Signature: {sig_line}",
        "",
        "The harness provides these library stubs. You write exactly one fn "
        "definition; the harness prepends the stub declarations itself, so do "
        "not repeat them in your answer:",
        stub_text,
        "",
        "The stubs above carry their full contracts; the verifier treats them "
        "as axioms, so you can prove properties of calls to them. Note: a "
        "contract is a claim about the stub, and its real runtime behavior may "
        "differ from it.",
        "",
        'If a call\'s result contradicts the documented behavior, the caller must return Err("contract violation").',
        "",
        f"Specification:\n{desc['spec']}",
        "",
    ]
    if enforce:
        lines += [
            "The toolchain wraps every stub call in an auto-generated runtime "
            'check: if the stub\'s actual result contradicts the contract above, the '
            'call itself returns Err("contract violation") instead of the stub\'s '
            "value. Treat that as the true result — your function MUST NOT second-"
            "guess, re-label, or fabricate error strings based on inspecting the "
            "result; simply return the result you received. You still verify the "
            "contract of the result you return, but you never re-derive the "
            "stub's outcome yourself.",
        ]
    lines += [
        "Write the ward0 source of the function, including requires/ensures "
        "contracts that Dafny can prove. Only code, no markdown fences, no "
        "explanation.",
    ]
    return "\n".join(lines)


def make_w_prompt_dafny(desc: dict) -> str:
    """Build the raw-Dafny prompt for a w-task (D arm)."""
    return (
        "Implement the following function in Dafny. The harness provides the "
        "Result datatype and these extern declarations (as axioms); do NOT "
        "repeat them in your answer. The externs' contracts are claims about "
        "real libraries whose runtime behavior may differ.\n\n"
        f"Externs provided by the harness:\n{desc['extern_dafny']}\n\n"
        f"Specification:\n{desc['spec']}\n\n"
        "Write the Dafny source of the method, including requires/ensures "
        "contracts that `dafny verify` can prove. Only code, no markdown "
        "fences, no explanation."
    )


def _mark_entry(entry: dict, markers: list[str], violation_idx: list[int]) -> None:
    results = [m == "PASS" for m in markers]
    entry["markers"] = markers
    passed = all(results)
    entry.update(
        status="pass" if passed else "test_fail",
        tests_passed=sum(results),
        tests_total=len(results),
    )
    entry["violation_passed"] = sum(results[i] for i in violation_idx)
    entry["boundary_escape"] = sum(1 for i in violation_idx if markers[i] == "OKLEAK")


def _run_tests_no_verify(runner, task_desc, candidate, arm, entry, violation_idx) -> None:
    """Run hidden tests with --no-verify (Contracted fallback / Tested tier)."""
    entry["verified"] = False
    try:
        if arm == "D":
            markers = runner.run_hidden_tests_dafny_b_marked(task_desc, candidate, no_verify=True)
        else:
            markers = runner.run_hidden_tests_b_marked(task_desc, candidate, "trust", enforce=(arm != "W-enforce"), no_verify=True)
    except Exception as exc:
        entry.update(status="test_run_error", detail=str(exc))
    else:
        _mark_entry(entry, markers, violation_idx)


def evaluate_w_task(
    runner: DafnyRunner,
    model,
    task_desc: dict,
    arm: str,
    tasks_dir: Path,
    attempts: int,
) -> WTaskResult:
    tier = module_tier(task_desc, arm)
    res = WTaskResult(task_id=task_desc["id"], arm=arm, tier=tier)
    violation_idx = [i for i, c in enumerate(task_desc["hidden_tests"]) if c.get("violation")]
    res.escape_total = len(violation_idx)

    if arm == "D":
        prompt = make_w_prompt_dafny(task_desc)
    else:
        prompt = make_w_prompt(task_desc, reference_for(task_desc, tasks_dir), enforce=(arm != "W-enforce"))

    t0 = time.monotonic()
    for attempt in range(1, attempts + 1):
        entry: dict = {"attempt": attempt}
        at0 = time.monotonic()
        try:
            candidate = model.generate(prompt, task_desc["id"], attempt)
        except Exception as exc:
            entry.update(status="model_error", detail=str(exc))
        else:
            if arm == "D":
                # Strip fences + leading `dafny` echo noise the model emits
                # (Phase-0 evaluate_b.py did this; the Phase-1 port missed it —
                # observed 2026-08-01: w3 D-arm failed with 'dafny' landing at
                # task.dfy(12,0) parse error after the harness's extern preamble).
                candidate = clean_dafny(candidate)
            entry["candidate_length"] = len(candidate)
            entry["generated"] = candidate
            entry["generate_s"] = round(time.monotonic() - at0, 1)
            # Transpile errors are their own status (A2 taxonomy) for every
            # ward0 arm (W, W-enforce, W-tiers) — including Tested tier — so
            # catch them explicitly before any verification or test run, the
            # same way evaluate_b.py does. (Raw-Dafny arm has no transpile step.)
            if arm != "D":
                try:
                    runner.transpile(candidate, enforce=(arm != "W-enforce"))
                except Exception as exc:
                    entry.update(status="transpile_error", detail=str(exc))
                    entry["attempt_s"] = round(time.monotonic() - at0, 1)
                    res.attempts.append(entry)
                    continue
            if tier == "Tested":
                # No proof obligation: never blocks on verification. Straight to
                # hidden tests compiled with --no-verify (generated wrappers still
                # enforce at runtime). NOTE: verify_s stays 0 for Tested.
                _run_tests_no_verify(runner, task_desc, candidate, arm, entry, violation_idx)
            else:
                try:
                    if arm == "D":
                        src = task_desc["extern_dafny"] + "\n\n" + candidate
                        ok, detail = runner.verify_dafny(src, verify_limit=TIER_VERIFY_LIMIT[tier])
                    else:
                        ok, detail = runner.verify_b(candidate, task_desc, "trust", enforce=(arm != "W-enforce"), verify_limit=TIER_VERIFY_LIMIT[tier])
                    entry["verify_s"] = round(time.monotonic() - at0 - entry.get("generate_s", 0), 1)
                except Exception as exc:
                    entry.update(status="verify_run_error", detail=str(exc))
                    ok = None
                else:
                    # verify_s is total verification effort across attempts (C3b).
                    res.verify_s += entry["verify_s"]
                    if not ok:
                        if tier == "Contracted":
                            # bounded proof search failed -> fall back to tests
                            entry["tier_fallback"] = True
                            res.tier_fallback = True
                            entry["verify_detail"] = detail.strip()[:500]
                            _run_tests_no_verify(runner, task_desc, candidate, arm, entry, violation_idx)
                        else:
                            entry.update(status="verify_fail", detail=detail.strip()[:2000])
                    else:
                        entry["verified"] = True
                        try:
                            if arm == "D":
                                markers = runner.run_hidden_tests_dafny_b_marked(task_desc, candidate)
                            else:
                                markers = runner.run_hidden_tests_b_marked(task_desc, candidate, "trust", enforce=(arm != "W-enforce"))
                        except Exception as exc:
                            entry.update(status="test_run_error", detail=str(exc))
                        else:
                            _mark_entry(entry, markers, violation_idx)
        entry["attempt_s"] = round(time.monotonic() - at0, 1)
        res.attempts.append(entry)
        if entry.get("status") == "pass":
            res.solved = True
            res.solved_at = attempt
            res.escape_passed = len(violation_idx)
            res.boundary_escape = 0
        elif entry.get("status") == "test_fail":
            res.escape_passed = entry.get("violation_passed", 0)
            res.boundary_escape = entry.get("boundary_escape", 0)
        if res.solved:
            break
    res.total_seconds = time.monotonic() - t0
    return res


def summarize(results: list[WTaskResult]) -> str:
    lines = []
    for arm in ARMS:
        arm_r = [r for r in results if r.arm == arm]
        if not arm_r:
            continue
        solved = sum(r.solved for r in arm_r)
        leaks = sum(r.boundary_escape for r in arm_r)
        esc_t = sum(r.escape_total for r in arm_r)
        esc_p = sum(r.escape_passed for r in arm_r)
        verify = sum(r.verify_s for r in arm_r)
        fb = sum(1 for r in arm_r if r.tier_fallback)
        lines.append(
            f"arm {arm}: solved {solved}/{len(arm_r)} | boundary_leaks {leaks}/{esc_t} "
            f"| escapes {esc_t - esc_p}/{esc_t} | verify_s {verify:.0f} | fallbacks {fb}"
        )
    w = [r for r in results if r.arm == "W"]
    d = [r for r in results if r.arm == "D"]
    if w and d:
        lines.append(f"C3a: W solved {sum(r.solved for r in w)} vs D solved {sum(r.solved for r in d)}")
        lines.append(f"C3b: W verify_s {sum(r.verify_s for r in w):.0f} vs D verify_s {sum(r.verify_s for r in d):.0f}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Phase-1 w-task evaluator (four arms)")
    parser.add_argument("--tasks-dir", type=Path, default=Path("benchmarks/w_tasks"))
    parser.add_argument("--model", choices=["fake", "opencode"], default="fake")
    parser.add_argument("--model-name", default="opencode/deepseek-v4-flash-free")
    parser.add_argument("--arm", choices=list(ARMS), default=None, help="only this arm (default: all)")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--task", action="append", help="only these task ids (repeatable)")
    parser.add_argument("--out", type=Path, default=None, help="JSONL output path")
    args = parser.parse_args(argv)

    runner = DafnyRunner()
    tasks = load_w_tasks(args.tasks_dir, set(args.task) if args.task else None)
    if not tasks:
        print("no w-tasks found")
        return
    for desc in tasks:
        problems = runner.check_scenario_sanity(desc)
        if problems:
            print(f"SCENARIO SANITY FAILED for {desc['id']}:")
            for p in problems:
                print(f"  {p}")
            return
    print(f"scenario sanity OK for {len(tasks)} w-tasks")

    arms = [args.arm] if args.arm else list(ARMS)
    results: list[WTaskResult] = []
    for arm in arms:
        if args.model == "opencode":
            guide = DAFNY_GUIDE if arm == "D" else WARD0_GUIDE
            model = OpenCodeModel(model=args.model_name, guide=guide)
        else:
            if arm == "D":
                sources = {d["id"]: d["reference_dafny_caller"] for d in tasks}
            else:
                sources = {d["id"]: reference_for(d, args.tasks_dir) for d in tasks}
            model = FakeModel(sources)
        for i, desc in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {desc['id']} (arm {arm}, tier {module_tier(desc, arm)})...", flush=True)
            res = evaluate_w_task(runner, model, desc, arm, args.tasks_dir, args.attempts)
            results.append(res)
            status = res.attempts[-1]["status"] if res.attempts else "no_attempts"
            print(
                f"    -> {'SOLVED' if res.solved else 'not solved'} ({status}, {res.total_seconds:.0f}s)",
                flush=True,
            )
            if args.out:
                with args.out.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(res.to_dict()) + "\n")
    print(summarize(results))
    return results


if __name__ == "__main__":
    main()
