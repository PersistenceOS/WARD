"""Probe the {:axiom}-with-body wrapper variant (C3b investigation).

Dafny's own diagnostic for {:verify false} said: "Consider using a bodyless
method together with the {:axiom} attribute instead." A method marked {:axiom}
is trusted (spec assumed, body NOT verified) but — unlike {:extern} — its body
still compiles and executes. That is exactly what a generated wrapper needs:
skip the ~0.1-0.5s per-method proof, keep the runtime Err("contract violation")
check. Measures verify-time delta AND runtime enforcement (C2) preservation.
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


class AxiomWrapperTranspiler(Ward0Transpiler):
    """Transpiler whose generated wrappers are marked {:axiom} (trusted, unverified)."""

    def _wrapper_dafny(self, info: dict) -> str:
        out = super()._wrapper_dafny(info)
        return out.replace("method ", "method {:axiom} ", 1)


def main() -> None:
    runner = DafnyRunner()
    transp = Ward0Transpiler()
    axiom_tp = AxiomWrapperTranspiler()
    print(f"dafny={runner.dafny}")
    print()

    for tid in TASKS:
        desc = json.loads((TASKS_DIR / f"{tid}.json").read_text(encoding="utf-8"))
        ref = (TASKS_DIR / f"{tid}.ward0").read_text(encoding="utf-8")
        extern = extern_ward0(desc)

        transp.enforce_boundary = True
        src_verified = transp.transpile(extern + "\n\n" + ref)
        axiom_tp.enforce_boundary = True
        src_axiom = axiom_tp.transpile(extern + "\n\n" + ref)

        t_verified, ok_v = time_verify(src_verified, runner.dafny, runner.z3)
        t_axiom, ok_a = time_verify(src_axiom, runner.dafny, runner.z3)

        # runtime enforcement with the axiom-marked wrappers (no-verify translate+run)
        orig = runner.transpiler
        runner.transpiler = axiom_tp
        try:
            markers_a = runner.run_hidden_tests_b_marked(desc, ref, "trust", enforce=True, no_verify=True)
        except Exception as exc:
            markers_a = [f"ERR:{str(exc)[:200]}"]
        finally:
            runner.transpiler = orig
        # baseline: verified wrappers
        markers_v = runner.run_hidden_tests_b_marked(desc, ref, "trust", enforce=True, no_verify=True)

        leak_v = sum(1 for m in markers_v if m == "OKLEAK")
        leak_a = sum(1 for m in markers_a if m == "OKLEAK")
        print(f"== {tid} ==")
        print(f"  verified wrapper : {t_verified:.2f}s ok={ok_v}  leaks={leak_v} markers={markers_v}")
        print(f"  {{:axiom}} wrapper : {t_axiom:.2f}s ok={ok_a}  leaks={leak_a} markers={markers_a}")
        print(f"  delta: {t_axiom - t_verified:+.2f}s  ({(t_axiom / t_verified - 1) * 100:+.0f}%)")


if __name__ == "__main__":
    main()
