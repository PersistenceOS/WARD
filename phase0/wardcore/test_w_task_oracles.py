"""Unit tests for the multi-function w-task oracles (w9-w12): schema sanity,
annotation attachment, E4b dependency resolution, E5-real per-function tier
routing + effects, and the T6 cross-tier rule — all through the typed pipeline,
with NO Dafny (the e5_real_gate runner owns the verify/hidden-test probes).

The four oracles are the first multi-function ward0 modules: each declares
per-fn tiers/effects, per-extern effect/dep references, and a module-level dep
header, so the unit suite asserts the elaborator + core passes consume them
exactly as the JSON descriptors declare.
"""

from __future__ import annotations

import unittest

from wardcore.dep_pass import DepPass
from wardcore.effects_pass import parse_effect_set
from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.ir import Tier
from wardcore.tier_pass import VerifyRoute
from wardcore.e5_real_gate import W_TASK_IDS, compose_module, load_task, reference_src

# The T6 cross-tier rule: a proof-carrying (Proven/Contracted) fn must not call
# a Tested fn. w12 was fixed during authoring (release_hold is Proven, not
# Tested); the negative probe below asserts the rule still fires.
TESTED_HELPER_BAD = """\
dep: escrow@^2.0.0

extern fn escrow_hold(account: int, amount: int) -> Result<int, str>
  requires account > 0
  requires amount > 0
  ensures is_ok(result) == (amount <= 500);
trust: "oracle reference stub"
effect: mut
dep: escrow@2.3.0

tier: Proven
fn hold_and_release(account: int, amount: int) -> Result<int, str>
  requires account > 0
  requires amount > 0
  ensures is_ok(result) == (amount <= 500)
{
    var h: Result<int, str> = escrow_hold(account, amount);
    if is_err(h) {
        return Err(unwrap_err(h));
    }
    return Ok(unwrap_ok(h));
}

tier: Tested
fn release_hold(hold_id: int) -> Result<int, str>
  requires hold_id > 0
  ensures is_ok(result) == (hold_id < 1000)
{
    var r: Result<int, str> = escrow_hold(hold_id, 1);
    if is_err(r) {
        return Err(unwrap_err(r));
    }
    return Ok(unwrap_ok(r));
}

tier: Proven
fn call_tested(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result)
{
    var r: Result<int, str> = release_hold(x);
    return r;
}
"""


