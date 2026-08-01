"""Experiment B runner: contract-stub trust boundary.

Two arms differ ONLY in the stub contract:
  - baseline: the stub declaration carries no contract (verifier knows nothing);
  - trust:    the stub declaration carries its full contract (treated as an axiom).

Metrics (pre-registered):
  B1 = relative reduction in violation-escape rate (trust vs baseline) on buggy
       scenarios. A violation escapes when the caller's output fails the hidden
       test for a case where the stub contradicts its contract.
  B2 = false positives: fraction of valid (reference) callers rejected per arm
       (verify fail or test fail); gate: <= 15%.

The oracle run (--model fake) must show 0 escapes and 0 rejections in both arms.
"""

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from harness.dafny_runner import DafnyRunner
from harness.models import FakeModel, OpenCodeModel, WARD0_GUIDE, make_b_prompt

ARMS = ("baseline", "trust")


@dataclass
class BTaskResult:
    task_id: str
    arm: str
    buggy: bool
    attempts: list[dict] = field(default_factory=list)
    solved: bool = False
    escape_total: int = 0
    escape_passed: int = 0
    boundary_escape: int = 0
    total_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "arm": self.arm,
            "buggy": self.buggy,
            "solved": self.solved,
            "escape_total": self.escape_total,
            "escape_passed": self.escape_passed,
            "boundary_escape": self.boundary_escape,
            "attempts": self.attempts,
            "seconds": round(self.total_seconds, 2),
        }


def load_b_tasks(tasks_dir: Path, task_ids: set[str] | None = None) -> list[dict]:
    out = []
    for jpath in sorted(tasks_dir.glob("*.json")):
        desc = json.loads(jpath.read_text(encoding="utf-8"))
        if task_ids is not None and desc["id"] not in task_ids:
            continue
        out.append(desc)
    return out


def reference_for(desc: dict, arm: str, tasks_dir: Path) -> str:
    suffix = "" if arm == "trust" else "_base"
    return (tasks_dir / f"{desc['id']}{suffix}.ward0").read_text(encoding="utf-8")


def evaluate_b_task(
    runner: DafnyRunner,
    model,
    task_desc: dict,
    arm: str,
    tasks_dir: Path,
    attempts: int,
    enforce: bool = False,
) -> BTaskResult:
    reference_source = reference_for(task_desc, arm, tasks_dir)
    res = BTaskResult(task_id=task_desc["id"], arm=arm, buggy=bool(task_desc.get("buggy", False)))
    prompt = make_b_prompt(task_desc, reference_source, arm, enforce=enforce)
    violation_idx = [i for i, c in enumerate(task_desc["hidden_tests"]) if c.get("violation")]
    res.escape_total = len(violation_idx)
    t0 = time.monotonic()
    for attempt in range(1, attempts + 1):
        entry: dict = {"attempt": attempt}
        at0 = time.monotonic()
        try:
            candidate = model.generate(prompt, task_desc["id"], attempt)
        except Exception as exc:
            entry.update(status="model_error", detail=str(exc))
        else:
            entry["candidate_length"] = len(candidate)
            entry["generated"] = candidate
            entry["generate_s"] = round(time.monotonic() - at0, 1)
            try:
                runner.transpile(candidate, enforce=enforce)
            except Exception as exc:
                entry.update(status="transpile_error", detail=str(exc))
            else:
                ok, detail = runner.verify_b(candidate, task_desc, arm, enforce=enforce)
                if not ok:
                    entry.update(status="verify_fail", detail=detail.strip()[:2000])
                else:
                    entry["verified"] = True
                    try:
                        markers = runner.run_hidden_tests_b_marked(task_desc, candidate, arm, enforce=enforce)
                    except Exception as exc:
                        entry.update(status="test_run_error", detail=str(exc))
                    else:
                        results = [m == "PASS" for m in markers]
                        entry["markers"] = markers
                        passed = all(results)
                        entry.update(
                            status="pass" if passed else "test_fail",
                            tests_passed=sum(results),
                            tests_total=len(results),
                        )
                        entry["violation_passed"] = sum(results[i] for i in violation_idx)
                        entry["boundary_escape"] = sum(
                            1 for i in violation_idx if markers[i] == "OKLEAK"
                        )
                        if passed:
                            res.solved = True
                            res.solved_at = attempt
                            res.escape_passed = len(violation_idx)
                            res.boundary_escape = 0
                        else:
                            res.escape_passed = sum(results[i] for i in violation_idx)
                            res.boundary_escape = entry["boundary_escape"]
        entry["attempt_s"] = round(time.monotonic() - at0, 1)
        res.attempts.append(entry)
        if res.solved:
            break
    res.total_seconds = time.monotonic() - t0
    return res


