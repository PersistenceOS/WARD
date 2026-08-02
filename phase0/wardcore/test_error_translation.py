"""Unit tests for the ward-core error translation pass (Phase-2 week 7, R8/E8).

No Dafny in this suite (unit-only; the E8 gate runner owns the real dafny
verify leg). Covers: raw-output parsing (the real Dafny 4.11 shapes captured
on this machine: postcondition, precondition-at-call-site, assertion, parse,
counterexample block), classification, the emitted-Dafny line map (fn /
extern / wrapper attribution, clause indices), clause rendering to ward0
surface text, timeout translation, and the full translate_errors surface
round-trip.
"""

import unittest

from wardcore.elaborator import Elaborator
from wardcore.error_translation import (
    EmittedLineMap,
    StructuredError,
    SurfaceLocation,
    annotate_tightness,
    classify,
    parse_dafny_output,
    render_expr,
    translate_errors,
    translate_timeout,
)
from wardcore.ir import Binary, Call, IntLit, Paren, Var

# Real Dafny 4.11.0 output shapes captured on this machine.
POST_DETAIL = """\
C:/Users/Legion/AppData/Local/Temp/fail_post.dfy(4,0): Error: a postcondition could not be proved on this return path
  |
4 | {
  | ^

C:/Users/Legion/AppData/Local/Temp/fail_post.dfy(3,12): Related location: this is the postcondition that could not be proved
  |
3 |   ensures r == amount
  |             ^^


Dafny program verifier finished with 0 verified, 1 error
"""

PRE_DETAIL = """\
C:/Users/Legion/AppData/Local/Temp/fail_pre.dfy(10,12): Error: a precondition for this call could not be proved
   |
10 |   x := debit(0);
   |             ^

C:/Users/Legion/AppData/Local/Temp/fail_pre.dfy(2,18): Related location: this is the precondition that could not be proved
  |
2 |   requires amount > 0
  |                   ^


Dafny program verifier finished with 1 verified, 1 error
"""

ASSERT_DETAIL = """\
C:/Users/Legion/AppData/Local/Temp/fail_assert.dfy(4,2): Error: assertion might not hold
  |
4 |   assert a >= 10;
  |   ^^^^^^


Dafny program verifier finished with 0 verified, 1 error
"""

PARSE_DETAIL = """\
C:/Users/Legion/AppData/Local/Temp/fail_parse.dfy(1,12): Error: closeparen expected
  |
1 | method bad( {
  |             ^

1 parse errors detected in fail_parse.dfy
"""

CEX_DETAIL = """\
C:/Users/Legion/AppData/Local/Temp/fail_cex2.dfy(3,0): Error: a postcondition could not be proved on this return path
 Related counterexample:
 WARNING: the following counterexample may be inconsistent or invalid. See dafny.org/dafny/DafnyRef/DafnyRef#sec-counterexamples
 C:/Users/Legion/AppData/Local/Temp/fail_cex2.dfy(3,0): initial state:
 assume 0 == a;
 C:/Users/Legion/AppData/Local/Temp/fail_cex2.dfy(4,8):
 assume 0 == a && 0 == r;

  |
3 | {
  | ^

C:/Users/Legion/AppData/Local/Temp/fail_cex2.dfy(2,12): Related location: this is the postcondition that could not be proved
  |
2 |   ensures r > a
  |             ^


Dafny program verifier finished with 0 verified, 1 error
"""


