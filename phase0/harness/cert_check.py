"""cert-check — standalone .proof validator (Phase C, gate E9 G2/G3).

Validates a ward-cert v0.1 .proof artifact WITHOUT Dafny, Z3, or any Ward
toolchain dependency — stdlib only, runs anywhere. Exit codes:

  0 = VALID
  1 = INVALID (each failing check named on stdout; unreadable proof/source
      also report as INVALID via check_file's io handling)

Checks (Level-1, per files/ward-certified-code.md §4):
  1. structure   — format, module, toolchain (enforce_boundary, vlimit),
                   functions, trust_boundary are well-formed
  2. rebinding   — source_sha256 matches the provided ward0 source text
  3. tier rules  — Tested entries carry proof "tested" (no obligation, T6);
                   only Proven/Contracted entries must verify for VALID
  4. manifest    — every extern declared in the source appears in
                   trust_boundary with a non-empty trust string, and its
                   monitor flag equals toolchain.enforce_boundary
  5. verdict     — recomputed from functions; must equal the recorded verdict

Level-2 (Phase-3 standalone checker, `--re-derive`): the one declared Level-1
honesty gap — emitted_dafny_sha256 was *declared, not independently re-derived*.
When enabled, the checker RE-TRANSPILES the bound ward0 source itself (stdlib-only
re-implementation, `cert_rederive` — no Dafny, no lark, no transpiler, no model)
and compares the re-derived hash to the recorded emitted_dafny_sha256. This closes
the source -> emitted binding trust gap: the certificate is only meaningful if the
code checked is the code shipped, and byte-identical emission (E1) is what makes a
hash-binding certificate sound at all.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

CERT_FORMAT = "ward-cert/v0.1"
TIERS = {"Tested", "Contracted", "Proven"}
PROOFS = {"verified", "failed", "tested", "unknown"}
EXTERN_RE = re.compile(r"extern\s+fn\s+(\w+)")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_level2(proof: dict, source: str) -> list[str]:
    """Phase-3: independently re-transpile the bound source and verify
    emitted_dafny_sha256. Never raises — a re-transpile failure (or a hash
    mismatch) is a named violation, not a traceback.

    Uses the certificate's own toolchain.enforce_boundary so the re-derivation
    runs in the same wrapper mode the emitter used.
    """
    problems: list[str] = []
    tc = proof.get("toolchain")
    if not isinstance(tc, dict):
        tc = {}
    enforce = bool(tc.get("enforce_boundary", False))
    try:
        from harness.cert_rederive import RederiveError, transpile as re_transpile

        derived = re_transpile(source, enforce=enforce)
    except RederiveError as exc:
        problems.append(f"re-derivation: source did not re-transpile: {exc}")
        return problems
    except Exception as exc:  # pragma: no cover - safety net, never raise
        problems.append(f"re-derivation: internal error: {exc}")
        return problems
    got = sha256_text(derived)
    want = proof.get("emitted_dafny_sha256")
    if not isinstance(want, str):
        problems.append("emitted_dafny_sha256: missing")
    elif got != want:
        problems.append(
            "re-derivation: re-transpiled source does not match emitted_dafny_sha256 "
            f"(got {got[:16]}…, recorded {want[:16]}…)"
        )
    return problems


def validate(proof: dict, source: str | None) -> list[str]:
    """Return a list of violations (empty list = VALID). Never raises."""
    problems: list[str] = []

    # 1. structure ---------------------------------------------------------
    if proof.get("format") != CERT_FORMAT:
        problems.append(f"format: expected {CERT_FORMAT!r}, got {proof.get('format')!r}")
    module = proof.get("module")
    if not isinstance(module, str) or not module:
        problems.append("module: missing")
    tc = proof.get("toolchain") or {}
    if not isinstance(tc, dict):
        problems.append("toolchain: must be a dict")
        tc = {}
    if not isinstance(tc.get("enforce_boundary"), bool):
        problems.append("toolchain.enforce_boundary: must be a bool")
    vlimit = tc.get("verification_time_limit")
    if not isinstance(vlimit, int) or vlimit <= 0:
        problems.append("toolchain.verification_time_limit: must be a positive int")
    fns = proof.get("functions")
    if not isinstance(fns, list) or not fns:
        problems.append("functions: must be a non-empty list")
    else:
        for fn in fns:
            name = fn.get("name")
            tier = fn.get("tier")
            pf = fn.get("proof")
            if tier not in TIERS:
                problems.append(f"functions[{name}].tier: {tier!r} not in {sorted(TIERS)}")
            if pf not in PROOFS:
                problems.append(f"functions[{name}].proof: {pf!r} not in {sorted(PROOFS)}")

    # 2. source rebinding ----------------------------------------------------
    if not isinstance(proof.get("source_sha256"), str):
        problems.append("source_sha256: missing")
    elif source is not None and sha256_text(source) != proof["source_sha256"]:
        problems.append("source_sha256: mismatch — certificate was made for different code")

    # 3. tier rules (T6) -----------------------------------------------------
    for fn in (fns if isinstance(fns, list) else []):
        name, tier, pf = fn.get("name"), fn.get("tier"), fn.get("proof")
        if tier == "Tested" and pf != "tested":
            problems.append(
                f"functions[{name}]: Tested tier must record proof 'tested' (T6, no obligation), got {pf!r}"
            )

    # 4. trust manifest vs source externs -------------------------------------
    if source is not None:
        externs = EXTERN_RE.findall(source)
        manifest = proof.get("trust_boundary") or []
        by_name = {m.get("extern"): m for m in manifest if isinstance(m, dict)}
        for ext in externs:
            entry = by_name.get(ext)
            if entry is None:
                problems.append(f"trust_boundary: extern {ext!r} declared in source but absent from manifest")
            elif not entry.get("trust"):
                problems.append(f"trust_boundary[{ext}].trust: must be non-empty")
            elif entry.get("monitor") is not tc.get("enforce_boundary"):
                problems.append(
                    f"trust_boundary[{ext}].monitor: must equal toolchain.enforce_boundary "
                    f"({tc.get('enforce_boundary')})"
                )
        declared = set(externs)
        for m in manifest:
            if isinstance(m, dict) and m.get("extern") not in declared:
                problems.append(f"trust_boundary: extern {m.get('extern')!r} in manifest but not declared in source")

    # 5. verdict consistency ---------------------------------------------------
    if isinstance(fns, list) and fns:
        proof_carrying = [f.get("proof") for f in fns if f.get("tier") != "Tested"]
        expected = "VALID" if all(p == "verified" for p in proof_carrying) else "INVALID"
        if proof.get("verdict") != expected:
            problems.append(f"verdict: recorded {proof.get('verdict')!r}, functions imply {expected!r}")

    return problems


def check_file(
    proof_path: Path, source_path: Path | None, rederive: bool = False
) -> tuple[bool, list[str]]:
    """Validate a .proof on disk against an optional source file."""
    try:
        proof = json.loads(proof_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, [f"io: cannot read {proof_path}: {exc}"]
    source = None
    if source_path is not None:
        try:
            source = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, [f"io: cannot read {source_path}: {exc}"]
    problems = validate(proof, source)
    if rederive:
        if source is None:
            problems.append("re-derivation: --source required for --re-derive")
        else:
            problems.extend(validate_level2(proof, source))
    return not problems, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="validate a ward-cert .proof artifact (no Dafny/Z3)")
    ap.add_argument("proof", help="path to the .proof artifact")
    ap.add_argument("--source", help="path to the ward0 source the certificate binds (recommended)")
    ap.add_argument(
        "--re-derive",
        action="store_true",
        help="Level-2 (Phase-3): re-transpile the bound source and independently verify "
        "emitted_dafny_sha256 (closes the Level-1 declared-not-re-derived gap)",
    )
    args = ap.parse_args(argv)
    ok, problems = check_file(
        Path(args.proof), Path(args.source) if args.source else None, rederive=args.re_derive
    )
    if ok:
        print("VALID")
        return 0
    for p in problems:
        print(f"INVALID: {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