def summarize_b(results: list[BTaskResult]) -> str:
    lines = []
    arms_present = sorted({r.arm for r in results})
    for arm in arms_present:
        arm_r = [r for r in results if r.arm == arm]
        solved = [r for r in arm_r if r.solved]
        lines.append(f"arm {arm}: tasks {len(arm_r)} solved {len(solved)}")
        for r in arm_r:
            status = r.attempts[-1]["status"] if r.attempts else "no_attempts"
            lines.append(
                f"  {r.task_id} buggy={r.buggy} {status} "
                f"escape {r.escape_total - r.escape_passed}/{r.escape_total} "
                f"boundary_okleak {r.boundary_escape}/{r.escape_total}"
            )
    if arms_present == list(ARMS):
        base = [r for r in results if r.arm == "baseline" and r.buggy]
        trust = [r for r in results if r.arm == "trust" and r.buggy]
        b_total = sum(r.escape_total for r in base)
        b_esc = sum(r.escape_total - r.escape_passed for r in base)
        t_total = sum(r.escape_total for r in trust)
        t_esc = sum(r.escape_total - r.escape_passed for r in trust)
        b_leak = sum(r.boundary_escape for r in base)
        t_leak = sum(r.boundary_escape for r in trust)
        lines.append(f"B1 violation escapes: baseline {b_esc}/{b_total} trust {t_esc}/{t_total}")
        if b_esc > 0:
            lines.append(f"B1 relative reduction: {(b_esc - t_esc) / b_esc:.3f}")
        else:
            lines.append("B1 relative reduction: undefined (baseline escape = 0)")
        lines.append(f"B1 boundary Ok-leaks: baseline {b_leak}/{b_total} trust {t_leak}/{t_total}")
        if b_leak > 0:
            lines.append(f"B1 boundary-leak reduction: {(b_leak - t_leak) / b_leak:.3f}")
        else:
            lines.append("B1 boundary-leak reduction: undefined (baseline leak = 0)")
    for arm in arms_present:
        arm_r = [r for r in results if r.arm == arm]
        rejected = [r for r in arm_r if not r.solved]
        lines.append(f"B2 rejected {arm}: {len(rejected)}/{len(arm_r)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="experiment B: contract-stub trust boundary")
    parser.add_argument("--tasks-dir", type=Path, default=Path("benchmarks/b_tasks"))
    parser.add_argument("--model", choices=["fake", "opencode"], default="fake")
    parser.add_argument("--arm", choices=list(ARMS), default="trust")
    parser.add_argument("--model-name", default="opencode/deepseek-v4-flash-free")
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--task", action="append", help="only these task ids (repeatable)")
    parser.add_argument("--enforce", action="store_true", help="generate the contract-check wrapper around extern calls")
    parser.add_argument("--out", type=Path, default=None, help="JSONL output path")
    args = parser.parse_args(argv)

    runner = DafnyRunner()
    tasks = load_b_tasks(args.tasks_dir, set(args.task) if args.task else None)
    for desc in tasks:
        problems = runner.check_scenario_sanity(desc)
        if problems:
            print(f"SCENARIO SANITY FAILED for {desc['id']}:")
            for p in problems:
                print(f"  {p}")
            return

    if args.model == "opencode":
        model = OpenCodeModel(model=args.model_name, guide=WARD0_GUIDE)
    else:
        sources = {
            desc["id"]: reference_for(desc, args.arm, args.tasks_dir) for desc in tasks
        }
        model = FakeModel(sources)

    results: list[BTaskResult] = []
    run_t0 = time.monotonic()
    for i, desc in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {desc['id']} (arm {args.arm})...", flush=True)
        res = evaluate_b_task(runner, model, desc, args.arm, args.tasks_dir, args.attempts, enforce=args.enforce)
        results.append(res)
        elapsed = time.monotonic() - run_t0
        avg = elapsed / i
        remaining = avg * (len(tasks) - i)
        status = res.attempts[-1]["status"] if res.attempts else "no_attempts"
        print(
            f"    -> {'SOLVED' if res.solved else 'not solved'} ({status}, {res.total_seconds:.0f}s) "
            f"| elapsed {elapsed:.0f}s, avg {avg:.0f}s/task, ETA {remaining:.0f}s",
            flush=True,
        )
        if args.out:
            with args.out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(res.to_dict()) + "\n")
    print(summarize_b(results))
    return results


if __name__ == "__main__":
    main()
