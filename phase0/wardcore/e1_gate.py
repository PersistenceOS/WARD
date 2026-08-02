"""E1 gate: the typed elaborator front-end must not regress the validated core.

Pre-registered gate (files/ward-phase2-scoping.md §6, row E1):

    100% of existing oracle references type-check + emit through the new
    pipeline (no regression: the 62 + 8 sets still pass; suite stays green).

Because the elaborator's emission is byte-identical to the Phase-0/1
Ward0Transpiler, and the transpiler's output already passed `dafny verify` on
every reference during Phase 0/1, byte-parity transitively proves verification.
The --verify flag makes that leg explicit by running `dafny verify` on the
emitted output.

Week-5/6 extension: the w-task oracle set grew w9-w12 (multi-function modules
with per-fn tier/effects + per-extern dep annotations). Their composition
reuses the E5-real gate's `compose_module` so the typed pipeline consumes the
same annotated surface the model faces. Tested-bearing modules (w11) are
EXCLUDED from byte-parity by design: the elaborator emits `{:verify false}`
for Tested fns (tier semantics, T6/R10) while the Phase-0/1 transpiler has no
tier concept — that divergence is verified by the E5/E5-real gates instead.

Usage:
    python -m wardcore.e1_gate            # parity only (fast)
    python -m wardcore.e1_gate --verify   # parity + dafny verify every reference
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from transpiler.transpiler import Ward0Transpiler

from wardcore.elaborator import Elaborator

PHASE0_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = PHASE0_DIR / "benchmarks" / "tasks"
W_TASKS_DIR = PHASE0_DIR / "benchmarks" / "w_tasks"

_TESTED_RE = re.compile(r'(?m)^[ \t]*tier[ \t]*:[ \t]*Tested[ \t]*$')


def w_task_source(desc: dict, tasks_dir: Path) -> str:
    """Compose the full ward0 source exactly as the Phase-1 harness does
    (make_w_prompt prepends extern stubs from the JSON descriptor), with the
    week-3+ toolchain annotations the typed pipeline consumes (per-extern
    trust/effect/dep + the module-level dep header) — the SAME composition as
    the E5-real gate (compose_module), so E1 parity and E5-real see one
    surface. The transpiler strips these lines pre-parse, so byte-parity is
    unaffected for non-Tested modules."""
    from wardcore.e5_real_gate import compose_module

    ref = (tasks_dir / f"{desc['id']}.ward0").read_text(encoding="utf-8")
    return compose_module(desc, ref)


def _is_tested_bearing(src: str) -> bool:
    """True if the module declares a Tested-tier fn. Such modules are excluded
    from byte-parity: the elaborator's `{:verify false}` emission for Tested
    fns is tier semantics (T6), not a regression — E5/E5-real verify them."""
    return bool(_TESTED_RE.search(src))


def reference_sources() -> list[tuple[str, str]]:
    """Return [(id, ward0_source)] for the full oracle set (62 + w-tasks)."""
    out: list[tuple[str, str]] = []
    for src_path in sorted(TASKS_DIR.glob("*.ward0")):
        out.append((src_path.stem, src_path.read_text(encoding="utf-8")))
    for jpath in sorted(W_TASKS_DIR.glob("*.json")):
        desc = json.loads(jpath.read_text(encoding="utf-8"))
        if desc.get("arm_kind") != "w":
            continue
        out.append((desc["id"], w_task_source(desc, W_TASKS_DIR)))
    return out


def run_parity(enforce: bool, label: str) -> tuple[list[str], int]:
    """Byte-compare elaborator vs transpiler for every parity-comparable
    reference (all but Tested-bearing). Returns (fails, compared_count)."""
    elab = Elaborator(enforce_boundary=enforce)
    tp = Ward0Transpiler(enforce_boundary=enforce)
    fails: list[str] = []
    count = 0
    skipped = 0
    for rid, src in reference_sources():
        if _is_tested_bearing(src):
            skipped += 1
            continue  # tier semantics change emission — verified by E5-real
        count += 1
        d0 = tp.transpile(src)
        d1 = elab.transpile(src)
        if d0 != d1:
            fails.append(rid)
    print(f"E1 {label}: {count - len(fails)}/{count} byte-identical (skipped {skipped} Tested-bearing -> E5-real)")
    for f in fails:
        print(f"  FAIL {f}")
    return fails, count


def run_verify() -> list[str]:
    """`dafny verify` the elaborator's emitted output for every reference."""
    from harness.dafny_runner import DafnyRunner

    runner = DafnyRunner()
    fails: list[str] = []
    count = 0
    for rid, src in reference_sources():
        count += 1
        try:
            emitted = Elaborator(enforce_boundary=True).transpile(src)
            ok, detail = runner.verify_dafny(emitted, timeout=120, allow_warnings=_is_tested_bearing(src))
        except Exception as exc:
            ok, detail = False, str(exc)
        if not ok:
            fails.append(rid)
            print(f"  VERIFY FAIL {rid}: {detail.strip()[:200]}")
        else:
            print(f"  verify ok {rid}", end="\r")
    print()
    print(f"E1 verify: {count - len(fails)}/{count} verify clean")
    return fails


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="E1 gate: no-regression parity + verify")
    parser.add_argument("--verify", action="store_true", help="also run dafny verify on every reference")
    args = parser.parse_args(argv)

    all_fails: list[str] = []
    f_plain, n_plain = run_parity(False, "plain")
    f_enforce, n_enforce = run_parity(True, "enforce")
    all_fails += f_plain + f_enforce
    if args.verify:
        all_fails += run_verify()

    if all_fails:
        print(f"\nE1 GATE FAIL ({len(all_fails)} problems)")
        return 1
    print(f"\nE1 GATE PASS: {n_plain}/{n_plain} plain + {n_enforce}/{n_enforce} enforce byte-identical" + (" (+ verify clean)" if args.verify else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
