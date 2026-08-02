"""Unit tests for the ward-core tier pass (Phase-2 week 3, R6/T6/R7).

Covers: the T6 routing table (Proven full / Contracted bounded+fallback /
Tested no-proof), the `tier:` surface annotation, `{:verify false}` emission
for Tested fns only, the T6 cross-tier rule (proof-carrying fns must not call
Tested fns), and the R7 EffortMeter schema (verify_s floats per obligation).
"""

from __future__ import annotations

import unittest

from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.ir import Tier
from wardcore.tier_pass import (
    CONTRACTED_VERIFY_LIMIT_S,
    EffortMeter,
    TierPass,
    VerifyRoute,
)

# A multi-function module exercising all three tiers: Proven entry, Contracted
# helper (bounded), Tested helper (no proof obligation).
MULTI = """\
extern fn db_get(key: int) -> Result<int, str>
  requires key > 0
ensures is_ok(result) == (key < 1000);
trust: "oracle reference stub"

tier: Proven
fn entry(user_id: int) -> Result<int, str>
  requires user_id > 0
  ensures is_ok(result) == (user_id < 1000)
{
    var v: Result<int, str> = db_get(user_id);
    if is_ok(v) != (user_id < 1000) {
        return Err("contract violation");
    }
    if is_err(v) {
        return Err(unwrap_err(v));
    }
    return Ok(unwrap_ok(v));
}

tier: Contracted
fn bounded(x: int) -> int
  requires x > 0
  ensures result > 0
{
    return x;
}

tier: Tested
fn tested(x: int) -> int
  ensures result == x + 1
{
    return x + 2;
}
"""


class TestTierRouting(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()
        self.module = self.elab.desugar(MULTI)

    def test_tier_annotation_attaches_in_declaration_order(self):
        self.assertEqual([f.tier for f in self.module.fns], [
            Tier.PROVEN,
            Tier.CONTRACTED,
            Tier.TESTED,
        ])

    def test_default_tier_is_proven(self):
        # no tier: lines -> every fn is Proven (E1 corpus unaffected)
        src = MULTI.replace("tier: Proven\n", "").replace("tier: Contracted\n", "").replace("tier: Tested\n", "")
        module = self.elab.desugar(src)
        self.assertTrue(all(f.tier is Tier.PROVEN for f in module.fns))

    def test_routing_table(self):
        plan = TierPass().plan(self.module)
        o_entry = plan.for_fn("entry")
        self.assertIs(o_entry.route, VerifyRoute.VERIFY_FULL)
        self.assertIsNone(o_entry.verify_limit_s)
        self.assertFalse(o_entry.fallback_allowed)

        o_bounded = plan.for_fn("bounded")
        self.assertIs(o_bounded.route, VerifyRoute.VERIFY_BOUNDED)
        self.assertEqual(o_bounded.verify_limit_s, CONTRACTED_VERIFY_LIMIT_S)
        self.assertTrue(o_bounded.fallback_allowed)

        o_tested = plan.for_fn("tested")
        self.assertIs(o_tested.route, VerifyRoute.NO_PROOF)
        self.assertIsNone(o_tested.verify_limit_s)
        self.assertFalse(o_tested.fallback_allowed)

    def test_cross_tier_rule_proven_must_not_call_tested(self):
        bad = """\
tier: Proven
fn caller(x: int) -> int
  requires x > 0
  ensures result > 0
{
    var r: int = helper(x);
    return r;
}

tier: Tested
fn helper(x: int) -> int
  ensures result > 0
{
    return x;
}
"""
        problems = TierPass().validate(self.elab.desugar(bad))
        self.assertTrue(any("calls Tested function helper" in p for p in problems))

    def test_cross_tier_hard_error_through_pipeline(self):
        bad = """\
tier: Proven
fn caller(x: int) -> int
  requires x > 0
  ensures result > 0
{
    var r: int = helper(x);
    return r;
}

tier: Tested
fn helper(x: int) -> int
  ensures result > 0
{
    return x;
}
"""
        with self.assertRaises(ElaborationError) as ctx:
            Elaborator().transpile(bad)
        self.assertIn("must not depend on an unverified callee (T6)", str(ctx.exception))

    def test_tested_can_call_proven(self):
        # the reverse direction is fine: Tested fns have no proof obligation
        ok = """\
tier: Proven
fn helper(x: int) -> int
  requires x > 0
  ensures result > 0
{
    return x;
}

tier: Tested
fn caller(x: int) -> int
  ensures result == helper(x)
{
    return helper(x);
}
"""
        plan = TierPass().run(self.elab.desugar(ok))
        self.assertIs(plan.for_fn("caller").route, VerifyRoute.NO_PROOF)


class TestEmission(unittest.TestCase):
    def test_tested_fn_emits_verify_false_only(self):
        emitted = Elaborator().transpile(MULTI)
        self.assertIn("method {:verify false} tested(", emitted)
        self.assertNotIn("{:verify false} entry", emitted)
        self.assertNotIn("{:verify false} bounded", emitted)

    def test_no_tier_lines_emit_plain_methods(self):
        src = MULTI.replace("tier: Proven\n", "").replace("tier: Contracted\n", "").replace("tier: Tested\n", "")
        emitted = Elaborator().transpile(src)
        self.assertNotIn("{:verify false}", emitted)

    def test_tier_plan_exposed_on_elaborator(self):
        elab = Elaborator()
        elab.transpile(MULTI)
        self.assertIsNotNone(elab.tier_plan)
        self.assertEqual(elab.tier_plan.for_fn("tested").route.value, "no_proof")


class TestEffortMeter(unittest.TestCase):
    def setUp(self):
        self.module = Elaborator().desugar(MULTI)
        self.plan = TierPass().plan(self.module)
        self.meter = EffortMeter(self.plan)

    def test_schema_verify_s_field(self):
        self.meter.record("entry", 2.5)
        report = self.meter.report()
        by_fn = {r["fn"]: r for r in report}
        self.assertEqual(by_fn["entry"]["verify_s"], 2.5)
        self.assertEqual(by_fn["entry"]["tier"], "Proven")
        self.assertEqual(by_fn["entry"]["route"], "full")

    def test_tested_never_run_actual_s_zero(self):
        # NO_PROOF obligations are never run — the Phase-1 Tested invariant
        # (verify_s stays 0) is preserved at the core level
        report = self.meter.report()
        by_fn = {r["fn"]: r for r in report}
        self.assertEqual(by_fn["tested"]["verify_s"], 0.0)
        self.assertEqual(by_fn["tested"]["outcome"], "not_run")
        self.assertEqual(self.meter.total_s(), 0.0)

    def test_record_updates_total(self):
        self.meter.record("entry", 1.25)
        self.meter.record("bounded", 0.75)
        self.assertAlmostEqual(self.meter.total_s(), 2.0)
        self.assertEqual(self.meter.for_fn("bounded").outcome, "verified")

    def test_record_unknown_fn_raises(self):
        with self.assertRaises(KeyError):
            self.meter.record("nope", 1.0)


if __name__ == "__main__":
    unittest.main()
