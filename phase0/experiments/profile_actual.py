"""Profile the ACTUAL W-arm candidates from phase1_W_full8.jsonl vs D-arm candidates.

The reference-based isolation showed the wrapper alone costs only ~0.15s. But
the live run showed W verify 1.8-2.5s vs D 1.4-1.5s on w4/w5/w7. This script
extracts the real model-written candidates per arm and times:

  - W candidate transpiled enforce=True   (what the W arm actually verified)
  - W candidate transpiled enforce=False  (wrapper isolated on real output)
  - D candidate raw                       (what the D arm actually verified)
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

TASKS = ["w4_order_placement", "w5_currency_roundtrip", "w7_idempotency"]
TASKS_DIR = Path("benchmarks/w_tasks")
VERIFY_LIMIT = 60


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


def time_verify(dafny_src: str, dafny: str, z3: str | None, runs: int = 3) -> tuple[float, bool]:
    cmd = [dafny, "verify", f"--verification-time-limit:{VERIFY_LIMIT}"]
    if z3:
        cmd += ["--solver-path", z3]
    ts = []
    ok = False
    for _ in range(runs):
        with tempfile.TemporaryDirectory() as td:
            dfy = Path(td) / "task.dfy"
            dfy.write_text(dafny_src, encoding="utf-8")
            t0 = time.perf_counter()
            proc = subprocess.run(cmd + [str(dfy)], capture_output=True, text=True, timeout=VERIFY_LIMIT + 30)
            ts.append(time.perf_counter() - t0)
            ok = proc.returncode == 0
    return statistics.median(ts), ok


def main() -> None:
    runner = DafnyRunner()
    transp = Ward0Transpiler()
    w_rows = {r["task_id"]: r for r in load_jsonl(Path("experiments/runs/phase1_W_full8.jsonl"))}
    d_rows = {r["task_id"]: r for r in load_jsonl(Path("experiments/runs/phase1_D_arm.jsonl"))}
    print(f"dafny={runner.dafny}")
    print()

    for tid in TASKS:
        desc = json.loads((TASKS_DIR / f"{tid}.json").read_text(encoding="utf-8"))
        extern = extern_ward0(desc)
        wrow = w_rows[tid]
        drow = d_rows[tid]
        # find the pass attempt (or last attempt) for each arm
        w_attempt = next((a for a in wrow["attempts"] if a.get("status") == "pass"), wrow["attempts"][-1])
        d_attempt = next((a for a in drow["attempts"] if a.get("status") == "pass"), drow["attempts"][-1])
        w_cand = w_attempt["generated"]
        d_cand = d_attempt["generated"]

        print(f"== {tid} ==")
        print(f"  W candidate {len(w_cand)} chars, D candidate {len(d_cand)} chars")

        # W+ : real W-arm verification path (transpile enforce=True)
        transp.enforce_boundary = True
        try:
            src_w_on = transp.transpile(extern + "\n\n" + w_cand)
            w_on, w_on_ok = time_verify(src_w_on, runner.dafny, runner.z3)
        except Exception as exc:
            print(f"  W+ transpile/verify FAILED: {exc}")
            w_on, w_on_ok = float("nan"), False
        # W- : same candidate, no wrappers
        transp.enforce_boundary = False
        try:
            src_w_off = transp.transpile(extern + "\n\n" + w_cand)
            w_off, w_off_ok = time_verify(src_w_off, runner.dafny, runner.z3)
        except Exception as exc:
            print(f"  W- transpile/verify FAILED: {exc}")
            w_off, w_off_ok = float("nan"), False
        # D  : raw candidate with extern preamble
        src_d = desc["extern_dafny"] + "\n\n" + d_cand
        d_t, d_ok = time_verify(src_d, runner.dafny, runner.z3)

        print(f"  actual W arm (recorded verify_s): {wrow['verify_s']}s  | D arm: {drow['verify_s']}s")
        print(f"  re-measured W+ enforce on : {w_on:.2f}s verified={w_on_ok}")
        print(f"  re-measured W- enforce off: {w_off:.2f}s verified={w_off_ok}")
        print(f"  re-measured D  raw         : {d_t:.2f}s verified={d_ok}")
        print(f"  wrapper delta on real candidate: {w_on - w_off:+.2f}s  |  model-output delta (W- vs D): {w_off - d_t:+.2f}s")


if __name__ == "__main__":
    main()
