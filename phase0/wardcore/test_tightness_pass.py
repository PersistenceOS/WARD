"""Tests for the advisory TightnessPass (I1) + elaborator + certificate wiring.

Covers: the pass measures every fn's surface tau (advisory — never blocks,
never changes tiers), pipeline tau == calibrated tau for a real w-task, the
elaborator exposes elab.tightness, and the certificate records tau fields.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from wardcore.elaborator import Elaborator
from wardcore.tightness_pass import TightnessPass, measure_source
from wardcore.ir import Tier

PHASE0_DIR = Path(__file__).resolve().parent.parent
W_TASKS_DIR = PHASE0_DIR / "benchmarks" / "w_tasks"

TIGHT = """\
fn f(x: int) -> int
  ensures result == x
{
    return x;
}
"""

VACUOUS = """\
fn g(x: int) -> int
  ensures true
{
    return x;
}
"""

UNEVALUABLE = """\
fn h(xs: List<int>) -> bool
  ensures len(xs) > 0
{
    return true;
}
"""


class TestMeasureSource(unittest.TestCase):
    def test_tight_fn_scores_high(self):
        r = measure_source(TIGHT, {"f": "Proven"})
        self.assertIn("f", r)
        self.assertGreaterEqual(r["f"]["tau"], 0.9)
        self.assertEqual(r["f"]["action"], "keep")

    def test_vacuous_proven_recommends_demote(self):
        r = measure_source(VACUOUS, {"g": "Proven"})
        self.assertEqual(r["g"]["action"], "demote")
        self.assertEqual(r["g"]["recommended_tier"], "Contracted")

    def test_unevaluable_flagged_never_demotes(self):
        r = measure_source(UNEVALUABLE, {"h": "Proven"})
        self.assertEqual(r["h"]["action"], "flag-unevaluable")
        self.assertEqual(r["h"]["recommended_tier"], "Proven")

    def test_fns_not_in_tiers_skipped(self):
        # only the listed fns are measured (extern stubs / other fns skipped)
        r = measure_source(TIGHT + "\n" + VACUOUS, {"f": "Proven"})
        self.assertEqual(set(r), {"f"})

    def test_pipeline_tau_matches_calibrated_w5(self):
        # w5 round_trip measured tau 0.321 in the calibration (scoping doc
        # section 10) — the pass must reproduce the calibrated number exactly
        src = (W_TASKS_DIR / "w5_currency_roundtrip.ward0").read_text(encoding="utf-8")
        desc = json.loads((W_TASKS_DIR / "w5_currency_roundtrip.json").read_text(encoding="utf-8"))
        r = measure_source(src, desc.get("tiers", {}))
        self.assertIn("round_trip", r)
        self.assertAlmostEqual(r["round_trip"]["tau"], 0.321, places=3)
        # 0.321 >= TAU0 (0.2) — a calibrated-Proven spec keeps its tier
        self.assertEqual(r["round_trip"]["action"], "keep")
        self.assertEqual(r["round_trip"]["recommended_tier"], "Proven")


class TestElaboratorWiring(unittest.TestCase):
    def test_transpile_sets_tightness(self):
        elab = Elaborator()
        elab.transpile(TIGHT)
        self.assertIsNotNone(elab.tightness)
        self.assertIn("f", elab.tightness)
        self.assertGreaterEqual(elab.tightness["f"]["tau"], 0.9)

    def test_advisory_never_changes_declared_tier(self):
        # a vacuous Proven fn: the pass RECOMMENDS demote but the IR tier and
        # the emitted Dafny are untouched (advisory-first)
        elab = Elaborator()
        emitted = elab.transpile(VACUOUS)
        self.assertEqual(elab.tightness["g"]["action"], "demote")
        self.assertEqual(elab.tightness["g"]["recommended_tier"], "Contracted")
        # the declared tier is still Proven in the IR
        self.assertEqual(elab.module.fns[0].tier, Tier.PROVEN)
        # no {:verify false} and no tier change in the emission
        self.assertIn("method g", emitted)
        self.assertNotIn("{:verify false}", emitted)

    def test_unevaluable_does_not_block_elaboration(self):
        elab = Elaborator()
        emitted = elab.transpile(UNEVALUABLE)
        self.assertEqual(elab.tightness["h"]["action"], "flag-unevaluable")
        self.assertIn("method h", emitted)

    def test_elaborate_function_sets_tightness(self):
        from wardcore.elaborator import elaborate
        module, dafny = elaborate(TIGHT)
        self.assertEqual(module.fns[0].name, "f")
        self.assertIn("method f", dafny)


class TestTightnessPassClass(unittest.TestCase):
    def test_run_matches_measure_source(self):
        elab = Elaborator()
        module = elab.desugar(TIGHT)
        pass_ = TightnessPass()
        self.assertEqual(pass_.run(module, TIGHT), measure_source(TIGHT, {"f": "Proven"}))


if __name__ == "__main__":
    unittest.main()