class TestParse(unittest.TestCase):
    def test_postcondition_parses_with_related_location(self):
        issues = parse_dafny_output(POST_DETAIL)
        errors = [i for i in issues if i.sev == "Error"]
        related = [i for i in issues if i.sev == "Related location"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].msg, "a postcondition could not be proved on this return path")
        self.assertEqual(errors[0].line, 4)
        self.assertEqual(len(related), 1)
        self.assertIn("postcondition", related[0].msg)

    def test_precondition_parses(self):
        issues = parse_dafny_output(PRE_DETAIL)
        errors = [i for i in issues if i.sev == "Error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("precondition for this call", errors[0].msg)
        self.assertEqual(errors[0].line, 10)

    def test_assertion_parses(self):
        issues = parse_dafny_output(ASSERT_DETAIL)
        errors = [i for i in issues if i.sev == "Error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(classify(errors[0].msg), "assertion")

    def test_parse_error_parses(self):
        issues = parse_dafny_output(PARSE_DETAIL)
        errors = [i for i in issues if i.sev == "Error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(classify(errors[0].msg), "parse")

    def test_counterexample_block_attached_to_issue(self):
        issues = parse_dafny_output(CEX_DETAIL)
        errors = [i for i in issues if i.sev == "Error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].cex, {"a": "0", "r": "0"})


class TestClassify(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(classify("a postcondition could not be proved on this return path"), "postcondition")
        self.assertEqual(classify("a precondition for this call could not be proved"), "precondition")
        self.assertEqual(classify("assertion might not hold"), "assertion")
        self.assertEqual(classify("a decreases clause could not be proved"), "termination")
        self.assertEqual(classify("closeparen expected"), "parse")
        self.assertEqual(classify("some unknown message"), "other")


class TestLineMap(unittest.TestCase):
    def test_fn_and_extern_attribution(self):
        emitted = (
            "datatype Result<T, E> = Ok(value: T) | Err(error: E)\n"
            "method {:extern}{:axiom} ledger_debit(amount: int) returns (result: Result<int, str>)\n"
            "  requires amount > 0\n"
            "  ensures is_ok(result) == (amount <= 1000000)\n"
            "method transfer(amount: int) returns (result: Result<int, str>)\n"
            "  requires amount > 0\n"
            "  ensures is_ok(result) == (amount <= 1000000)\n"
            "{\n"
            "  var w0 := ledger_debit_checked(amount);\n"
            "  if w0.Err? {\n"
            "    return Err(w0.error);\n"
            "  }\n"
            "  return Ok(w0.value);\n"
            "}\n"
        )
        lm = EmittedLineMap(emitted, {"ledger_debit"})
        self.assertEqual(lm.annotate(1).clause, "datatype")
        ext = lm.annotate(2)
        self.assertEqual((ext.kind, ext.fn, ext.clause), ("extern", "ledger_debit", "sig"))
        self.assertEqual(lm.annotate(3).clause, "requires")
        self.assertEqual(lm.annotate(4).clause, "ensures")
        fn = lm.annotate(5)
        self.assertEqual((fn.kind, fn.fn), ("fn", "transfer"))
        self.assertEqual(lm.annotate(6).clause, "requires")
        self.assertEqual(lm.annotate(7).clause, "ensures")
        self.assertEqual(lm.annotate(8).clause, "body")
        # the wrapper's requires maps back to the extern's ward0 name
        wrapped = EmittedLineMap(
            "method {:extern}{:axiom} ledger_debit(amount: int) returns (result: Result<int, str>)\n"
            "method ledger_debit_checked(amount: int) returns (result: Result<int, str>)\n"
            "  requires amount > 0\n",
            {"ledger_debit"},
        )
        w = wrapped.annotate(3)
        self.assertEqual((w.kind, w.fn, w.clause), ("extern", "ledger_debit", "requires"))

    def test_line_map_out_of_range(self):
        lm = EmittedLineMap("method m() returns (r: int)\n{\n}\n")
        loc = lm.annotate(99)
        self.assertEqual(loc.emitted_line, 99)


class TestRender(unittest.TestCase):
    def test_render_expr_surface(self):
        # is_ok(result) == (amount <= 1000000)
        e = Binary(
            "==",
            Call("is_ok", (Var("result"),)),
            Paren(Binary("<=", Var("amount"), IntLit(1000000))),
        )
        self.assertEqual(render_expr(e), "is_ok(result) == (amount <= 1000000)")


class TestTimeout(unittest.TestCase):
    def test_runner_timeout_string(self):
        t = translate_timeout("verify wall-clock timeout after 120s (process tree killed): <proc>")
        self.assertIsNotNone(t)
        self.assertEqual(t.kind, "timeout")
        self.assertIn("120s", t.violated_obligation)

    def test_non_timeout_returns_none(self):
        self.assertIsNone(translate_timeout("a normal dafny error"))


class TestTranslate(unittest.TestCase):
    def setUp(self):
        # a broken fn: postcondition bounds amount but the body never enforces it
        self.src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000);
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000)
{
    return Ok(amount);
}
"""

    def _translate(self, detail):
        elab = Elaborator(enforce_boundary=True)
        emitted = elab.transpile(self.src)
        return elab.diagnose(detail, emitted)

    def test_postcondition_triple_surface_terms(self):
        # the emitted module's ensures line is what Dafny would point at.
        # The emitter renders `is_ok(result)` as `result.Ok?`, so transfer's
        # ensures is the LAST `ensures` line (transfer emits after the extern
        # and its `_checked` wrapper).
        elab = Elaborator(enforce_boundary=True)
        emitted = elab.transpile(self.src)
        lines = emitted.splitlines()
        ens_lines = [i for i, l in enumerate(lines, 1) if l.strip().startswith("ensures")]
        ens_line = ens_lines[-1]  # transfer's ensures
        # build a realistic detail: Error at the body, Related at the ensures
        detail = (
            f"task.dfy({ens_line + 1},0): Error: a postcondition could not be proved on this return path\n"
            f"task.dfy({ens_line},10): Related location: this is the postcondition that could not be proved\n"
        )
        triples = self._translate(detail)
        self.assertEqual(len(triples), 1)
        t = triples[0]
        self.assertEqual(t.kind, "postcondition")
        self.assertEqual(t.location.fn, "transfer")
        self.assertEqual(t.location.clause, "ensures")
        self.assertEqual(t.location.surface(), "transfer:ensures")
        self.assertIn("postcondition of transfer", t.violated_obligation)
        self.assertIn("is_ok(result)", t.violated_obligation)
        self.assertIn("transfer", t.surface)
        self.assertNotIn("task.dfy", t.location.surface())
        self.assertNotIn("task.dfy", t.surface)
        d = t.to_dict()
        self.assertEqual(d["kind"], "postcondition")
        self.assertEqual(d["location"]["surface"], "transfer:ensures")

    def test_precondition_triple_names_callee_requires(self):
        elab = Elaborator(enforce_boundary=False)
        src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000);
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount >= 0
  ensures is_ok(result)
{
    var r: Result<int, str> = ledger_debit(amount);
    return r;
}
"""
        emitted = elab.transpile(src)
        lines = emitted.splitlines()
        # the extern's requires (first `requires amount > 0` line; the caller's
        # requires is `amount >= 0` so it cannot match)
        req_line = next(i for i, l in enumerate(lines, 1) if l.strip() == "requires amount > 0")
        detail = (
            f"task.dfy({req_line + 3},10): Error: a precondition for this call could not be proved\n"
            f"task.dfy({req_line},10): Related location: this is the precondition that could not be proved\n"
        )
        triples = elab.diagnose(detail, emitted)
        self.assertEqual(len(triples), 1)
        t = triples[0]
        self.assertEqual(t.kind, "precondition")
        self.assertEqual(t.location.fn, "ledger_debit")
        self.assertEqual(t.location.clause, "requires")
        self.assertIn("precondition of ledger_debit", t.violated_obligation)
        self.assertIn("amount > 0", t.violated_obligation)

    def test_parse_detail_translates(self):
        triples = translate_errors(PARSE_DETAIL, emitted="method bad( {\n")
        self.assertEqual(len(triples), 1)
        self.assertEqual(triples[0].kind, "parse")
        self.assertIn("syntax error", triples[0].surface)

    def test_no_emitted_is_empty(self):
        self.assertEqual(translate_errors(POST_DETAIL), [])

    def test_counterexample_flows_through_translate(self):
        elab = Elaborator(enforce_boundary=True)
        emitted = elab.transpile(self.src)
        triples = elab.diagnose(CEX_DETAIL, emitted)
        self.assertEqual(len(triples), 1)
        self.assertEqual(triples[0].counterexample, {"a": "0", "r": "0"})


class TestTightnessAdvisory(unittest.TestCase):
    """I1: the advisory tightness result is appended to the repair-loop
    triples — tau + the specific weak ensures clauses for a Proven fn below
    TAU0 (the concrete spec-fixing target)."""

    def _triple(self, fn="transfer", kind="postcondition") -> StructuredError:
        return StructuredError(
            kind=kind,
            location=SurfaceLocation(fn=fn, kind="fn", clause="ensures"),
            violated_obligation=f"postcondition of {fn}",
            surface=f"{fn}: postcondition failed",
        )

    def _tightness(self, fn="transfer", tau=0.0, clauses=None):
        return {
            fn: {
                "fn": fn,
                "declared_tier": "Proven",
                "tau": tau,
                "admissible": 10,
                "zero_count": 0,
                "unevaluable": None,
                "recommended_tier": "Contracted",
                "action": "demote",
                "clauses": clauses or [
                    {"kind": "ensures", "text": "true", "tau": 0.0, "weak": True},
                ],
            }
        }

    def test_demoted_proven_gets_tau_and_weak_clause_appended(self):
        t = self._triple()
        out = annotate_tightness([t], self._tightness())
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kind, "postcondition")  # kind untouched
        self.assertIn("tau=0.0", out[0].surface)
        self.assertIn("ensures true", out[0].surface)
        self.assertIn("I1 tightness", out[0].tightness_advisory)
        d = out[0].to_dict()
        self.assertEqual(d["kind"], "postcondition")
        self.assertIn("tightness_advisory", d)
        self.assertIn("tau=0.0", d["surface"])

    def test_kept_or_unevaluable_fns_untouched(self):
        t = self._triple()
        tight = {"transfer": {"fn": "transfer", "declared_tier": "Proven",
                               "tau": 0.8, "action": "keep", "clauses": []}}
        out = annotate_tightness([t], tight)
        self.assertEqual(out[0].surface, "transfer: postcondition failed")
        self.assertEqual(out[0].tightness_advisory, "")
        # unevaluable (action != demote) also untouched
        uneval = {"transfer": {"fn": "transfer", "declared_tier": "Proven",
                                "tau": None, "action": "flag-unevaluable",
                                "clauses": []}}
        out2 = annotate_tightness([t], uneval)
        self.assertEqual(out2[0].tightness_advisory, "")

    def test_other_fn_triple_untouched_weak_proven_gets_standalone(self):
        # a weak Proven fn with NO error triple gets its own kind="tightness"
        # advisory triple (vacuous spec that Dafny verified — the anti-slop
        # case), while an unrelated fn's triple stays clean
        t = self._triple(fn="other_fn")
        out = annotate_tightness([t], self._tightness(fn="weak_proven"))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].surface, "other_fn: postcondition failed")
        self.assertEqual(out[1].kind, "tightness")
        self.assertEqual(out[1].location.fn, "weak_proven")
        self.assertIn("tau=0.0", out[1].surface)
        self.assertIn("ensures true", out[1].surface)

    def test_no_tightness_returns_triples_unchanged(self):
        t = self._triple()
        out = annotate_tightness([t], None)
        self.assertEqual(out, [t])
        out2 = annotate_tightness([t], {})
        self.assertEqual(out2, [t])

    def test_end_to_end_diagnose_appends_advisory(self):
        # a vacuous Proven fn in a module that FAILS verification for a
        # different reason: the failing triple keeps its kind, the weak
        # Proven fn gets the tau advisory in the same diagnose() result
        src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000);
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount >= 0
  ensures true
{
    var r: Result<int, str> = ledger_debit(amount);
    return r;
}
"""
        elab = Elaborator(enforce_boundary=False)
        emitted = elab.transpile(src)
        # transfer's ensures true is vacuous -> the pass demotes it (advisory)
        self.assertEqual(elab.tightness["transfer"]["action"], "demote")
        lines = emitted.splitlines()
        req_line = next(i for i, l in enumerate(lines, 1) if l.strip() == "requires amount > 0")
        detail = (
            f"task.dfy({req_line + 3},10): Error: a precondition for this call could not be proved\n"
            f"task.dfy({req_line},10): Related location: this is the precondition that could not be proved\n"
        )
        triples = elab.diagnose(detail, emitted)
        kinds = [t.kind for t in triples]
        self.assertIn("precondition", kinds)  # the real Dafny error
        self.assertIn("tightness", kinds)  # the I1 advisory for transfer
        adv = next(t for t in triples if t.kind == "tightness")
        self.assertIn("transfer", adv.surface)
        self.assertIn("tau=", adv.surface)


if __name__ == "__main__":
    unittest.main()
