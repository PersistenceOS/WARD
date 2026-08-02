"""Unit tests for the ward-core elaborator front-end (Phase-2 week 2).

Covers: R2 extraction, R1 desugar (contract-as-annotation, no-semicolon rule,
trust stripping), type-check hard-error-on-ambiguity (undefined names/callees,
builtin arity, T1 contract purity, scope: params/loop vars/quant vars),
byte-parity emission vs Ward0Transpiler, and the T3-trust deferral.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from transpiler.transpiler import Ward0Transpiler, TranspileError

from wardcore.elaborator import Elaborator, ElaborationError, extract_candidate

PHASE0_DIR = Path(__file__).resolve().parent.parent
W_TASKS_DIR = PHASE0_DIR / "benchmarks" / "w_tasks"

FIB = """\
fn fib(n: int) -> int
  requires n >= 0 and n <= 40
  ensures result >= 0
{
    var a: int = 0;
    var b: int = 1;
    for i in range(0, n)
      invariant a >= 0 and b >= 0
    {
        var t: int = b;
        b = a + b;
        a = t;
    }
    return a;
}
"""

W1_EXTERNS = """\
extern fn auth_check(user_id: int) -> Result<Unit, str>
  requires user_id > 0
ensures is_ok(result) == (user_id < 1000);
trust: "oracle reference stub"
extern fn rate_limit(amount: int) -> Result<Unit, str>
  requires amount > 0
