"""Tests for wardcore.tightness (engine) + wardcore.tightness_gate (I1 gate)."""

import unittest

from wardcore.tightness import build_output_space, clause_tightness, compute_tightness
from wardcore.tightness_gate import TAU0, TightnessGate


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


class TestClauseTightness(unittest.TestCase):
    """I1 per-clause breakdown: which specific ensures clauses pin the output
    (the repair-loop fix target)."""

    def test_marks_weak_vacuous_clause(self):
        # one strong clause + one vacuous clause: only the vacuous one is weak
        clauses = clause_tightness([("x", "int")], "int", [],
                                   ["result == x", "true"], TAU0)
        self.assertEqual(len(clauses), 2)
        strong, weak = clauses[0], clauses[1]
        self.assertFalse(strong["weak"])
        self.assertGreaterEqual(strong["tau"], 0.9)
        self.assertTrue(weak["weak"])
        self.assertLess(weak["tau"], 0.05)

    def test_all_clauses_have_kind_ensures(self):
        clauses = clause_tightness([("x", "int")], "int", ["x > 0"],
                                   ["result == x"], TAU0)
        self.assertTrue(all(c["kind"] == "ensures" for c in clauses))
        self.assertEqual(len(clauses), 1)

    def test_unevaluable_clause_never_weak(self):
        # a quantifier ensures clause is unevaluable -> tau None, weak False
        # (the honest bounded-domain limit: never named as the fix target)
        clauses = clause_tightness([("xs", "List<int>")], "bool", [],
                                   ["forall i in range(0, 1) :: result"], TAU0)
        self.assertEqual(len(clauses), 1)
        self.assertIsNone(clauses[0]["tau"])
        self.assertFalse(clauses[0]["weak"])

    def test_w5_style_is_ok_clause_weak_at_strict_tau0(self):
        # w5-style: pins is_ok but not the unwrapped value -> weak at the
        # strict 0.5 threshold, kept at the calibrated 0.2
        clauses = clause_tightness(
            [("amount", "int")], "Result<int, str>", [],
            ["is_ok(result) == (amount <= 500)"], 0.5,
        )
        self.assertTrue(clauses[0]["weak"])
        clauses2 = clause_tightness(
            [("amount", "int")], "Result<int, str>", [],
            ["is_ok(result) == (amount <= 500)"], TAU0,
        )
        self.assertFalse(clauses2[0]["weak"])


if __name__ == "__main__":
    unittest.main()
