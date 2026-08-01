"""Isolate the _checked wrapper's verification cost (C3b investigation).

For the slow tasks (w4/w5/w7), transpile the SAME reference three ways and
time `dafny verify` on each:

  W+  : ward0 reference, enforce=True   (checked wrappers)      <- the treatment
  W-  : ward0 reference, enforce=False  (direct stub calls)     <- wrapper isolated
  D   : raw Dafny reference (extern_dafny + reference_dafny_caller)

The W+ vs W- delta is the pure wrapper cost on identical program logic.
Also dumps the generated .dfy for w4 so the extra obligations can be inspected.
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
RUNS = 3
VERIFY_LIMIT = 60  # same as harness TIER_VERIFY_LIMIT["Proven"]


def load(desc_path: Path) -> dict:
    return json.loads(desc_path.read_text(encoding="utf-8"))


def extern_ward0(desc: dict) -> str:
    parts = []
    for stub in desc["externs"]:
        params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
        sig = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
        if stub.get("contract"):
            sig += "\n  " + stub["contract"]
        parts.append(sig + ";")
    return "\n\n".join(parts)


def time_verify(dafny_src: str, dafny: str, z3: str | None) -> tuple[float, str, int]:
    """Time one dafny verify. Returns (seconds, tail of output, returncode)."""
    cmd = [dafny, "verify", f"--verification-time-limit:{VERIFY_LIMIT}"]
    if z3:
        cmd += ["--solver-path", z3]
    with tempfile.TemporaryDirectory() as td:
        dfy = Path(td) / "task.dfy"
        dfy.write_text(dafny_src, encoding="utf-8")
        t0 = time.perf_counter()
        proc = subprocess.run(cmd + [str(dfy)], capture_output=True, text=True, timeout=VERIFY_LIMIT + 30)
        dt = time.perf_counter() - t0
    return dt, (proc.stdout + proc.stderr).strip()[-400:], proc.returncode


def main() -> None:
    runner = DafnyRunner()
    transp = Ward0Transpiler()
    dafny = runner.dafny
    z3 = runner.z3
    print(f"dafny={dafny} z3={z3}")
    out = []

    for tid in TASKS:
        desc = load(TASKS_DIR / f"{tid}.json")
        ref_ward0 = (TASKS_DIR / f"{tid}.ward0").read_text(encoding="utf-8")
        extern = extern_ward0(desc)

        # Build the three variants
        transp.enforce_boundary = True
        src_w_on = transp.transpile(extern + "\n\n" + ref_ward0)
        transp.enforce_boundary = False
        src_w_off = transp.transpile(extern + "\n\n" + ref_ward0)
        src_d = desc["extern_dafny"] + "\n\n" + desc["reference_dafny_caller"]

        # Sanity: all three must verify
        for label, src in (("W+", src_w_on), ("W-", src_w_off), ("D", src_d)):
            dt, tail, rc = time_verify(src, dafny, z3)
            print(f"  sanity {tid} {label}: rc={rc} {dt:.2f}s")

        # Timed runs
        times = {}
        for label, src in (("W+", src_w_on), ("W-", src_w_off), ("D", src_d)):
            ts = []
            rcs = []
            for _ in range(RUNS):
                dt, _tail, rc = time_verify(src, dafny, z3)
                ts.append(dt)
                rcs.append(rc)
            times[label] = (statistics.median(ts), min(ts), max(ts), all(rc == 0 for rc in rcs))
        w_on_med = times["W+"][0]
        w_off_med = times["W-"][0]
        d_med = times["D"][0]
        print(f"== {tid} ==")
        for label, (med, lo, hi, ok) in times.items():
            print(f"  {label}: median {med:.2f}s  (min {lo:.2f}, max {hi:.2f}) verified={ok}")
        print(f"  wrapper delta W+ - W-: {w_on_med - w_off_med:+.2f}s  ({(w_on_med / w_off_med - 1) * 100:+.0f}%)")
        print(f"  W+ vs D: {w_on_med - d_med:+.2f}s  ({(w_on_med / d_med - 1) * 100:+.0f}%)")
        out.append(
            {
                "task": tid,
                "W+_median": round(w_on_med, 2),
                "W-_median": round(w_off_med, 2),
                "D_median": round(d_med, 2),
                "wrapper_delta_s": round(w_on_med - w_off_med, 2),
                "verified": times["W+"][3] and times["W-"][3] and times["D"][3],
            }
        )

        # Dump the w4 generated files for inspection
        if tid == "w4_order_placement":
            (Path("experiments") / "w4_wrappers_on.dfy").write_text(src_w_on, encoding="utf-8")
            (Path("experiments") / "w4_wrappers_off.dfy").write_text(src_w_off, encoding="utf-8")

    print("\n=== summary ===")
    for row in out:
        print(row)


if __name__ == "__main__":
    main()
