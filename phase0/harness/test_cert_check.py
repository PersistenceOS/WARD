"""Tests for the standalone .proof checker (Phase C, gate E9: G2 + G3).

G2 fidelity: the checker's verdict agrees with the harness's measured verdicts
on the committed cert_probe artifacts (all VALID — every task verified).
G3 tamper-evidence: modifying source, a trust string, or a recorded verdict
invalidates the certificate (exit 1 / non-empty violations).

Level-2 (Phase-3 standalone checker): with `--re-derive` the checker
re-transpiles the bound ward0 source itself and independently verifies
emitted_dafny_sha256 — closing the Level-1 declared-not-re-derived gap.

Both checker paths must stay stdlib-only (no dafny/lark/transpiler/wardcore
imports — the standalone claim; cert_rederive is itself a stdlib-only
re-implementation). The tests here may use the harness to *recompose* the
source for rebinding, but the module under test never does.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness.cert_check import validate, validate_level2, check_file
from harness.dafny_runner import DafnyRunner

BENCH_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "w_tasks"
CERT_DIR = Path(__file__).resolve().parent.parent / "experiments" / "runs" / "cert_probe"

PROBE_TASKS = [
    "w1_payment_chain",      # Proven, 3 externs
    "w4_order_placement",    # Proven, 2 externs
    "w5_currency_roundtrip", # Proven, 2 externs
    "w6_crud_handler",       # Tested, 2 externs
    "w7_idempotency",        # Proven, 3 externs
]


def composed_source(task_id: str) -> str:
    """Recompose the exact module the certificate was emitted for: extern
    declarations (trust arm) + caller. Mirrors harness/certificate.py."""
    task = json.loads((BENCH_DIR / f"{task_id}.json").read_text(encoding="utf-8"))
    ward0 = (BENCH_DIR / f"{task_id}.ward0").read_text(encoding="utf-8")
    return DafnyRunner.extern_ward0_of(task, "trust") + "\n\n" + ward0


def load_cert(task_id: str) -> dict:
    return json.loads((CERT_DIR / f"{task_id}.proof").read_text(encoding="utf-8"))


class G2FidelityTest(unittest.TestCase):
    """The checker's verdict must agree with the measured verdicts."""

    def test_all_probe_certs_validate(self):
        for tid in PROBE_TASKS:
            cert = load_cert(tid)
            problems = validate(cert, composed_source(tid))
            self.assertEqual(problems, [], f"{tid} should be VALID: {problems}")
            self.assertEqual(cert["verdict"], "VALID")

    def test_w1_payment_chain_valid(self):
        cert = load_cert("w1_payment_chain")
        self.assertEqual(cert["functions"][0]["proof"], "verified")
        self.assertEqual(cert["functions"][0]["tier"], "Proven")
        self.assertEqual(len(cert["trust_boundary"]), 3)

    def test_exit_code_zero_end_to_end(self):
        """G2 at the gate level: cert-check exits 0 (VALID) on a good artifact."""
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as td:
            src_path = Path(td) / "src.ward0"
            src_path.write_text(composed_source("w1_payment_chain"), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "harness.cert_check",
                 str(CERT_DIR / "w1_payment_chain.proof"), "--source", str(src_path)],
                cwd=str(Path(__file__).resolve().parent.parent),
                capture_output=True,
                text=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("VALID", proc.stdout)

    def test_w6_tested_tier_no_proof_obligation(self):
        """T6: a Tested-tier entry carries proof 'tested' and still validates."""
        cert = load_cert("w6_crud_handler")
        fn = cert["functions"][0]
        self.assertEqual(fn["tier"], "Tested")
        self.assertEqual(fn["proof"], "tested")
        problems = validate(cert, composed_source("w6_crud_handler"))
        self.assertEqual(problems, [])


