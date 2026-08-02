"""Unit tests for the Z3-direct backend (Phase-2 E10, '3+' row first slice).

No Dafny in this suite (unit-only; the E10 gate runner owns the dafny parity
leg). Covers: trivial contracts verify, broken contracts fail with a
counterexample, extern contracts (requires obligation + ensures assumption),
Result datatype round-trips, if/else path forking, Tested never reaches a
solver, loop invariant rule, counterexample rendering.
"""

import unittest

from wardcore.elaborator import Elaborator
from wardcore.z3_backend import Z3ModuleVerifier


def _verify(src: str, enforce: bool = True) -> dict:
    """Elaborate a ward0 module and run the Z3-direct verifier on it."""
    elab = Elaborator(enforce_boundary=enforce)
    elab.transpile(src)
    return Z3ModuleVerifier(elab.module, elab.tier_plan).verify_all()


def _outcome(src: str, fn: str = "main") -> str:
    return _verify(src)[fn]["outcome"]


class TestTrivialContracts(unittest.TestCase):
    def test_trivial_pass(self):
        src = "fn main(x: int, y: int) -> int\n  requires x >= 0\n  ensures result == x + y\n{\n  return x + y;\n}\n"
        self.assertEqual(_outcome(src), "verified")

    def test_broken_fails_with_counterexample(self):
        src = "fn main(x: int, y: int) -> int\n  requires x >= 0\n  ensures result == x + 1\n{\n  return x + y;\n}\n"
        rec = _verify(src)["main"]
        self.assertEqual(rec["outcome"], "failed")
        self.assertIsNotNone(rec["counterexample"])
        # the counterexample names the violating inputs (y != 1)
        self.assertIn("x", rec["counterexample"])
        self.assertIn("y", rec["counterexample"])

    def test_bool_contract(self):
        src = "fn main(b: bool) -> bool\n  ensures result == b\n{\n  return b;\n}\n"
        self.assertEqual(_outcome(src), "verified")

    def test_str_round_trip(self):
        src = "fn main(t: str) -> str\n  ensures result == t\n{\n  return t;\n}\n"
        self.assertEqual(_outcome(src), "verified")


class TestExternContracts(unittest.TestCase):
    EXTERN = (
        "extern fn gate(x: int) -> Result<int, str>\n"
        "  requires x > 0\n"
        "  ensures is_ok(result) == (x <= 100)\n"
        "  ensures unwrap_ok(result) == x\n"
        ";\n"
        'trust: "oracle reference stub"\n'
        "\n"
    )

    def test_extern_contract_honored(self):
        src = self.EXTERN + (
            "fn main(x: int) -> Result<int, str>\n"
            "  requires x > 0\n"
            "  ensures is_ok(result) == (x <= 100)\n"
            "{\n"
            "  var r: Result<int, str> = gate(x);\n"
            "  if is_err(r) {\n"
            "    return Err(unwrap_err(r));\n"
            "  }\n"
            "  return Ok(unwrap_ok(r));\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "verified")

    def test_caller_must_prove_extern_requires(self):
        # main drops the requires the extern demands (x > 0): the obligation
        # x > 0 is unprovable at the call site -> failed with a cex
        src = self.EXTERN + (
            "fn main(x: int) -> Result<int, str>\n"
            "  requires x >= 0\n"
            "  ensures is_ok(result)\n"
            "{\n"
            "  var r: Result<int, str> = gate(x);\n"
            "  if is_err(r) {\n"
            "    return Err(unwrap_err(r));\n"
            "  }\n"
            "  return Ok(unwrap_ok(r));\n"
            "}\n"
        )
        rec = _verify(src)["main"]
        self.assertEqual(rec["outcome"], "failed")
        self.assertIsNotNone(rec["counterexample"])
        # the model must exhibit an x that violates x > 0
        self.assertIn("x", rec["counterexample"])

    def test_result_datatype_round_trip(self):
        # is_ok / unwrap_err / Ok / Err through an extern boundary
        src = self.EXTERN + (
            "fn main(x: int) -> Result<int, str>\n"
            "  requires x > 0\n"
            "  ensures is_ok(result) == (x <= 100)\n"
            "  ensures is_err(result) == (x > 100)\n"
            "{\n"
            "  var r: Result<int, str> = gate(x);\n"
            "  if is_ok(r) {\n"
            "    return Ok(unwrap_ok(r));\n"
            "  }\n"
            "  return Err(unwrap_err(r));\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "verified")


class TestControlFlow(unittest.TestCase):
    def test_if_else_both_paths(self):
        src = (
            "fn main(x: int) -> int\n"
            "  requires x >= 0\n"
            "  ensures result >= 0\n"
            "{\n"
            "  if x > 10 {\n"
            "    return x - 10;\n"
            "  }\n"
            "  return x;\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "verified")

    def test_missing_return_on_path_fails(self):
        src = (
            "fn main(x: int) -> int\n"
            "  requires x >= 0\n"
            "  ensures result >= 0\n"
            "{\n"
            "  if x > 10 {\n"
            "    return x;\n"
            "  }\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "failed")

    def test_unit_implicit_return(self):
        src = (
            "extern fn log_note(msg: int) -> Result<Unit, str>\n"
            "  requires msg >= 0\n"
            "  ensures is_ok(result)\n"
            ";\n"
            'trust: "oracle reference stub"\n'
            "\n"
            "fn main(x: int) -> Unit\n"
            "  requires x >= 0\n"
            "{\n"
            "  var r: Result<Unit, str> = log_note(x);\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "verified")


class TestTierRouting(unittest.TestCase):
    def test_tested_never_reaches_solver(self):
        src = (
            "extern fn gate(x: int) -> Result<int, str>\n"
            "  requires x > 0\n"
            "  ensures is_ok(result) == (x <= 100)\n"
            ";\n"
            'trust: "oracle reference stub"\n'
            "\n"
            "fn main(x: int) -> Result<int, str>\n"
            "  requires x > 0\n"
            "  tier: Tested\n"
            "  ensures is_ok(result)\n"
            "{\n"
            "  return gate(x);\n"
            "}\n"
        )
        rec = _verify(src)["main"]
        self.assertEqual(rec["outcome"], "not_run")
        self.assertEqual(rec["verify_s"], 0.0)


class TestLoops(unittest.TestCase):
    def test_loop_with_invariant(self):
        src = (
            "fn main(n: int) -> int\n"
            "  requires n >= 0\n"
            "  ensures result >= 0\n"
            "{\n"
            "  var acc: int = 0;\n"
            "  for i in range(0, n)\n"
            "    invariant acc >= 0\n"
            "  {\n"
            "    acc += 1;\n"
            "  }\n"
            "  return acc;\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "verified")

    def test_loop_constant_bounds_unroll(self):
        src = (
            "fn main() -> int\n"
            "  ensures result == 3\n"
            "{\n"
            "  var acc: int = 0;\n"
            "  for i in range(0, 3)\n"
            "  {\n"
            "    acc += 1;\n"
            "  }\n"
            "  return acc;\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "verified")

    def test_loop_without_invariant_symbolic_is_not_proved(self):
        src = (
            "fn main(n: int) -> int\n"
            "  requires n >= 0\n"
            "  ensures result >= 0\n"
            "{\n"
            "  var acc: int = 0;\n"
            "  for i in range(0, n)\n"
            "  {\n"
            "    acc += 1;\n"
            "  }\n"
            "  return acc;\n"
            "}\n"
        )
        self.assertEqual(_outcome(src), "not_proved")


if __name__ == "__main__":
    unittest.main()