ensures is_ok(result) == (amount <= 5000);
trust: "oracle reference stub"
"""


def w1_source() -> str:
    desc = json.loads((W_TASKS_DIR / "w1_payment_chain.json").read_text(encoding="utf-8"))
    parts = []
    for stub in desc["externs"]:
        params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
        sig = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
        if stub.get("contract"):
            sig += "\n  " + stub["contract"]
        parts.append(sig + ';\ntrust: "oracle reference stub"')
    ref = (W_TASKS_DIR / "w1_payment_chain.ward0").read_text(encoding="utf-8")
    return "\n".join(parts) + "\n\n" + ref


class TestExtract(unittest.TestCase):
    def test_plain_passthrough(self):
        self.assertEqual(extract_candidate(FIB), FIB)

    def test_strips_fences(self):
        raw = "```ward0\n" + FIB + "```\n"
        self.assertEqual(extract_candidate(raw), FIB)

    def test_strips_dafny_echo_line(self):
        raw = "dafny\n" + FIB
        self.assertEqual(extract_candidate(raw), FIB)

    def test_strips_leading_prose(self):
        raw = "Here is my solution:\n\n" + FIB
        self.assertEqual(extract_candidate(raw), FIB)


class TestDesugar(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()

    def test_module_shape(self):
        module = self.elab.desugar(FIB)
        self.assertEqual(len(module.fns), 1)
        self.assertEqual(module.fns[0].name, "fib")
        self.assertEqual(module.fns[0].tier.value, "Proven")
        self.assertEqual(len(module.fns[0].requires), 1)
        self.assertEqual(len(module.fns[0].ensures), 1)
        self.assertEqual(len(module.externs), 0)

    def test_extern_desugar_and_trust_report(self):
        src = W1_EXTERNS + "\n" + FIB.replace("fn fib", "fn pay")
        module = self.elab.desugar(src)
        self.assertEqual([e.name for e in module.externs], ["auth_check", "rate_limit"])
        self.assertTrue(all(e.requires or e.ensures for e in module.externs))
        self.assertEqual(
            self.elab.trust_report,
            [
                {"stub": "auth_check", "trust": "oracle reference stub"},
                {"stub": "rate_limit", "trust": "oracle reference stub"},
            ],
        )

    def test_trust_annotation_attached(self):
        src = "trust: \"stripe SDK v14.2\"\n" + W1_EXTERNS + "\n" + FIB.replace("fn fib", "fn pay")
        module = self.elab.desugar(src)
        self.assertEqual(module.externs[0].trust, "stripe SDK v14.2")

    def test_contract_semicolon_is_hard_error(self):
        # R1: contracts are annotations, not statements — no trailing `;`
        bad = FIB.replace("  ensures result >= 0\n{", "  ensures result >= 0;\n{")
        with self.assertRaises(ElaborationError):
            self.elab.desugar(bad)

    def test_parse_error_mentions_no_semicolon_hint(self):
        bad = FIB.replace("  ensures result >= 0\n{", "  ensures result >= 0;\n{")
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.desugar(bad)
        self.assertIn("no trailing semicolon", str(ctx.exception))


class TestTypeCheck(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()

    def type_check_source(self, src: str) -> list[str]:
        module = self.elab.desugar(src)
        return self.elab.type_check(module)

    def test_clean_fib(self):
        self.assertEqual(self.type_check_source(FIB), [])

    def test_undefined_name(self):
        src = FIB.replace("var a: int = 0;", "var a: int = zzz;")
        problems = self.type_check_source(src)
        self.assertTrue(any("undefined name 'zzz'" in p for p in problems))

    def test_undefined_callee(self):
        src = FIB.replace("var a: int = 0;", "var a: int = no_such_fn(1);")
        problems = self.type_check_source(src)
        self.assertTrue(any("undefined callee 'no_such_fn'" in p for p in problems))

    def test_builtin_arity(self):
        src = FIB.replace("var a: int = 0;", "var a: bool = is_ok();")
        problems = self.type_check_source(src)
        self.assertTrue(any("takes exactly 1 argument" in p for p in problems))

    def test_contract_calls_method_is_rejected(self):
        # T1: contracts may only call builtins/constructors
        src = FIB.replace(
            "  ensures result >= 0\n{",
            "  ensures fib(n) >= 0\n{",
        )
        problems = self.type_check_source(src)
        self.assertTrue(any("no method calls in contracts" in p for p in problems))

    def test_contract_calls_extern_is_rejected(self):
        # T1: an extern in a contract term is an extern METHOD call in
        # expression position — Dafny rejects it with the Phase-1 R1 error;
        # the elaborator must hard-error before it reaches the backend.
        src = (
            "extern fn db_get(key: int) -> Result<int, str>\n"
            "  requires key > 0\n"
            "ensures is_ok(result) == (key < 1000);\n\n"
            "fn crud(key: int) -> Result<int, str>\n"
            "  requires key > 0\n"
            "  ensures is_ok(result) == is_ok(db_get(key))\n"
            "{\n"
            "    return db_get(key);\n"
            "}\n"
        )
        problems = self.type_check_source(src)
        self.assertTrue(any("no method calls in contracts" in p for p in problems))

    def test_loop_var_visible_in_invariants(self):
        # regression: `invariant n == i` must not flag `i` undefined
        src = "fn f(xs: List<int>) -> bool\n  requires len(xs) > 0\n  ensures result == true\n{\n    var n: int = 0;\n    for i in range(0, len(xs))\n      invariant n == i\n    {\n        n += 1;\n    }\n    return n == len(xs);\n}\n"
        self.assertEqual(self.type_check_source(src), [])

    def test_quant_var_scoped_to_body(self):
        # `forall j in range(0, i)` — j only in body, i must be outer
        src = "fn f(xs: List<int>) -> bool\n  requires len(xs) > 0\n  ensures result == (forall j in range(0, len(xs)) :: xs[j] > 0)\n{\n    var all_pos: bool = true;\n    for i in range(0, len(xs))\n      invariant all_pos == (forall j in range(0, i) :: xs[j] > 0)\n    {\n        if xs[i] <= 0 {\n            all_pos = false;\n        }\n    }\n    return all_pos;\n}\n"
        self.assertEqual(self.type_check_source(src), [])

    def test_t3_contract_mandatory_even_with_trust_deferred(self):
        # contract-less extern = hard error even with trust deferred (structural
        # type_check defers trust; the extern pass enforces it at transpile)
        src = "extern fn db_get(key: int) -> Result<int, str>;\n\n" + FIB.replace("fn fib", "fn pay")
        problems = self.type_check_source(src)
        self.assertTrue(any("contract is mandatory" in p for p in problems))
        # contract-ful extern WITH trust passes the full transpile
        clean = W1_EXTERNS + "\n" + FIB.replace("fn fib", "fn pay")
        self.assertEqual(self.type_check_source(clean), [])
        self.assertIn("datatype", self.elab.transpile(clean))

    def test_t3_trust_now_enforced_by_extern_pass(self):
        # week 3: the extern pass enforces the trust half of T3 — a contract-ful
        # extern WITHOUT a trust annotation fails transpile, not just type_check
        src = (
            "extern fn db_get(key: int) -> Result<int, str>\n"
            "  requires key > 0\n"
            "ensures is_ok(result) == (key < 1000);\n\n"
            + FIB.replace("fn fib", "fn pay")
        )
        # structural type_check defers trust
        self.assertEqual(self.type_check_source(src), [])
        # but the full pipeline enforces it
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.transpile(src)
        self.assertIn("trust annotation is mandatory", str(ctx.exception))

    def test_reserved_identifier_rejected(self):
        src = FIB.replace("fn fib", "fn len")
        with self.assertRaises(ElaborationError):
            self.elab.transpile(src)


class TestEmissionParity(unittest.TestCase):
    def assert_parity(self, src: str, enforce: bool):
        elab = Elaborator(enforce_boundary=enforce)
        tp = Ward0Transpiler(enforce_boundary=enforce)
        self.assertEqual(elab.transpile(src), tp.transpile(src))

    def test_fib_plain(self):
        self.assert_parity(FIB, False)

    def test_fib_enforce(self):
        self.assert_parity(FIB, True)

    def test_w1_plain_and_enforce(self):
        src = w1_source()
        self.assert_parity(src, False)
        self.assert_parity(src, True)

    def test_hoist_indent_in_nested_block(self):
        # regression: hoisted `var wN := call;` must use the block's indent
        src = (
            "extern fn db_get(key: int) -> Result<int, str>\n"
            "  requires key > 0\n"
            "ensures is_ok(result) == (key < 1000);\n"
            'trust: "oracle reference stub"\n\n'
            "fn crud(op: int, key: int) -> Result<int, str>\n"
            "  requires key > 0\n"
            "  ensures is_ok(result) == (op >= 1 and op <= 2 and key < 1000)\n"
            "{\n"
            "    if op == 1 {\n"
            "        var v: Result<int, str> = db_get(key);\n"
            "        return v;\n"
            "    }\n"
            "    return Err(\"bad\");\n"
            "}\n"
        )
        emitted = Elaborator().transpile(src)
        self.assertIn("    var w0 := db_get(key);", emitted)  # 4-space indent

    def test_call_stmt_not_hoisted(self):
        src = (
            "extern fn poke(x: int) -> Result<Unit, str>\n"
            "  requires x > 0\n"
            "ensures is_ok(result) == (x < 1000);\n"
            'trust: "oracle reference stub"\n\n'
            "fn f(x: int) -> Result<Unit, str>\n"
            "  requires x > 0\n"
            "  ensures is_ok(result)\n"
            "{\n"
            "    poke(x);\n"
            "    return Ok(());\n"
            "}\n"
        )
        emitted = Elaborator().transpile(src)
        self.assertIn("var discard := poke(x);", emitted)


if __name__ == "__main__":
    unittest.main()
