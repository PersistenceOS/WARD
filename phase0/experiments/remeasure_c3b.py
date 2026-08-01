"""Clean C3b re-read: re-measure verify effort on the ACTUAL W/D candidates.

The original full-8 run recorded W verify_s totals by summing per-attempt wall
time (w4's 2.5s = failed a1 0.8s + pass a2 1.7s) on a loaded machine. This
script re-measures all 8 w-tasks with an interleaved, median-of-3 protocol on
the SAME passing candidates from the JSONL, so W vs D are compared under the
same machine conditions:

  W+ : W-arm pass candidate, transpiled enforce=True  (what W actually verified)
  W- : same candidate, enforce=False                  (wrapper delta on real code)
  D  : D-arm pass candidate, raw (extern_dafny preamble)

Report: per-task medians, totals, and the C3b ratio (total and median) vs the
pre-registered <= 0.7 gate.
"""

import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.dafny_runner import DafnyRunner
from transpiler.transpiler import Ward0Transpiler

TASKS_DIR = Path("benchmarks/w_tasks")
W_JSONL = Path("experiments/runs/phase1_W_full8.jsonl")
D_JSONL = Path("experiments/runs/phase1_D_arm.jsonl")
VERIFY_LIMIT = 60
RUNS = 3


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def extern_ward0(desc: dict) -> str:
    parts = []
    for stub in desc["externs"]:
        params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
        sig = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
        if stub.get("contract"):
            sig += "\n  " + stub["contract"]
        parts.append(sig + ";")
    return "\n\n".join(parts)


def pass_candidate(row: dict) -> str:
    for a in row["attempts"]:
        if a.get("status") == "pass" and "generated" in a:
            return a["generated"]
    return row["attempts"][-1]["generated"]


def time_verify(dafny_src: str, dafny: str, z3: str | None) -> float:
    cmd = [dafny, "verify", f"--verification-time-limit:{VERIFY_LIMIT}"]
    if z3:
        cmd += ["--solver-path", z3]
    with tempfile.TemporaryDirectory() as td:
        dfy = Path(td) / "task.dfy"
        dfy.write_text(dafny_src, encoding="utf-8")
        t0 = time.perf_counter()
        proc = subprocess.run(cmd + [str(dfy)], capture_output=True, text=True, timeout=VERIFY_LIMIT + 30)
        dt = time.perf_counter() - t0
        if proc.returncode != 0:
            raise RuntimeError(f"verify failed for a candidate:\n{(proc.stdout + proc.stderr)[-500:]}")
    return dt


def main() -> None:
    runner = DafnyRunner()
    transp = Ward0Transpiler()
    w_rows = {r["task_id"]: r for r in load_jsonl(W_JSONL)}
    d_rows = {r["task_id"]: r for r in load_jsonl(D_JSONL)}
    task_ids = sorted(w_rows.keys())
    print(f"dafny={runner.dafny}  tasks={len(task_ids)}  runs={RUNS} (interleaved)")
    print()

    w_on, w_off, d = {}, {}, {}
    for tid in task_ids:
        desc = json.loads((TASKS_DIR / f"{tid}.json").read_text(encoding="utf-8"))
        extern = extern_ward0(desc)
        w_cand = pass_candidate(w_rows[tid])
        d_cand = pass_candidate(d_rows[tid])
        transp.enforce_boundary = True
        src_w_on = transp.transpile(extern + "\n\n" + w_cand)
        transp.enforce_boundary = False
        src_w_off = transp.transpile(extern + "\n\n" + w_cand)
        src_d = desc["extern_dafny"] + "\n\n" + d_cand

        # interleave the three variants across RUNS rounds to cancel drift
        ts = {"W+": [], "W-": [], "D": []}
        for r in range(RUNS):
            for label, src in (("W+", src_w_on), ("W-", src_w_off), ("D", src_d)):
                ts[label].append(time_verify(src, runner.dafny, runner.z3))
        w_on[tid] = statistics.median(ts["W+"])
        w_off[tid] = statistics.median(ts["W-"])
        d[tid] = statistics.median(ts["D"])
        print(
            f"  {tid:28s} W+ {w_on[tid]:.2f}s  W- {w_off[tid]:.2f}s  "
            f"D {d[tid]:.2f}s  wrap-delta {w_on[tid] - w_off[tid]:+.2f}s  W+/D {(w_on[tid] / d[tid] if d[tid] else 0):.2f}"
        )

    w_on_tot = sum(w_on.values())
    w_off_tot = sum(w_off.values())
    d_tot = sum(d.values())
    wv = list(w_on.values())
    dv = list(d.values())
    print()
    print(f"totals:  W+ {w_on_tot:.1f}s  W- {w_off_tot:.1f}s  D {d_tot:.1f}s")
    print(f"C3b total ratio   W+/D: {w_on_tot / d_tot:.2f}   (gate <= 0.7)")
    print(f"C3b median ratio  W+/D: {statistics.median(wv) / statistics.median(dv):.2f}   (gate <= 0.7)")
    print(f"wrapper overhead across 8 tasks (W+ minus W-): {w_on_tot - w_off_tot:.1f}s total, "
          f"{((w_on_tot / w_off_tot) - 1) * 100:.0f}%")
    print()
    print("per-task W+ vs D (W+ faster than D on):",
          [t for t in task_ids if w_on[t] < d[t]])
    print("per-task W+ vs D (D faster than W+ on):",
          [t for t in task_ids if d[t] < w_on[t]])


if __name__ == "__main__":
    main()
