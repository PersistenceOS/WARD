"""Unit tests for the ward-core dependency-resolution pass (Phase-2 week 5, E4b).

Covers: version-range grammar (exact/caret/tilde/wildcard), contains
semantics, in-range passes, out-of-range (version drift) / unresolved /
ambiguous hard errors, malformed spec/version hard errors, the `dep:`
surface annotations (module header vs extern reference), and the week-5
boundary (externs without a reference are unconstrained — E1 parity).
"""

from __future__ import annotations

import unittest

from wardcore.dep_pass import DepPass, parse_range
from wardcore.elaborator import Elaborator, ElaborationError

# A module that pins two dependencies and references them in-range.
SRC = """\
dep: ledger@^2.0.0
dep: auth@~1.2.0

extern fn ledger_debit(balance: int, amount: int) -> Result<int, str>
  requires balance >= 0
  requires amount >= 0
  ensures is_ok(result) == (amount <= balance);
dep: ledger@2.4.1
trust: "oracle reference stub"

extern fn auth_check(user_id: int) -> Result<Unit, str>
  requires user_id > 0
  ensures is_ok(result) == (user_id < 1000);
dep: auth@1.2.0
trust: "oracle reference stub"

fn pay(user_id: int, amount: int) -> Result<Unit, str>
  requires user_id > 0
  requires amount > 0
  ensures is_ok(result) == (user_id < 1000 and amount <= 100)
{
    var a: Result<Unit, str> = auth_check(user_id);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    var b: Result<int, str> = ledger_debit(1000, amount);
    if is_err(b) {
        return Err(unwrap_err(b));
    }
    return Ok(unwrap_ok(a));
}
"""


class TestVersionRangeGrammar(unittest.TestCase):
    def test_exact(self):
        r = parse_range("2.4.1")
        self.assertTrue(r.contains((2, 4, 1)))
        self.assertFalse(r.contains((2, 4, 2)))

    def test_caret(self):
        r = parse_range("^2.0.0")
        self.assertTrue(r.contains((2, 0, 0)))
        self.assertTrue(r.contains((2, 4, 1)))
        self.assertTrue(r.contains((2, 99, 0)))
        self.assertFalse(r.contains((3, 0, 0)))  # major bump = drift
        self.assertFalse(r.contains((1, 9, 9)))

    def test_tilde(self):
        r = parse_range("~1.0.0")
        self.assertTrue(r.contains((1, 0, 0)))
        self.assertTrue(r.contains((1, 0, 9)))
        self.assertFalse(r.contains((1, 1, 0)))  # minor bump = drift
        self.assertFalse(r.contains((2, 0, 0)))

    def test_wildcard_major(self):
        r = parse_range("2.*")
        self.assertTrue(r.contains((2, 0, 0)))
        self.assertTrue(r.contains((2, 7, 3)))
        self.assertFalse(r.contains((3, 0, 0)))

    def test_wildcard_minor(self):
        r = parse_range("1.2.*")
        self.assertTrue(r.contains((1, 2, 0)))
        self.assertTrue(r.contains((1, 2, 99)))
        self.assertFalse(r.contains((1, 3, 0)))

    def test_malformed_version_hard_error(self):
        with self.assertRaises(ElaborationError) as ctx:
            parse_range("2.4")  # two components
        self.assertIn("malformed", str(ctx.exception))
        with self.assertRaises(ElaborationError):
            parse_range("abc")

    def test_malformed_wildcard_hard_error(self):
        with self.assertRaises(ElaborationError) as ctx:
            parse_range("*")
        self.assertIn("malformed range", str(ctx.exception))


