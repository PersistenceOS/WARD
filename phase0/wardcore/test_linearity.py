"""Unit tests for the ward-core linearity pass (Phase-2 week 6, T7/E6).

No Dafny in this suite (unit-only; the E6 gate runner owns the dafny-verify +
hidden-test leg). Covers: annotation attachment (positional), inference
(flow through calls and moves, transitive through fn calls), consume-exactly-
once on every path (copy/drop/double-use/loop/minting), the inference-only
boundary (no linear extern = unconstrained), and the elaborator pipeline.
"""

import unittest

from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.linearity_pass import LinearPass

MONEY_SRC = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000)
{
    var r: Result<int, str> = ledger_debit(amount);
    if is_err(r) {
        return Err(unwrap_err(r));
    }
    return Ok(unwrap_ok(r));
}
"""


def _module(src: str):
    return Elaborator().desugar(src)


def _problems(src: str) -> list[str]:
    return LinearPass().validate(_module(src))


def _transpile(src: str) -> str:
    return Elaborator(enforce_boundary=True).transpile(src)


class TestLinearAnnotation(unittest.TestCase):
    def test_linear_annotation_marks_extern_param(self):
        module = _module(MONEY_SRC)
        ext = module.externs[0]
        self.assertEqual(ext.name, "ledger_debit")
        self.assertTrue(ext.params[0].linear)
        # no OTHER param is linear (only the one named by `linear:`)
        self.assertFalse(any(p.linear for p in ext.params[1:]))

    def test_linear_annotation_is_positional_not_declaration_order(self):
        # extern A has NO linear param; extern B's `linear:` must attach to B
        src = """\
extern fn auth(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
trust: "oracle reference stub"

extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn f(x: int, amount: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result)
{
    var a: Result<int, str> = auth(x);
    var r: Result<int, str> = ledger_debit(amount);
    return Ok(0);
}
"""
        module = _module(src)
        ext_a, ext_b = module.externs
        self.assertFalse(any(p.linear for p in ext_a.params))
        self.assertTrue(ext_b.params[0].linear)

    def test_linear_unknown_param_is_error(self):
        src = MONEY_SRC.replace("linear: amount", "linear: bogus")
        with self.assertRaises(ElaborationError):
            _module(src)

    def test_linear_before_any_extern_is_error(self):
        src = "linear: amount\n" + MONEY_SRC
        with self.assertRaises(ElaborationError):
            _module(src)


class TestInference(unittest.TestCase):
    def test_param_flowing_into_linear_extern_is_inferred(self):
        inferred = LinearPass().infer(_module(MONEY_SRC))
        self.assertEqual(inferred["transfer"], {"amount"})

    def test_inference_is_transitive_through_fn_calls(self):
        src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn helper(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000)
{
    var r: Result<int, str> = ledger_debit(amount);
    if is_err(r) {
        return Err(unwrap_err(r));
    }
    return Ok(unwrap_ok(r));
}

fn caller(amount: int) -> Result<int, str>
  requires amount > 0
{
    return helper(amount);
}
"""
        inferred = LinearPass().infer(_module(src))
        self.assertEqual(inferred["helper"], {"amount"})
        self.assertEqual(inferred["caller"], {"amount"})

    def test_inference_through_local_move(self):
        src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn transfer(amount: int) -> Result<int, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 1000000)
{
    var m: int = amount;
    var r: Result<int, str> = ledger_debit(m);
    if is_err(r) {
        return Err(unwrap_err(r));
    }
    return Ok(unwrap_ok(r));
}
"""
        inferred = LinearPass().infer(_module(src))
        self.assertEqual(inferred["transfer"], {"amount"})
        self.assertEqual(_problems(src), [])


class TestConsumeExactlyOnce(unittest.TestCase):
    def test_money_transfer_is_clean(self):
        self.assertEqual(_problems(MONEY_SRC), [])

    def test_copy_in_condition_is_error(self):
        # wrap the WHOLE body in `if amount > 0` — the condition reads `amount`
        # in a non-consuming position (the copy); `r` stays in scope below
        src = MONEY_SRC.replace(
            "{\n    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {\n        return Err(unwrap_err(r));\n    }\n    return Ok(unwrap_ok(r));\n}",
            "{\n    if amount > 0 {\n        var r: Result<int, str> = ledger_debit(amount);\n        if is_err(r) {\n            return Err(unwrap_err(r));\n        }\n        return Ok(unwrap_ok(r));\n    }\n    return Err(\"neg\");\n}",
        )
        problems = _problems(src)
        self.assertTrue(any("is copied" in p for p in problems))

    def test_copy_to_nonlinear_param_is_error(self):
        # the leak must happen where `amount` is linear — in the SAME fn that
        # flows it into the linear extern (a separate fn's param is never
        # inferred linear: that is the inference-only rule)
        src = MONEY_SRC.replace(
            "    var r: Result<int, str> = ledger_debit(amount);",
            "    var b: int = read_balance(amount);\n    var r: Result<int, str> = ledger_debit(amount);",
        )
        src = src.replace(
            "fn transfer(",
            "fn read_balance(x: int) -> int\n  ensures result >= 0\n{\n    return x;\n}\n\nfn transfer(",
        )
        problems = _problems(src)
        self.assertTrue(any("transfer: linear value amount is copied" in p for p in problems))

    def test_drop_never_consumed_is_error(self):
        src = MONEY_SRC.replace(
            "    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {",
            "    return Ok(0);\n    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {",
        )
        problems = _problems(src)
        self.assertTrue(any("is dropped" in p for p in problems))

    def test_double_use_is_error(self):
        src = MONEY_SRC.replace(
            "    return Ok(unwrap_ok(r));",
            "    var r2: Result<int, str> = ledger_debit(amount);\n    return Ok(unwrap_ok(r2));",
        )
        problems = _problems(src)
        self.assertTrue(any("consumed more than once" in p for p in problems))

    def test_linear_inside_loop_is_error(self):
        # `amount` mentioned inside the loop body (any position) is a hard
        # error — consumption count across iterations is not trackable in v0.1
        src = MONEY_SRC.replace(
            "{\n    var r: Result<int, str> = ledger_debit(amount);",
            "{\n    for i in range(0, 3) {\n        var unused: int = amount;\n    }\n    var r: Result<int, str> = ledger_debit(amount);",
        )
        problems = _problems(src)
        self.assertTrue(any("inside a loop" in p for p in problems))

    def test_minting_nonlinear_expr_is_error(self):
        src = MONEY_SRC.replace(
            "    var r: Result<int, str> = ledger_debit(amount);",
            "    var r: Result<int, str> = ledger_debit(500);",
        )
        problems = _problems(src)
        self.assertTrue(any("cannot consume" in p for p in problems))

    def test_path_split_drop_is_error(self):
        src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn conditional(amount: int, go: bool) -> Result<int, str>
  requires amount > 0
{
    if go {
        var r: Result<int, str> = ledger_debit(amount);
        return Ok(unwrap_ok(r));
    }
    return Err("skipped");
}
"""
        problems = _problems(src)
        self.assertTrue(any("is dropped" in p for p in problems))

    def test_consume_in_both_branches_is_clean(self):
        src = """\
extern fn ledger_debit(amount: int) -> Result<int, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 1000000);
linear: amount
trust: "oracle reference stub"

fn both(amount: int, go: bool) -> Result<int, str>
  requires amount > 0
{
    if go {
        var r: Result<int, str> = ledger_debit(amount);
        return Ok(unwrap_ok(r));
    }
    var r2: Result<int, str> = ledger_debit(amount);
    if is_err(r2) {
        return Err(unwrap_err(r2));
    }
    return Ok(unwrap_ok(r2));
}
"""
        self.assertEqual(_problems(src), [])


