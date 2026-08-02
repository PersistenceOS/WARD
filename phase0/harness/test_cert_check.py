"""Tests for the standalone .proof checker (Phase C, gate E9: G2 + G3).

G2 fidelity: the checker's verdict agrees with the harness's measured verdicts
on the committed cert_probe artifacts (all VALID — every task verified).
G3 tamper-evidence: modifying source, a trust string, or a recorded verdict
invalidates the certificate (exit 1 / non-empty violations).

The checker itself must stay stdlib-only (no dafny/lark/transpiler imports) —
the standalone claim. The tests here may use the harness to *recompose* the
source for rebinding, but the module under test never does.
"""

import json
import tempfile
import unittest
from pathlib import Path

from harness.cert_check import validate, check_file
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


if __name__ == "__main__":
    unittest.main()