class TestResolution(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()
        self.module = self.elab.desugar(SRC)

    def test_in_range_reference_resolves(self):
        resolution = DepPass().resolve(self.module)
        self.assertTrue(resolution.all_resolved)
        self.assertEqual(resolution.for_extern("ledger_debit").status, "resolved")
        self.assertEqual(resolution.for_extern("auth_check").status, "resolved")

    def test_validate_clean(self):
        self.assertEqual(DepPass().validate(self.module), [])

    def test_module_deps_attached(self):
        self.assertEqual(self.module.deps, ("ledger@^2.0.0", "auth@~1.2.0"))

    def test_extern_refs_attached_in_declaration_order(self):
        by_name = {e.name: e for e in self.module.externs}
        self.assertEqual(by_name["ledger_debit"].dep, "ledger@2.4.1")
        self.assertEqual(by_name["auth_check"].dep, "auth@1.2.0")


class TestVersionDrift(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()

    def test_out_of_range_is_problem(self):
        # version-drift probe: declared ^2.0.0, referenced 3.1.0
        src = SRC.replace("dep: ledger@2.4.1", "dep: ledger@3.1.0")
        problems = DepPass().validate(self.elab.desugar(src))
        self.assertTrue(any("outside the pinned range ^2.0.0" in p for p in problems))

    def test_out_of_range_hard_error_through_pipeline(self):
        src = SRC.replace("dep: ledger@2.4.1", "dep: ledger@3.1.0")
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.transpile(src)
        self.assertIn("outside the pinned range", str(ctx.exception))

    def test_unresolved_is_problem(self):
        # references a dependency with no declared range
        src = SRC.replace("dep: ledger@2.4.1", "dep: payments@1.0.0")
        problems = DepPass().validate(self.elab.desugar(src))
        self.assertTrue(any("unresolved" in p and "payments" in p for p in problems))

    def test_unresolved_hard_error_through_pipeline(self):
        src = SRC.replace("dep: ledger@2.4.1", "dep: payments@1.0.0")
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.transpile(src)
        self.assertIn("unresolved", str(ctx.exception))

    def test_ambiguous_is_problem(self):
        # two declared ranges for the same name -> cannot pick one. The second
        # range must go in the MODULE HEADER (before the first def) — a dep:
        # line after the last def would be read as an extern reference.
        src = SRC.replace(
            "dep: ledger@^2.0.0", "dep: ledger@^2.0.0\ndep: ledger@^1.0.0"
        )
        problems = DepPass().validate(self.elab.desugar(src))
        self.assertTrue(any("ambiguous" in p for p in problems))

    def test_ambiguous_hard_error_through_pipeline(self):
        src = SRC.replace(
            "dep: ledger@^2.0.0", "dep: ledger@^2.0.0\ndep: ledger@^1.0.0"
        )
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.transpile(src)
        self.assertIn("ambiguous", str(ctx.exception))

    def test_malformed_declaration_hard_error(self):
        src = "dep: ledger@nonsense\n" + SRC
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.desugar(src)
            # the malformed spec is caught by DepPass.resolve -> parse_range
            DepPass().run(self.elab.module)
        self.assertIn("malformed", str(ctx.exception))

    def test_malformed_reference_hard_error(self):
        src = SRC.replace("dep: ledger@2.4.1", "dep: ledger@two.four.one")
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.transpile(src)
        self.assertIn("malformed version", str(ctx.exception))


class TestWeek5Boundary(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()

    def test_extern_without_ref_unconstrained(self):
        # E1 parity: the 70-reference corpus declares no deps at all — externs
        # without a `dep:` reference must never block elaboration
        bare = """\
extern fn stub(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
trust: "oracle reference stub"

fn f(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    var a: Result<int, str> = stub(x);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    return Ok(unwrap_ok(a));
}
"""
        module = self.elab.desugar(bare)
        self.assertEqual(module.deps, ())
        self.assertEqual(module.externs[0].dep, "")
        self.assertEqual(DepPass().validate(module), [])

    def test_declared_range_unused_is_fine(self):
        # a module can pin a dependency nothing references yet (manifest ahead
        # of code) — only REFERENCED deps are validated this week. The extra
        # pin goes in the header (before the first def), not after the last.
        src = SRC.replace(
            "dep: auth@~1.2.0", "dep: auth@~1.2.0\ndep: analytics@^5.0.0"
        )
        problems = DepPass().validate(self.elab.desugar(src))
        self.assertEqual(problems, [])

    def test_pipeline_exposes_resolution_plan(self):
        elab = Elaborator()
        elab.transpile(SRC)
        self.assertIsNotNone(elab.dep_resolution)
        self.assertTrue(elab.dep_resolution.all_resolved)


if __name__ == "__main__":
    unittest.main()
