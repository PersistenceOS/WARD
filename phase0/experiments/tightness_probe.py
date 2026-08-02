"""Specification Tightness (tau) probe — theory instrument I1 (pre-registered
in files/ward-phase2-scoping.md section 10).

Uses the canonical engine wardcore.tightness. Covers the full corpus:
62 Phase-0 t-tasks (benchmarks/tasks/) + 12 w-tasks (benchmarks/w_tasks/),
plus a deliberately vacuous control (ensures true) that must score tau ~ 0.

Definition (bounded-domain):
    tau(x) = 1 - log2(|Y_perm(x)|) / log2(|Y|)        for admissible x (P(x) true)
    tau    = E_{x ~ P}[tau(x)]

tau = 1: the contract pins the output completely (maximally anti-slop).
tau = 0: `ensures true` -- zero output entropy constrained (vacuous).

Usage:
    python experiments/tightness_probe.py [--tasks w1,t1_abs] [--no-control]
"""

import argparse
import json
import re
import statistics
import sys
from pathlib import Path

# allow running as a plain script from experiments/ while importing wardcore
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wardcore.tightness import compute_tightness, parse_ward0_fns

TASKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "tasks"
WTASKS_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "w_tasks"


def _find(task_id):
    for d in (TASKS_DIR, WTASKS_DIR):
        j, w = d / f"{task_id}.json", d / f"{task_id}.ward0"
        if w.exists():
            desc = json.loads(j.read_text(encoding="utf-8")) if j.exists() else {}
            return w.read_text(encoding="utf-8"), desc, d.name
    return None, None, None


def probe_task(task_id):
    src, desc, dname = _find(task_id)
    if src is None:
        return {"task": task_id, "corpus": "?", "tier": "?", "fn": "?",
                "ret": "?", "status": "missing"}
    fn = _extract_fn(src, desc.get("fn"))
    if fn is None:
        return {"task": task_id, "corpus": dname, "tier": "?", "fn": "?",
                "ret": "?", "status": "no fn found"}
    row = {"task": task_id, "corpus": dname,
           "tier": desc.get("tiers", {}).get(fn["name"], "?"),
           "fn": fn["name"], "ret": fn["ret"]}
    r = compute_tightness(fn["params"], fn["ret"], fn["requires"], fn["ensures"])
    row.update({k: v for k, v in r.items()})
    return row


def _extract_fn(src, want_name=None):
    fns = parse_ward0_fns(src)
    if want_name is not None:
        for fn in fns:
            if fn["name"] == want_name:
                return fn
    return fns[0] if fns else None


def vacuous_control():
    """A deliberately vacuous spec: ensures true must score tau ~ 0."""
    return compute_tightness([("x", "int")], "int", [], ["true"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=None)
    ap.add_argument("--no-control", action="store_true")
    args = ap.parse_args()

    if args.tasks:
        tasks = [t.strip() for t in args.tasks.split(",")]
    else:
        t1 = sorted(p.name.split(".")[0] for p in TASKS_DIR.glob("t*.ward0"))
        w1 = sorted((p.name.split(".")[0] for p in WTASKS_DIR.glob("w*.ward0")),
                    key=lambda t: int(re.search(r"\d+", t).group()))
        tasks = t1 + w1

    rows = [probe_task(t) for t in tasks]

    print(f"{'task':26} {'corp':5} {'tier':10} {'ret':16} {'|Y|':4} {'adm':5} {'tau':7} status")
    print("-" * 84)
    for r in rows:
        tau = f"{r['tau']:.3f}" if r.get("tau") is not None else "  -  "
        y = r["|Y|"] if r.get("|Y|") is not None else "-"
        adm = r["admissible"] if r.get("admissible") is not None else "-"
        print(f"{r['task']:26} {r.get('corpus','')[:5]:5} {str(r['tier']):10} "
              f"{r['ret']:16} {y:>4} {adm:>5} "
              f"{tau:>7} {r.get('status','')}")

    if not args.no_control:
        c = vacuous_control()
        print(f"\nVACUOUS CONTROL (ensures true): tau = {c['tau']}, "
              f"status = {c['status']}  (must be ~0.0 to prove the instrument separates)")

    ok = [r for r in rows if r.get("status") == "ok" and r["tau"] is not None]
    if ok:
        taus = [r["tau"] for r in ok]
        print(f"\n{len(taus)}/{len(rows)} tasks measured; "
              f"mean tau = {statistics.mean(taus):.3f}, "
              f"median = {statistics.median(taus):.3f}, "
              f"min = {min(taus):.3f}, max = {max(taus):.3f}")
        print(f"distribution: < 0.5: {sum(1 for t in taus if t < 0.5)}, "
              f"0.5-0.8: {sum(1 for t in taus if 0.5 <= t < 0.8)}, "
              f">= 0.8: {sum(1 for t in taus if t >= 0.8)}")


if __name__ == "__main__":
    main()
