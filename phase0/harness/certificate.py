"""ward-cert certificate emission — Phase A feasibility probe (gate E9, Phase 2.5).

Implements the `.proof` artifact from `files/ward-certified-code.md`: a
machine-checkable certificate binding ward0 source, emitted Dafny, per-function
tier + proof outcome, and the trust-boundary manifest.

This module only EMITS and MEASURES (Phase A of the plan). The standalone
checker (cert_check, no Dafny/Z3) is Phase C and lives separately.

Status: PROBE — the numbers it prints are the first measured inputs to gate E9
(production cost <= 5% of measured verify + token cost).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from harness.dafny_runner import DafnyRunner
from wardcore.tightness_pass import measure_source

CERT_FORMAT = "ward-cert/v0.1"

BENCH_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "w_tasks"
OUT_DIR = Path(__file__).resolve().parent.parent / "experiments" / "runs" / "cert_probe"

# Phase-A probe set: mixed tiers (Proven, Contracted, Tested) so the manifest
# and the per-function tier fields are exercised honestly.
PROBE_TASKS = [
    "w1_payment_chain",      # Proven, 3 externs
    "w4_order_placement",    # Proven, 2 externs
    "w6_crud_handler",       # Tested, 2 externs
    "w5_currency_roundtrip", # Proven, 2 externs
    "w7_idempotency",        # Proven, 3 externs
]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_TOOLCHAIN_CACHE: dict | None = None


def detect_toolchain(enforce: bool, verify_limit: int) -> dict:
    """Toolchain versions detected from the installed binaries (fall back to
    the known-good defaults if a probe fails). Keeps the certificate's
    toolchain block honest without lying about drift."""
    global _TOOLCHAIN_CACHE
    if _TOOLCHAIN_CACHE is None:
        versions = {"dafny": "4.11.0", "z3": "4.12.1"}
        for exe, key in (("dafny", "dafny"), ("z3", "z3")):
            path = shutil.which(exe)
            if not path:
                continue
            try:
                out = subprocess.run(
                    [path, "--version"], capture_output=True, text=True,
                    timeout=15, check=False,
                ).stdout
            except (OSError, subprocess.TimeoutExpired):
                continue
            m = re.search(r"(\d+\.\d+\.\d+)", out)
            if m:
                versions[key] = m.group(1)
        _TOOLCHAIN_CACHE = versions
    return {
        "ward_core": "0.1",
        "dafny": _TOOLCHAIN_CACHE["dafny"],
        "z3": _TOOLCHAIN_CACHE["z3"],
        "enforce_boundary": enforce,
        "verification_time_limit": verify_limit,
    }


def _dafny_counts(detail: str) -> dict:
    """Parse `X verified, Y errors` from `dafny verify` output (best-effort).

    Dafny 4.x prints the singular ``1 error`` on a single failure, so the
    errors pattern must accept both singular and plural.
    """
    m = re.search(r"(\d+)\s+verified", detail)
    e = re.search(r"(\d+)\s+errors?", detail)
    return {
        "verified": int(m.group(1)) if m else 0,
        "errors": int(e.group(1)) if e else 0,
    }


def build_trust_manifest(task: dict, enforce: bool) -> list[dict]:
    """Extern name + trust string + monitor flag, from the task descriptor.

    Phase-A note: the w-task corpus composes externs from JSON descriptors
    (the .ward0 files carry no inline `trust:` lines), so the manifest is built
    from the descriptor's contract. The monitor flag = enforce_boundary applied.
    """
    manifest = []
    for stub in task.get("externs", []):
        manifest.append(
            {
                "extern": stub["name"],
                "trust": stub.get("contract", ""),
                "monitor": enforce,
            }
        )
    return manifest


def emit_certificate(
    *,
    module: str,
    source: str,
    dafny_src: str,
    tiers: dict,
    outcomes: dict,
    trust_manifest: list,
    toolchain: dict,
    tightness: dict | None = None,
) -> dict:
    """Assemble the .proof artifact (design: files/ward-certified-code.md §3).

    T6 semantics are honored per function: a Tested-tier entry carries
    ``proof: "tested"`` (no proof obligation) and is excluded from the
    VALID-all requirement — only Proven/Contracted entries must verify.

    tightness: optional {fn_name: I1 GateResult dict} (advisory). Each fn
    entry gains tau / tau_advisory / tau_unevaluable — recorded, never
    enforced: tau is the measured Specification Tightness of the surface
    contract, and a low tau (or unevaluable) NEVER changes the tier or the
    verdict.
    """
    fns = sorted(set(tiers) | set(outcomes))

    def fn_entry(fn: str) -> dict:
        o = outcomes.get(fn, {})
        t = (tightness or {}).get(fn, {})
        tau = {
            "tau": t.get("tau"),
            "tau_advisory": t.get("action"),
            "tau_unevaluable": t.get("unevaluable"),
        }
        tier = tiers.get(fn, "Proven")
        if tier == "Tested":
            # No proof obligation (T6): the tier, not a raw-verify outcome,
            # decides what the certificate claims for this function.
            # verify_s reflects the probe's full-module raw verify (no tier
            # routing in Phase A), not a T6-style "never ran" measurement.
            return {
                "name": fn,
                "tier": tier,
                "proof": "tested",
                "verified": 0,
                "errors": 0,
                "verify_s": round(o.get("verify_s", 0.0), 3),
                **tau,
            }
        return {
            "name": fn,
            "tier": tier,
            "proof": o.get("proof", "unknown"),
            "verified": o.get("verified", 0),
            "errors": o.get("errors", 0),
            "verify_s": round(o.get("verify_s", 0.0), 3),
            **tau,
        }

    proof_carrying = [
        o.get("proof")
        for fn, o in outcomes.items()
        if tiers.get(fn, "Proven") != "Tested"
    ]
    verdict = "VALID" if all(p == "verified" for p in proof_carrying) else "INVALID"
    return {
        "format": CERT_FORMAT,
        "module": module,
        # source is the composed module (extern decls + caller) — exactly what
        # was verified, so the hash binds precisely what the certificate claims.
        "source_sha256": sha256_text(source),
        "emitted_dafny_sha256": sha256_text(dafny_src),
        "toolchain": toolchain,
        "functions": [fn_entry(fn) for fn in fns],
        "trust_boundary": trust_manifest,
        "verdict": verdict,
    }


def probe_one(task_id: str, enforce: bool = False, verify_limit: int = 30) -> dict:
    """Emit a .proof for one w-task and measure the marginal cost of producing it."""
    task = json.loads((BENCH_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    ward0_src = (BENCH_DIR / f"{task_id}.ward0").read_text(encoding="utf-8")

    # Build the full ward0 source exactly like the harness does (trust arm):
    # extern declarations (contracts) prepended to the caller.
    full_src = DafnyRunner.extern_ward0_of(task, "trust") + "\n\n" + ward0_src

    # Transpile for the emitted-Dafny hash (reuse the runner's transpiler so
    # the source is transpiled exactly once for both hash and verify).
    runner = DafnyRunner()
    t_transpile = time.perf_counter()
    dafny_src = runner.transpile(full_src, enforce=enforce)
    transpile_s = time.perf_counter() - t_transpile

    # Verify (the existing pipeline step — this cost is NOT certificate-specific).
    t_verify = time.perf_counter()
    ok, detail = runner.verify(full_src, enforce=enforce, verify_limit=verify_limit)
    verify_s = time.perf_counter() - t_verify

    counts = _dafny_counts(detail)
    fn = task.get("fn")
    tiers = task.get("tiers", {})
    outcomes = {
        fn: {
            "proof": "verified" if ok else "failed",
            "verified": counts["verified"],
            "errors": counts["errors"],
            "verify_s": verify_s,
        }
    }
    toolchain = detect_toolchain(enforce, verify_limit)

    # Certificate emission itself (hash + json) — the marginal cost.
    t_emit = time.perf_counter()
    cert = emit_certificate(
        module=task_id,
        source=full_src,
        dafny_src=dafny_src,
        tiers=tiers,
        outcomes=outcomes,
        trust_manifest=build_trust_manifest(task, enforce),
        toolchain=toolchain,
        # I1 (advisory): per-fn Specification Tightness from the caller's
        # surface contract — recorded in the .proof, never enforced.
        tightness=measure_source(ward0_src, tiers),
    )
    emit_s = time.perf_counter() - t_emit

    return {
        "task": task_id,
        "ok": ok,
        "verified": counts["verified"],
        "errors": counts["errors"],
        "transpile_s": round(transpile_s, 3),
        "verify_s": round(verify_s, 3),
        "emit_s": round(emit_s, 6),
        "cert": cert,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase-A certificate probe (gate E9)")
    ap.add_argument("--tasks", nargs="*", default=PROBE_TASKS, help="task ids")
    ap.add_argument("--enforce", action="store_true", help="enforce_boundary on")
    ap.add_argument("--out", default=str(OUT_DIR), help="output directory")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for tid in args.tasks:
        r = probe_one(tid, enforce=args.enforce)
        rows.append(r)
        (out_dir / f"{tid}.proof").write_text(
            json.dumps(r["cert"], indent=2), encoding="utf-8"
        )

    print(f"{'task':<20}{'ok':<5}{'verified':<10}{'transpile_s':<13}{'verify_s':<10}{'emit_s':<12}{'emit/verify':<12}")
    total_v = total_e = 0.0
    for r in rows:
        ratio = (r["emit_s"] / r["verify_s"] * 100) if r["verify_s"] > 0 else float("inf")
        print(
            f"{r['task']:<20}{str(r['ok']):<5}{r['verified']:<10}{r['transpile_s']:<13.3f}"
            f"{r['verify_s']:<10.3f}{r['emit_s']:<12.6f}{ratio:<12.4f}"
        )
        total_v += r["verify_s"]
        total_e += r["emit_s"]
    print(f"\nTOTAL verify {total_v:.3f}s, emit {total_e:.6f}s, "
          f"marginal cost {(total_e / total_v * 100) if total_v else 0:.4f}%")
    print(f"certificates written to {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