class TestInferenceOnlyBoundary(unittest.TestCase):
    def test_no_linear_extern_is_unconstrained(self):
        src = """\
extern fn log_note(msg: int) -> Result<int, str>
  requires msg >= 0
ensures is_ok(result) == (msg < 1000000);
trust: "oracle reference stub"

fn plain(x: int) -> Result<int, str>
  requires x >= 0
  ensures is_ok(result)
{
    var r: Result<int, str> = log_note(x);
    if is_err(r) {
        return Err(unwrap_err(r));
    }
    return Ok(unwrap_ok(r));
}
"""
        inferred = LinearPass().infer(_module(src))
        self.assertEqual(inferred["plain"], set())
        self.assertEqual(_problems(src), [])


class TestPipeline(unittest.TestCase):
    def test_pipeline_raises_on_drop(self):
        src = MONEY_SRC.replace(
            "    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {",
            "    return Ok(0);\n    var r: Result<int, str> = ledger_debit(amount);\n    if is_err(r) {",
        )
        with self.assertRaises(ElaborationError):
            _transpile(src)

    def test_pipeline_passes_money_transfer_and_exposes_inferred(self):
        elab = Elaborator(enforce_boundary=True)
        emitted = elab.transpile(MONEY_SRC)
        self.assertIn("method transfer(", emitted)
        self.assertEqual((elab.linearity_inferred or {}).get("transfer"), {"amount"})

    def test_pipeline_noop_without_linear_annotation(self):
        src = """\
extern fn log_note(msg: int) -> Result<int, str>
  requires msg >= 0
ensures is_ok(result) == (msg < 1000000);
trust: "oracle reference stub"

fn plain(x: int) -> Result<int, str>
  requires x >= 0
  ensures is_ok(result)
{
    var r: Result<int, str> = log_note(x);
    return r;
}
"""
        elab = Elaborator(enforce_boundary=True)
        elab.transpile(src)
        self.assertTrue(not (elab.linearity_inferred or {}) or not elab.linearity_inferred["plain"])


if __name__ == "__main__":
    unittest.main()