class G3TamperTest(unittest.TestCase):
    """Tampering must invalidate the certificate (exit 1 / violations)."""

    def _check_tampered(self, cert: dict, source: str) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            cert_path = td / "tampered.proof"
            cert_path.write_text(json.dumps(cert), encoding="utf-8")
            src_path = td / "src.ward0"
            src_path.write_text(source, encoding="utf-8")
            ok, problems = check_file(cert_path, src_path)
        self.assertFalse(ok, "tampered certificate must not validate")
        return problems

    def test_tampered_source_invalidates(self):
        """Modify the source → source_sha256 rebinding fails."""
        src = composed_source("w1_payment_chain")
        tampered = src.replace("token: str", "token:  str")  # whitespace change
        self.assertNotEqual(tampered, src)
        problems = self._check_tampered(load_cert("w1_payment_chain"), tampered)
        self.assertTrue(any("source_sha256" in p for p in problems), problems)

    def test_tampered_trust_string_invalidates(self):
        """Empty a trust string → manifest check fails."""
        cert = load_cert("w1_payment_chain")
        cert["trust_boundary"][0]["trust"] = ""
        problems = self._check_tampered(cert, composed_source("w1_payment_chain"))
        self.assertTrue(any("trust" in p for p in problems), problems)

    def test_tampered_verdict_invalidates(self):
        """Flip a recorded verdict → verdict-consistency check fails."""
        cert = load_cert("w1_payment_chain")
        cert["verdict"] = "INVALID"
        problems = self._check_tampered(cert, composed_source("w1_payment_chain"))
        self.assertTrue(any("verdict" in p for p in problems), problems)

    def test_tampered_proof_outcome_invalidates(self):
        """Flip a function's proof outcome → verdict recomputation fails."""
        cert = load_cert("w1_payment_chain")
        cert["functions"][0]["proof"] = "failed"
        problems = self._check_tampered(cert, composed_source("w1_payment_chain"))
        self.assertTrue(any("verdict" in p for p in problems), problems)

    def test_removed_extern_from_manifest_invalidates(self):
        """Drop an extern from the manifest → manifest-vs-source check fails."""
        cert = load_cert("w1_payment_chain")
        cert["trust_boundary"].pop(0)
        problems = self._check_tampered(cert, composed_source("w1_payment_chain"))
        self.assertTrue(any("trust_boundary" in p for p in problems), problems)

    def test_wrong_format_invalidates(self):
        cert = load_cert("w1_payment_chain")
        cert["format"] = "ward-cert/v9.9"
        problems = self._check_tampered(cert, composed_source("w1_payment_chain"))
        self.assertTrue(any("format" in p for p in problems), problems)

    def test_malformed_types_never_raise(self):
        """The checker must never raise on malformed-but-parseable JSON —
        non-list functions, non-dict toolchain, non-list trust_boundary all
        report INVALID cleanly (exit-1 contract, no traceback)."""
        for key, value in (
            ("functions", {"pay": "Proven"}),   # non-list
            ("functions", "pay"),                # non-list
            ("toolchain", "dafny 4.11"),         # non-dict
            ("trust_boundary", "stripe_charge"),  # non-list
        ):
            cert = load_cert("w1_payment_chain")
            cert[key] = value
            problems = validate(cert, composed_source("w1_payment_chain"))
            self.assertTrue(problems, f"{key}={value!r} must be INVALID, not crash")


