"""Experiment runner: generate -> transpile -> dafny verify -> hidden tests."""

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from harness.dafny_runner import DafnyRunner
from harness.models import (
    ApiModel,
    DAFNY_GUIDE,
    FakeModel,
    OpenCodeModel,
    WARD0_GUIDE,
    clean_dafny,
    make_prompt,
    make_raw_prompt,
)

STATUSES = ("transpile_error", "verify_fail", "test_fail", "pass")


@dataclass
class TaskResult:
    task_id: str
    tier: int
    holdout: bool
    attempts: list[dict] = field(default_factory=list)
    solved: bool = False
    solved_at: int | None = None
    total_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "tier": self.tier,
            "holdout": self.holdout,
            "solved": self.solved,
            "solved_at": self.solved_at,
            "attempts": self.attempts,
            "seconds": round(self.total_seconds, 2),
        }


def load_tasks(
    tasks_dir: Path,
    tiers: set[int] | None,
    include_holdout: bool,
    task_ids: set[str] | None = None,
) -> list[tuple[dict, str]]:
    """Return (task_desc, reference_source) pairs, sorted by id."""
    out = []
    for jpath in sorted(tasks_dir.glob("*.json")):
        desc = json.loads(jpath.read_text(encoding="utf-8"))
        if task_ids is not None and desc["id"] not in task_ids:
            continue
        if tiers is not None and desc["tier"] not in tiers:
            continue
        if desc.get("holdout") and not include_holdout:
            continue
        src = (tasks_dir / f"{desc['id']}.ward0").read_text(encoding="utf-8")
        out.append((desc, src))
    return out


def evaluate_task(
    runner: DafnyRunner,
    model,
    task_desc: dict,
    reference_source: str,
    attempts: int,
    arm: str = "ward0",
) -> TaskResult:
    res = TaskResult(task_id=task_desc["id"], tier=task_desc["tier"], holdout=task_desc.get("holdout", False))
    raw = arm == "raw-dafny"
    prompt = make_raw_prompt(task_desc, reference_source) if raw else make_prompt(task_desc, reference_source)
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
            if raw:
                candidate = clean_dafny(candidate)
                ok, detail = runner.verify_dafny(candidate)
                if not ok:
                    status = "parse_error" if "parse error" in detail.lower() or "syntax" in detail.lower() else "verify_fail"
                    entry.update(status=status, detail=detail.strip()[:2000])
                else:
                    entry["verified"] = True
                    try:
                        results = runner.run_hidden_tests_dafny(task_desc, candidate)
                    except Exception as exc:
                        entry.update(status="test_run_error", detail=str(exc))
                    else:
                        passed = all(results)
                        entry.update(
                            status="pass" if passed else "test_fail",
                            tests_passed=sum(results),
                            tests_total=len(results),
                        )
                        if passed:
                            res.solved = True
                            res.solved_at = attempt
            else:
                try:
                    runner.transpile(candidate)
                except Exception as exc:
                    entry.update(status="transpile_error", detail=str(exc))
                else:
                    ok, detail = runner.verify(candidate)
                    if not ok:
                        entry.update(status="verify_fail", detail=detail.strip()[:2000])
                    else:
                        entry["verified"] = True
                        try:
                            results = runner.run_hidden_tests(task_desc, candidate)
                        except Exception as exc:
                            entry.update(status="test_run_error", detail=str(exc))
                        else:
                            passed = all(results)
                            entry.update(
                                status="pass" if passed else "test_fail",
                                tests_passed=sum(results),
                                tests_total=len(results),
                            )
                            if passed:
                                res.solved = True
                                res.solved_at = attempt
        entry["attempt_s"] = round(time.monotonic() - at0, 1)
        res.attempts.append(entry)
        if res.solved:
            break
    res.total_seconds = time.monotonic() - t0
    return res


def summarize(results: list[TaskResult], n_attempts: int) -> str:
    solved = [r for r in results if r.solved]
    lines = [
        f"tasks: {len(results)}  solved: {len(solved)}  pass@{n_attempts}: {len(solved) / max(len(results), 1):.3f}",
    ]
    for tier in sorted({r.tier for r in results}):
        rs = [r for r in results if r.tier == tier]
        sv = [r for r in rs if r.solved]
        lines.append(f"  tier {tier}: {len(sv)}/{len(rs)} solved")
    for k in range(1, n_attempts + 1):
        sv = [r for r in results if r.solved_at is not None and r.solved_at <= k]
        lines.append(f"  pass@{k}: {len(sv)}")
    from collections import Counter

    counts = Counter()
    for r in results:
        for a in r.attempts:
            counts[a["status"]] += 1
    lines.append(f"  attempt outcomes: {dict(counts)}")
    return "\n".join(lines)


def run_experiment(
    runner: DafnyRunner,
    model,
    tasks_dir: Path,
    attempts: int,
    tiers: set[int] | None = None,
    include_holdout: bool = False,
    out_path: Path | None = None,
    limit: int | None = None,
    task_ids: set[str] | None = None,
    arm: str = "ward0",
) -> list[TaskResult]:
    tasks = load_tasks(tasks_dir, tiers, include_holdout, task_ids)
    if limit:
        tasks = tasks[:limit]
    results = []
    run_t0 = time.monotonic()
    for i, (desc, src) in enumerate(tasks, 1):
        print(f"[{i}/{len(tasks)}] {desc['id']} (tier {desc['tier']})...", flush=True)
        res = evaluate_task(runner, model, desc, src, attempts, arm=arm)
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
        if out_path:
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(res.to_dict()) + "\n")
    print(summarize(results, attempts))
    return results


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="ward0 evaluation: generate -> transpile -> verify -> hidden tests")
    parser.add_argument("--tasks-dir", type=Path, default=Path("benchmarks/tasks"))
    parser.add_argument("--model", choices=["fake", "api", "opencode"], default="fake")
    parser.add_argument("--arm", choices=["ward0", "raw-dafny"], default="ward0")
    parser.add_argument("--base-url", default="http://localhost:8080/v1")
    parser.add_argument("--model-name", default="local")
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--attempts", type=int, default=5)
    parser.add_argument("--tier", type=int, action="append", help="only these tiers (repeatable)")
    parser.add_argument("--task", action="append", help="only these task ids (repeatable)")
    parser.add_argument("--max-tokens", type=int, default=2048, help="cap on generated tokens per attempt")
    parser.add_argument("--holdout", action="store_true", help="include holdout tasks")
    parser.add_argument("--limit", type=int, default=None, help="only first N tasks (sorted by id)")
    parser.add_argument("--out", type=Path, default=None, help="JSONL output path")
    args = parser.parse_args(argv)

    runner = DafnyRunner()
    if args.model == "api":
        model = ApiModel(base_url=args.base_url, model=args.model_name, api_key=args.api_key, max_tokens=args.max_tokens)
    elif args.model == "opencode":
        model = OpenCodeModel(model=args.model_name, guide=DAFNY_GUIDE if args.arm == "raw-dafny" else WARD0_GUIDE)
    else:
        sources = {d["id"]: (Path(args.tasks_dir) / f"{d['id']}.ward0").read_text(encoding="utf-8") for d in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(args.tasks_dir.glob("*.json")))}
        model = FakeModel(sources)

    tiers = set(args.tier) if args.tier else None
    run_experiment(
        runner,
        model,
        args.tasks_dir,
        attempts=args.attempts,
        tiers=tiers,
        include_holdout=args.holdout,
        out_path=args.out,
        limit=args.limit,
        task_ids=set(args.task) if args.task else None,
        arm=args.arm,
    )


if __name__ == "__main__":
    main()
