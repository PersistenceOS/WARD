"""Tests for wardcore.tightness (engine) + wardcore.tightness_gate (I1 gate)."""

import unittest

from wardcore.tightness import build_output_space, compute_tightness
from wardcore.tightness_gate import TightnessGate


class TestTightnessEngine(unittest.TestCase):
    def test_vacuous_ensures_true_scores_zero(self):
        r = compute_tightness([("x", "int")], "int", [], ["true"])
        self.assertEqual(r["status"], "ok")
        self.assertIsNotNone(r["tau"])
        self.assertLess(r["tau"], 0.05)  # ~0.0: pins nothing

    def test_fully_pinning_scores_one(self):
        # result == x pins the output completely on the grid
        r = compute_tightness([("x", "int")], "int", [], ["result == x"])
        self.assertEqual(r["status"], "ok")
        self.assertGreaterEqual(r["tau"], 0.9)

    def test_is_ok_pin_on_result_unit(self):
        r = compute_tightness([("x", "int")], "Result<Unit, str>",
                              ["x > 0"], ["is_ok(result) == (x < 1000)"])
        self.assertEqual(r["status"], "ok")
        self.assertGreaterEqual(r["tau"], 0.9)  # pins Ok/Err fully

    def test_partial_constraint_scores_between(self):
        # pins result >= 0 over a grid that spans negatives and positives:
        # some outputs still permitted -> tau in (0,1)
        r = compute_tightness([("x", "int")], "int", [], ["result >= 0"])
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["tau"], 0.0)
        self.assertLess(r["tau"], 1.0)

    def test_quantifier_is_unevaluable(self):
        r = compute_tightness([("xs", "List<int>")], "bool",
                              [], ["forall i in range(0, 1) :: result"])
        self.assertEqual(r["status"], "unevaluable: quantifier/len")

    def test_unsupported_ret_is_unevaluable(self):
        r = compute_tightness([("x", "int")], "List<int>", [], ["true"])
        self.assertTrue(r["status"].startswith("unevaluable"))

    def test_output_spaces(self):
        self.assertEqual(len(build_output_space("Result<Unit, str>", [])), 2)
        self.assertGreater(len(build_output_space("Result<int, str>", ["5"])), 2)
        self.assertGreaterEqual(len(build_output_space("int", ["5"])), 2)
        self.assertEqual(len(build_output_space("bool", [])), 2)
        self.assertIsNone(build_output_space("List<int>", []))


class TestTightnessGate(unittest.TestCase):
    def _fn(self, name="f", ret="int", requires=(), ensures=("true",),
            params=None):
        return {"name": name, "params": params or [("x", "int")],
                "ret": ret, "requires": list(requires), "ensures": list(ensures)}

    def test_proven_tight_kept(self):
        fn = self._fn(ensures=("result == x",))
        res = TightnessGate().check(fn, "Proven")
        self.assertEqual(res.action, "keep")
        self.assertEqual(res.recommended_tier, "Proven")

    def test_proven_vacuous_demoted(self):
        fn = self._fn(ensures=("true",))
        res = TightnessGate().check(fn, "Proven")
        self.assertEqual(res.action, "demote")
        self.assertEqual(res.recommended_tier, "Contracted")

    def test_unevaluable_never_demotes(self):
        fn = self._fn(ensures=("forall i in range(0, 1) :: result",))
        res = TightnessGate().check(fn, "Proven")
        self.assertEqual(res.action, "flag-unevaluable")
        self.assertEqual(res.recommended_tier, "Proven")  # never demotes

    def test_tested_and_contracted_untouched(self):
        gate = TightnessGate()
        fn = self._fn(ensures=("true",))
        self.assertEqual(gate.check(fn, "Tested").action, "keep")
        self.assertEqual(gate.check(fn, "Contracted").action, "keep")

    def test_threshold_respects_tau0(self):
        gate = TightnessGate(tau0=0.9)
        fn = self._fn(ensures=("result >= 0",))  # tau in (0,1), below 0.9
        res = gate.check(fn, "Proven")
        self.assertEqual(res.action, "demote")

    def test_report_summary(self):
        gate = TightnessGate()
        fns = [(self._fn("a", ensures=("result == x",)), "Proven"),
               (self._fn("b", ensures=("true",)), "Proven")]
        rep = gate.report(fns)
        self.assertEqual(rep["checked"], 2)
        self.assertEqual(len(rep["demoted"]), 1)


if __name__ == "__main__":
    unittest.main()