class TauFieldTest(unittest.TestCase):
    """The .proof records the I1 Specification Tightness tau per fn (advisory)."""

    def _cert(self, tightness=None):
        from harness.certificate import emit_certificate
        return emit_certificate(
            module="w1_payment_chain",
            source="extern fn auth_check(...)\n\nfn pay(...)",
            dafny_src="method auth_check(...)\nmethod pay(...)",
            tiers={"pay": "Proven"},
            outcomes={"pay": {"proof": "verified", "verified": 1, "errors": 0, "verify_s": 1.0}},
            trust_manifest=[],
            toolchain={"ward_core": "0.1", "dafny": "4.11.0", "z3": "4.12.1"},
            tightness=tightness,
        )

    def test_tau_recorded_when_passed(self):
        cert = self._cert(tightness={
            "pay": {"tau": 0.234, "action": "keep", "unevaluable": None}
        })
        fn = cert["functions"][0]
        self.assertEqual(fn["tau"], 0.234)
        self.assertEqual(fn["tau_advisory"], "keep")
        self.assertIsNone(fn["tau_unevaluable"])
        # advisory: tau never changes the tier or the verdict
        self.assertEqual(fn["tier"], "Proven")
        self.assertEqual(cert["verdict"], "VALID")

    def test_tau_null_without_measurement(self):
        cert = self._cert(tightness=None)
        fn = cert["functions"][0]
        self.assertIsNone(fn["tau"])
        self.assertIsNone(fn["tau_advisory"])
        self.assertIsNone(fn["tau_unevaluable"])

    def test_tested_tier_also_carries_tau(self):
        from harness.certificate import emit_certificate
        cert = emit_certificate(
            module="w6_crud_handler",
            source="fn crud(...)",
            dafny_src="method crud(...)",
            tiers={"crud": "Tested"},
            outcomes={"crud": {"verify_s": 0.5}},
            trust_manifest=[],
            toolchain={},
            tightness={"crud": {"tau": 0.8, "action": "keep", "unevaluable": None}},
        )
        fn = cert["functions"][0]
        self.assertEqual(fn["tier"], "Tested")
        self.assertEqual(fn["proof"], "tested")
        self.assertEqual(fn["tau"], 0.8)  # measured even without a proof obligation

    def test_probe_one_records_tau(self):
        # end-to-end: probe_one emits a .proof whose fn entry carries the
        # caller's measured tau (w5 = 0.321 from the calibration)
        from harness.certificate import probe_one
        r = probe_one("w5_currency_roundtrip")
        fn = r["cert"]["functions"][0]
        self.assertEqual(fn["name"], "round_trip")
        self.assertAlmostEqual(fn["tau"], 0.321, places=3)


