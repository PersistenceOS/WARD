"""Probe the {:verify false} wrapper variant (C3b investigation).

Question: can the wrapper's ensures be made cheaper by skipping its
re-verification (the stub is already {:axiom}, so the wrapper's proof is
axiom-discharged)? Measures:
  1. verify-time delta: verified wrapper vs {:verify false} wrapper on the
     same reference code (w4/w5/w7).
  2. runtime enforcement preserved: hidden-test markers on the buggy scenarios
     still show 0 boundary escapes (C2) with the unverified wrapper.

Note: {:verify false} affects verification only — Dafny still compiles the
wrapper body, so the runtime Err("contract violation") check still executes.
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


def patch_verify_false(dafny_src: str) -> str:
    """Add {:verify false} to every generated _checked wrapper method."""
    out = []
    for line in dafny_src.splitlines():
        if line.startswith("method ") and "_checked(" in line:
            line = line.replace("method ", "method {:verify false} ", 1)
        out.append(line)
    return "\n".join(out)


def main() -> None:
    runner = DafnyRunner()
    transp = Ward0Transpiler()
    print(f"dafny={runner.dafny}")
    print()

    for tid in TASKS:
        desc = json.loads((TASKS_DIR / f"{tid}.json").read_text(encoding="utf-8"))
        ref = (TASKS_DIR / f"{tid}.ward0").read_text(encoding="utf-8")
        extern = extern_ward0(desc)

        transp.enforce_boundary = True
        src_verified = transp.transpile(extern + "\n\n" + ref)
        src_skip = patch_verify_false(src_verified)

        t_verified, ok_v = time_verify(src_verified, runner.dafny, runner.z3)
        t_skip, ok_s = time_verify(src_skip, runner.dafny, runner.z3)

        # Runtime enforcement check (C2): buggy scenarios, no-verify translate+run
        transp.enforce_boundary = True
        src_skip_via_transpiler = patch_verify_false(transp.transpile(extern + "\n\n" + ref))
        try:
            markers_v = runner.run_hidden_tests_b_marked(desc, ref, "trust", enforce=True, no_verify=True)
        except Exception as exc:
            markers_v = [f"ERR:{exc}"]
        # run the {:verify false} variant by temporarily swapping the runner transpiler output
        orig = runner.transpiler
        runner.transpiler = _SkipVerifyTranspiler()
        try:
            markers_s = runner.run_hidden_tests_b_marked(desc, ref, "trust", enforce=True, no_verify=True)
        except Exception as exc:
            markers_s = [f"ERR:{exc}"]
        finally:
            runner.transpiler = orig

        leak_v = sum(1 for m in markers_v if m == "OKLEAK")
        leak_s = sum(1 for m in markers_s if m == "OKLEAK")
        print(f"== {tid} ==")
        print(f"  verified wrapper : {t_verified:.2f}s ok={ok_v}  markers={markers_v} leaks={leak_v}")
        print(f"  skip-verify wrap : {t_skip:.2f}s ok={ok_s}  markers={markers_s} leaks={leak_s}")
        print(f"  delta: {t_skip - t_verified:+.2f}s  ({(t_skip / t_verified - 1) * 100:+.0f}%)")


class _SkipVerifyTranspiler(Ward0Transpiler):
    """Transpiler that emits {:verify false} wrappers (used for the runtime probe)."""

    def _wrapper_dafny(self, info: dict) -> str:
        out = super()._wrapper_dafny(info)
        return out.replace("method ", "method {:verify false} ", 1)


if __name__ == "__main__":
    main()