class TestOracleSchemaSanity(unittest.TestCase):
    def test_every_task_has_complete_metadata(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                self.assertEqual(desc["arm_kind"], "w")
                self.assertTrue(desc["buggy"])  # stubs violate their contracts
                self.assertIn("fn", desc)  # entry fn name
                self.assertTrue(desc["externs"], "oracle must declare externs")
                self.assertIn("tiers", desc)
                self.assertIn("effects", desc)
                self.assertIn("deps", desc)
                # every fn in the ward0 source has a tier + effect set declared
                src = reference_src(tid)
                fn_names = [
                    line.split("(")[0].removeprefix("fn ").strip()
                    for line in src.splitlines()
                    if line.strip().startswith("fn ")
                ]
                for fn in fn_names:
                    self.assertIn(fn, desc["tiers"], f"{fn} missing tier")
                    self.assertIn(fn, desc["effects"], f"{fn} missing effects")
                # entry fn is declared
                self.assertIn(desc["fn"], desc["tiers"])

    def test_externs_carry_effect_and_dep(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                for stub in desc["externs"]:
                    self.assertIn("effect", stub, f"{stub['name']} missing effect")
                    self.assertIn("dep", stub, f"{stub['name']} missing dep reference")
                    self.assertIn("contract", stub)
                    self.assertIn("contract_py", stub)

    def test_all_three_tier_gates_exercised(self):
        """Proven/Contracted/Tested all appear across the four oracles."""
        tiers = set()
        for tid in W_TASK_IDS:
            tiers.update(load_task(tid)["tiers"].values())
        self.assertEqual(tiers, {"Proven", "Contracted", "Tested"})


class TestOracleElaboration(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator(enforce_boundary=True)

    def test_every_oracle_elaborates_clean(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                emitted = self.elab.transpile(compose_module(desc, reference_src(tid)))
                self.assertTrue(emitted.strip())
                # T4: every extern call site routed through _checked (enforce)
                for stub in desc["externs"]:
                    self.assertIn(f"{stub['name']}_checked(", emitted)

    def test_module_deps_attached(self):
        desc = load_task("w9_inventory_order")
        module = self.elab.desugar(compose_module(desc, reference_src("w9_inventory_order")))
        self.assertEqual(
            module.deps, ("inventory@^2.0.0", "orders@^1.0.0")
        )

    def test_extern_dep_references_attached(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                module = self.elab.desugar(compose_module(desc, reference_src(tid)))
                externs = {e.name: e for e in module.externs}
                for stub in desc["externs"]:
                    self.assertEqual(externs[stub["name"]].dep, stub["dep"])

    def test_per_fn_tiers_attached(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                module = self.elab.desugar(compose_module(desc, reference_src(tid)))
                fns = {f.name: f for f in module.fns}
                for fn_name, tier in desc["tiers"].items():
                    self.assertEqual(fns[fn_name].tier, Tier(tier))

    def test_per_fn_effects_attached(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                module = self.elab.desugar(compose_module(desc, reference_src(tid)))
                fns = {f.name: f for f in module.fns}
                for fn_name, effects in desc["effects"].items():
                    want = parse_effect_set(", ".join(effects))
                    self.assertEqual(fns[fn_name].effects, want)


class TestOracleE4bDeps(unittest.TestCase):
    def test_every_reference_resolves(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                elab = Elaborator()
                module = elab.desugar(compose_module(desc, reference_src(tid)))
                plan = DepPass().resolve(module)
                self.assertTrue(plan.all_resolved)
                self.assertEqual(
                    len(plan.records),
                    len([s for s in desc["externs"] if s.get("dep")]),
                )
                for rec in plan.records:
                    self.assertEqual(rec.status, "resolved")

    def test_version_drift_on_oracle_fails(self):
        """An out-of-range reference on a real oracle module fails elaboration."""
        desc = load_task("w12_hold_release")
        bad = compose_module(desc, reference_src("w12_hold_release")).replace(
            "dep: escrow@2.3.0", "dep: escrow@9.9.9", 1
        )
        with self.assertRaises(ElaborationError) as ctx:
            Elaborator().transpile(bad)
        self.assertIn("outside the pinned range", str(ctx.exception))


class TestOracleE5RealRouting(unittest.TestCase):
    def test_tier_routes_match_declared(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                elab = Elaborator()
                elab.transpile(compose_module(desc, reference_src(tid)))
                plan = elab.tier_plan
                for fn_name, tier in desc["tiers"].items():
                    o = plan.for_fn(fn_name)
                    expected = {
                        "Proven": VerifyRoute.VERIFY_FULL,
                        "Contracted": VerifyRoute.VERIFY_BOUNDED,
                        "Tested": VerifyRoute.NO_PROOF,
                    }[tier]
                    self.assertIs(o.route, expected, f"{tid}.{fn_name}")

    def test_contracted_budget_and_fallback(self):
        desc = load_task("w9_inventory_order")
        elab = Elaborator()
        elab.transpile(compose_module(desc, reference_src("w9_inventory_order")))
        o = elab.tier_plan.for_fn("reserve_inventory")
        self.assertIs(o.route, VerifyRoute.VERIFY_BOUNDED)
        self.assertIsNotNone(o.verify_limit_s)
        self.assertTrue(o.fallback_allowed)

    def test_tested_emits_verify_false_only_for_tested(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                elab = Elaborator()
                emitted = elab.transpile(compose_module(desc, reference_src(tid)))
                tested = [n for n, t in desc["tiers"].items() if t == "Tested"]
                if tested:
                    for n in tested:
                        self.assertIn(f"{{:verify false}} {n}(", emitted)
                else:
                    self.assertNotIn("{:verify false}", emitted)

    def test_effects_inferred_match_declared(self):
        for tid in W_TASK_IDS:
            with self.subTest(tid=tid):
                desc = load_task(tid)
                elab = Elaborator()
                elab.transpile(compose_module(desc, reference_src(tid)))
                inferred = elab.effects_inferred
                for fn_name, effects in desc["effects"].items():
                    want = parse_effect_set(", ".join(effects))
                    self.assertEqual(inferred[fn_name], want, f"{tid}.{fn_name}")

    def test_effect_escape_on_oracle_fails(self):
        """A fn calling a net extern without declaring net fails (E4 probe on a
        real oracle module)."""
        desc = load_task("w10_session_ledger")
        src = reference_src("w10_session_ledger").replace(
            "effects: net\nfn auth_session", "effects: db\nfn auth_session", 1
        )
        with self.assertRaises(ElaborationError) as ctx:
            Elaborator().transpile(compose_module(desc, src))
        self.assertIn("calls undeclared effect net", str(ctx.exception))


class TestOracleCrossTier(unittest.TestCase):
    def test_proven_calling_tested_is_hard_error(self):
        with self.assertRaises(ElaborationError) as ctx:
            Elaborator().transpile(TESTED_HELPER_BAD)
        self.assertIn("must not depend on an unverified callee", str(ctx.exception))

    def test_w12_fixed_shape_passes(self):
        """w12 was re-authored to Proven+Contracted+Proven (a Proven entry must
        not call a Tested helper) — the shipped shape must elaborate clean."""
        desc = load_task("w12_hold_release")
        self.assertEqual(desc["tiers"]["hold_funds"], "Contracted")
        self.assertEqual(desc["tiers"]["release_hold"], "Proven")
        Elaborator().transpile(compose_module(desc, reference_src("w12_hold_release")))


if __name__ == "__main__":
    unittest.main()