class StandaloneTest(unittest.TestCase):
    """The checker module must not pull in Dafny/lark/transpiler."""

    def test_cert_check_imports_are_stdlib_only(self):
        import subprocess
        import sys

        # Run in a fresh interpreter so sys.modules reflects cert_check alone.
        code = (
            "import sys\n"
            "import harness.cert_check\n"
            "bad = [m for m in sys.modules\n"
            "       if 'dafny' in m or 'lark' in m or 'transpiler' in m or 'wardcore' in m]\n"
            "print('BAD=' + repr(bad) if bad else 'CLEAN')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("CLEAN", proc.stdout)


class Level2RederiveTest(unittest.TestCase):
    """Phase-3 standalone checker: re-transpile the bound source and verify
    emitted_dafny_sha256 independently (closes the Level-1 honesty gap)."""

    def test_level2_rederives_all_committed_artifacts(self):
        """Every committed cert_probe artifact re-derives its emitted hash."""
        for tid in PROBE_TASKS:
            cert = load_cert(tid)
            problems = validate_level2(cert, composed_source(tid))
            self.assertEqual(problems, [], f"{tid} Level-2: {problems}")

    def test_level2_byte_identical_vs_real_transpiler(self):
        """The stdlib-only re-transpiler matches Ward0Transpiler byte-for-byte
        on the composed sources (both enforce modes)."""
        from harness.cert_rederive import transpile as re_transpile
        from transpiler.transpiler import Ward0Transpiler

        for tid in PROBE_TASKS:
            src = composed_source(tid)
            for enforce in (False, True):
                ref = Ward0Transpiler(enforce_boundary=enforce).transpile(src)
                self.assertEqual(
                    re_transpile(src, enforce=enforce), ref,
                    f"{tid} enforce={enforce} must be byte-identical",
                )

    def test_level2_tampered_source_fails_rederive(self):
        """Change the source → re-transpiled hash no longer matches the
        recorded emitted_dafny_sha256 → INVALID. (The mutation would also trip
        Level-1 rebinding; this test isolates that Level-2 alone catches it.)"""
        cert = load_cert("w1_payment_chain")
        src = composed_source("w1_payment_chain")
        # flip a comparison bound in the caller: different Dafny emission
        tampered = src.replace("amount <= 100", "amount <= 101")
        self.assertNotEqual(tampered, src)
        problems = validate_level2(cert, tampered)
        self.assertTrue(any("re-derivation" in p for p in problems), problems)

    def test_level2_tampered_emitted_hash_fails(self):
        """Rewrite emitted_dafny_sha256 in the proof → Level-2 flags it."""
        cert = load_cert("w1_payment_chain")
        cert["emitted_dafny_sha256"] = "0" * 64
        problems = validate_level2(cert, composed_source("w1_payment_chain"))
        self.assertTrue(any("re-derivation" in p for p in problems), problems)

    def test_level2_missing_emitted_hash_fails(self):
        cert = load_cert("w1_payment_chain")
        del cert["emitted_dafny_sha256"]
        problems = validate_level2(cert, composed_source("w1_payment_chain"))
        self.assertTrue(any("emitted_dafny_sha256" in p for p in problems), problems)

    def test_level2_never_raises_on_bad_source(self):
        """A source that cannot re-transpile reports INVALID cleanly (no raise)."""
        cert = load_cert("w1_payment_chain")
        garbage = "fn broken( {\n"
        problems = validate_level2(cert, garbage)
        self.assertTrue(problems, "garbage source must be a named violation")

    def test_level2_never_raises_on_non_dict_toolchain(self):
        """A malformed toolchain (the Level-1 hardening class) must not make
        validate_level2 raise — same never-raises contract as Level-1. Level-1's
        structure check flags the bad toolchain separately; Level-2 still
        re-derives (enforce defaults to False) without crashing."""
        cert = load_cert("w1_payment_chain")
        cert["toolchain"] = "dafny 4.11"  # truthy non-dict
        problems = validate_level2(cert, composed_source("w1_payment_chain"))
        self.assertIsInstance(problems, list)  # never raises, clean list

    def test_level2_exit_codes_cli(self):
        """CLI: VALID exit 0 with --re-derive on a good artifact, INVALID exit 1
        when the source has been tampered with."""
        import subprocess
        import sys

        root = str(Path(__file__).resolve().parent.parent)
        with tempfile.TemporaryDirectory() as td:
            src_path = Path(td) / "src.ward0"
            src_path.write_text(composed_source("w1_payment_chain"), encoding="utf-8")
            good = subprocess.run(
                [sys.executable, "-m", "harness.cert_check",
                 str(CERT_DIR / "w1_payment_chain.proof"),
                 "--source", str(src_path), "--re-derive"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn("VALID", good.stdout)

            tampered = src_path.with_name("tampered.ward0")
            tampered.write_text(
                composed_source("w1_payment_chain").replace("amount <= 100", "amount <= 101"),
                encoding="utf-8",
            )
            bad = subprocess.run(
                [sys.executable, "-m", "harness.cert_check",
                 str(CERT_DIR / "w1_payment_chain.proof"),
                 "--source", str(tampered), "--re-derive"],
                cwd=root, capture_output=True, text=True,
            )
            self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
            self.assertIn("re-derivation", bad.stdout)

    def test_level2_requires_source(self):
        """--re-derive without --source is a named violation, not a crash."""
        import subprocess
        import sys

        root = str(Path(__file__).resolve().parent.parent)
        proc = subprocess.run(
            [sys.executable, "-m", "harness.cert_check",
             str(CERT_DIR / "w1_payment_chain.proof"), "--re-derive"],
            cwd=root, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("re-derivation", proc.stdout)


if __name__ == "__main__":
    unittest.main()
