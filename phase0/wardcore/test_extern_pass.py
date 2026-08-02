"""Unit tests for the ward-core extern pass (Phase-2 week 3, R3/T3/T4).

Covers: T4 rewrite coverage (all call positions), T4 validation (no direct stub
call survives), T3-trust enforcement, enforce-off arm semantics, and pipeline
integration through Elaborator.transpile.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.extern_pass import ExternPass
from wardcore.ir import (
    Binary,
    Block,
    Call,
    Contract,
    ContractKind,
    ExternFn,
    Function,
    IntLit,
    Module,
    Param,
    Return,
    TInt,
    TResult,
    TStr,
    Var,
    validate_module,
)

PHASE0_DIR = Path(__file__).resolve().parent.parent
W_TASKS_DIR = PHASE0_DIR / "benchmarks" / "w_tasks"

SRC = """\
extern fn db_get(key: int) -> Result<int, str>
  requires key > 0
ensures is_ok(result) == (key < 1000);
trust: "oracle reference stub"

fn crud(op: int, key: int) -> Result<int, str>
  requires key > 0
  ensures is_ok(result) == (op >= 1 and op <= 2 and key < 1000)
{
    var v: Result<int, str> = db_get(key);
    if op == 1 {
        var u: Result<int, str> = db_get(key);
        return u;
    }
    db_get(key);
    return v;
}
"""


def trust_less(src: str) -> str:
    return src.replace('trust: "oracle reference stub"\n', "")


class TestRewrite(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()
        self.module = self.elab.desugar(SRC)
        self.pass_on = ExternPass(enforce=True)

    def _extern_calls(self, module: Module) -> list[Call]:
        calls: list[Call] = []

        def walk_expr(e) -> None:
            if isinstance(e, Call):
                calls.append(e)
                for a in e.args:
                    walk_expr(a)
            for attr in ("operand", "left", "right", "index", "inner", "lo", "hi", "body"):
                child = getattr(e, attr, None)
                if child is not None:
                    walk_expr(child)

        def walk_stmt(s) -> None:
            if isinstance(s, Return) and s.value is not None:
                walk_expr(s.value)
            elif hasattr(s, "cond"):
                walk_expr(s.cond)
                walk_expr(getattr(s, "value", None) or getattr(s, "call", None))
            for attr in ("value", "call", "cond"):
                child = getattr(s, attr, None)
                if child is not None:
                    walk_expr(child)
            for attr in ("then_branch", "else_branch", "body"):
                blk = getattr(s, attr, None)
                if blk is not None:
                    for st in blk.stmts:
                        walk_stmt(st)

        for fn in module.fns:
            for st in fn.body.stmts:
                walk_stmt(st)
        return calls

    def test_all_extern_calls_rewritten(self):
        rewritten = self.pass_on.rewrite(self.module)
        calls = self._extern_calls(rewritten)
        self.assertTrue(calls, "expected extern calls to be found")
        for call in calls:
            self.assertEqual(call.callee, "db_get")
            self.assertTrue(call.checked, f"call {call} not routed through _checked")

    def test_rewrite_idempotent(self):
        once = self.pass_on.rewrite(self.module)
        twice = self.pass_on.rewrite(once)
        self.assertEqual(once, twice)

    def test_enforce_off_keeps_direct_calls(self):
        rewritten = ExternPass(enforce=False).rewrite(self.module)
        self.assertEqual(rewritten, self.module)  # unchanged module
        calls = self._extern_calls(rewritten)
        self.assertTrue(all(not c.checked for c in calls))

    def test_validate_t4_flags_unchecked_stub_call(self):
        # a hand-built module with a DIRECT stub call must fail T4
        # pure contract term (Binary, not a Call — T1 forbids method calls in
        # contracts; `>` as a Call callee would trip the purity check)
        gt0 = Binary(">", Var("key"), IntLit(0))
        ext = ExternFn(
            name="db_get",
            params=(Param("key", TInt()),),
            ret=TResult(TInt(), TStr()),
            requires=(Contract(ContractKind.REQUIRES, gt0),),
            ensures=(Contract(ContractKind.ENSURES, Var("result")),),
            trust="x",
        )
        fn = Function(
            name="f",
            params=(Param("key", TInt()),),
            ret=TResult(TInt(), TStr()),
            requires=(Contract(ContractKind.REQUIRES, gt0),),
            body=Block(stmts=(Return(Call("db_get", (Var("key"),))),)),
        )
        module = Module(name="m", externs=(ext,), fns=(fn,))
        problems = validate_module(module, check_t4=True, check_t3_trust=True)
        self.assertTrue(any("not routed through _checked wrapper (T4)" in p for p in problems))
        # the pass rewrites it away
        rewritten = ExternPass(enforce=True).run(module)
        self.assertTrue(rewritten.fns[0].body.stmts[0].value.checked)


class TestT3Trust(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()

    def test_trust_mandatory_through_pipeline(self):
        module = self.elab.desugar(trust_less(SRC))
        problems = ExternPass(enforce=True).validate(module)
        self.assertTrue(any("trust annotation is mandatory (T3)" in p for p in problems))

    def test_trust_present_passes(self):
        # T3-trust: extern WITH trust is clean — but T4 flags the direct stub
        # calls in the UNREWRITTEN module, so validate the rewritten one (the
        # pass's own output) where no direct stub call survives.
        module = self.elab.desugar(SRC)
        rewritten = ExternPass(enforce=True).rewrite(module)
        problems = ExternPass(enforce=True).validate(rewritten)
        self.assertEqual(problems, [])

    def test_contract_less_extern_hard_error(self):
        # E3 probe A: contract-less extern = hard elaboration error
        bad = (
            "extern fn db_get(key: int) -> Result<int, str>;\n"
            'trust: "oracle reference stub"\n\n'
            "fn crud(key: int) -> Result<int, str>\n"
            "  requires key > 0\n"
            "  ensures is_ok(result)\n"
            "{\n"
            "    var v: Result<int, str> = db_get(key);\n"
            "    return v;\n"
            "}\n"
        )
        with self.assertRaises(ElaborationError) as ctx:
            self.elab.transpile(bad)
        self.assertIn("contract is mandatory", str(ctx.exception))


class TestPipelineIntegration(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()

    def test_enforce_on_routes_to_checked(self):
        emitted = Elaborator(enforce_boundary=True).transpile(SRC)
        self.assertIn("db_get_checked", emitted)
        # the caller must never call the stub directly
        caller = emitted.split("method crud(")[1]
        direct = [l for l in caller.splitlines() if "db_get(" in l and "db_get_checked" not in l]
        self.assertEqual(direct, [])

    def test_enforce_off_keeps_direct_stub_calls(self):
        emitted = Elaborator(enforce_boundary=False).transpile(SRC)
        self.assertNotIn("db_get_checked", emitted)
        self.assertIn("db_get(key)", emitted)

    def test_trust_report_survives(self):
        elab = Elaborator(enforce_boundary=True)
        elab.transpile(SRC)
        self.assertEqual(
            elab.trust_report,
            [{"stub": "db_get", "trust": "oracle reference stub"}],
        )


if __name__ == "__main__":
    unittest.main()
