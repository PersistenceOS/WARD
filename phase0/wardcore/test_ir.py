"""Unit tests for ward-core IR v0.1 (Phase-2 week 1).

Grounding: the real w1_payment_chain shape (benchmarks/w_tasks/w1_payment_chain
.ward0 + .json) — 3 externs (auth_check, rate_limit, stripe_charge), a Proven
single entry fn `pay`, cross-function contracts. Each test encodes one of the
T1–T8 structural obligations from files/ward-phase2-scoping.md §4.
"""

import unittest

from wardcore.ir import (
    Assign,
    Binary,
    Block,
    BoolLit,
    Call,
    CallStmt,
    Contract,
    ContractKind,
    EffectKind,
    ExternFn,
    Function,
    If,
    IntLit,
    Loop,
    Module,
    Param,
    Return,
    StrLit,
    Tier,
    TInt,
    TList,
    TResult,
    TStr,
    TUnit,
    Var,
    VarDecl,
    validate_module,
)

U = TUnit()
R = TResult(U, TStr())


def _w1_module(trust: bool = True) -> Module:
    """The w1_payment_chain shape as ward-core IR (reference caller logic)."""
    auth = ExternFn(
        name="auth_check",
        params=(Param("user_id", TInt(), linear=False),),
        ret=R,
        requires=(Contract(ContractKind.REQUIRES, Binary(">", Var("user_id"), IntLit(0))),),
        ensures=(Contract(ContractKind.ENSURES, Binary("==", Call("is_ok", (Var("result"),)), Binary("and", Binary("<", Var("user_id"), IntLit(1000)), BoolLit(True)))),),
        trust="stub — auth SDK" if trust else "",
        effect=EffectKind.NET,
    )
    rate = ExternFn(
        name="rate_limit",
        params=(Param("amount", TInt()),),
        ret=R,
        ensures=(Contract(ContractKind.ENSURES, Binary("==", Call("is_ok", (Var("result"),)), Binary("<=", Var("amount"), IntLit(5000)))),),
        trust="stub — rate limiter" if trust else "",
        effect=EffectKind.NET,
    )
    charge = ExternFn(
        name="stripe_charge",
        params=(Param("amount", TInt()), Param("token", TStr())),
        ret=R,
        ensures=(Contract(ContractKind.ENSURES, Binary("==", Call("is_ok", (Var("result"),)), Binary("<=", Var("amount"), IntLit(100)))),),
        trust="stub — Stripe SDK" if trust else "",
        effect=EffectKind.NET,
    )
    pay = Function(
        name="pay",
        params=(Param("user_id", TInt()), Param("amount", TInt()), Param("token", TStr())),
        ret=R,
        requires=(Contract(ContractKind.REQUIRES, Binary(">", Var("user_id"), IntLit(0))),),
        ensures=(Contract(ContractKind.ENSURES, Binary("==", Call("is_ok", (Var("result"),)), Binary("and", Binary("<", Var("user_id"), IntLit(1000)), Binary("<=", Var("amount"), IntLit(100))))),),
        effects=frozenset({EffectKind.NET}),
        tier=Tier.PROVEN,
        body=Block(
            stmts=(
                VarDecl("a", R, Call("auth_check", (Var("user_id"),), checked=True)),
                If(
                    Binary("!=", Call("is_ok", (Var("a"),)), Binary("<", Var("user_id"), IntLit(1000))),
                    Block(stmts=(Return(Call("Err", (StrLit("contract violation"),))),)),
                ),
                VarDecl("c", R, Call("stripe_charge", (Var("amount"), Var("token")), checked=True)),
                Return(Call("Ok", (Call("unwrap_ok", (Var("c"),)),))),
            )
        ),
    )
    return Module(name="w1_payment_chain", externs=(auth, rate, charge), fns=(pay,))


def _transfer_linear_module() -> Module:
    """A linear-money transfer: `amount` is linear and must be consumed."""
    ledger = ExternFn(
        name="ledger_debit",
        params=(Param("amount", TInt()),),  # extern params: linearity not enforced (unverified boundary)
        ret=R,
        requires=(Contract(ContractKind.REQUIRES, Binary(">", Var("amount"), IntLit(0))),),
        ensures=(Contract(ContractKind.ENSURES, Binary("==", Call("is_ok", (Var("result"),)), BoolLit(True))),),
        trust="stub — ledger",
        effect=EffectKind.DB,
    )
    transfer = Function(
        name="transfer",
        params=(Param("amount", TInt(), linear=True),),
        ret=R,
        effects=frozenset({EffectKind.DB}),
        tier=Tier.PROVEN,
        body=Block(stmts=(CallStmt(Call("ledger_debit", (Var("amount"),), checked=True)), Return(Call("Ok", (Var("amount"),))))),
    )
    return Module(name="transfer", externs=(ledger,), fns=(transfer,))


class TestTypes(unittest.TestCase):
    def test_result_and_list_types(self):
        t = TResult(TStr(), TInt())
        self.assertEqual(t.name, "Result")
        self.assertEqual(t.args, (TStr(), TInt()))
        self.assertEqual(TList(TInt()).name, "List")


class TestWellFormedModule(unittest.TestCase):
    def test_w1_module_is_clean(self):
        self.assertEqual(validate_module(_w1_module()), [])

    def test_transfer_linear_module_is_clean(self):
        self.assertEqual(validate_module(_transfer_linear_module()), [])


class TestT3ExternContractAndTrust(unittest.TestCase):
    def test_missing_trust_is_error(self):
        problems = validate_module(_w1_module(trust=False))
        self.assertTrue(any("trust annotation is mandatory" in p for p in problems))

    def test_missing_contract_is_error(self):
        m = _w1_module()
        bare = ExternFn(name="bare_stub", params=(), ret=U)
        m = Module(name=m.name, externs=m.externs + (bare,), fns=m.fns)
        problems = validate_module(m)
        self.assertTrue(any("contract is mandatory" in p for p in problems))


class TestT1T2ContractIsPureAnnotation(unittest.TestCase):
    def test_method_call_in_contract_is_error(self):
        m = _w1_module()
        bad_fn = Function(
            name="bad",
            params=(Param("x", TInt()),),
            ret=U,
            ensures=(Contract(ContractKind.ENSURES, Call("auth_check", (Var("x"),))),),
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (bad_fn,))
        problems = validate_module(m)
        self.assertTrue(any("no method calls in contracts" in p for p in problems))


class TestT4CheckedRouting(unittest.TestCase):
    def test_unchecked_extern_call_is_error(self):
        m = _w1_module()
        fn = Function(
            name="sneaky",
            params=(Param("x", TInt()),),
            ret=R,
            body=Block(stmts=(Return(Call("auth_check", (Var("x"),), checked=False)),)),
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (fn,))
        problems = validate_module(m)
        self.assertTrue(any("not routed through _checked wrapper" in p for p in problems))

    def test_checked_extern_call_is_fine(self):
        m = _w1_module()
        fn = Function(
            name="fine",
            params=(Param("x", TInt()),),
            ret=R,
            body=Block(stmts=(Return(Call("auth_check", (Var("x"),), checked=True)),)),
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (fn,))
        self.assertEqual(validate_module(m), [])


class TestT5Effects(unittest.TestCase):
    def test_declared_but_unused_effect_is_error(self):
        m = _transfer_linear_module()
        fn = Function(
            name="ghost",
            params=(Param("x", TInt()),),
            ret=U,
            effects=frozenset({EffectKind.FS}),
            body=Block(stmts=(Return(),)),
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (fn,))
        problems = validate_module(m)
        self.assertTrue(any("declared effect fs is unused" in p for p in problems))


class TestT6TierRouting(unittest.TestCase):
    def test_tiers_are_explicit_per_function(self):
        m = _w1_module()
        self.assertEqual(m.fns[0].tier, Tier.PROVEN)
        # Contracted and Tested are the other legal tiers (enum members).
        self.assertIn(Tier.CONTRACTED, Tier)
        self.assertIn(Tier.TESTED, Tier)


class TestT7LinearityStructural(unittest.TestCase):
    def test_linear_param_dropped_is_error(self):
        m = _transfer_linear_module()
        fn = Function(
            name="drop_money",
            params=(Param("amount", TInt(), linear=True),),
            ret=U,
            body=Block(stmts=(Return(),)),  # never uses amount
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (fn,))
        problems = validate_module(m)
        self.assertTrue(any("linear param amount is never used" in p for p in problems))

    def test_linear_param_used_is_fine(self):
        m = _transfer_linear_module()
        fn = Function(
            name="consume",
            params=(Param("amount", TInt(), linear=True),),
            ret=R,
            effects=frozenset({EffectKind.DB}),
            body=Block(
                stmts=(
                    CallStmt(Call("ledger_debit", (Var("amount"),), checked=True)),
                    Return(Call("Ok", (Var("amount"),))),
                )
            ),
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (fn,))
        self.assertEqual(validate_module(m), [])


class TestT8Totality(unittest.TestCase):
    def test_recursion_is_error(self):
        m = _w1_module()
        fn = Function(
            name="loop_forever",
            params=(Param("x", TInt()),),
            ret=TInt(),
            body=Block(stmts=(Return(Call("loop_forever", (Var("x"),))),)),
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (fn,))
        problems = validate_module(m)
        self.assertTrue(any("recursion is forbidden" in p for p in problems))

    def test_bounded_loop_is_the_only_iteration(self):
        # T8: the IR's only loop node is the bounded `Loop` (for over range);
        # there is no while node. A Loop with a bound and invariant validates.
        m = _w1_module()
        fn = Function(
            name="count",
            params=(Param("xs", TList(TInt())),),
            ret=TInt(),
            requires=(Contract(ContractKind.REQUIRES, Binary(">", Call("len", (Var("xs"),)), IntLit(0))),),
            body=Block(
                stmts=(
                    VarDecl("total", TInt(), IntLit(0)),
                    Loop(
                        var="i",
                        lo=IntLit(0),
                        hi=Call("len", (Var("xs"),)),
                        invariants=(Binary("<=", IntLit(0), Var("total")),),
                        body=Block(stmts=(Assign("total", "+=", Binary("+", Var("total"), IntLit(1))),)),
                    ),
                    Return(Var("total")),
                )
            ),
        )
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (fn,))
        self.assertEqual(validate_module(m), [])


class TestDuplicateNames(unittest.TestCase):
    def test_duplicate_function_name_is_error(self):
        m = _w1_module()
        dup = Function(name="pay", params=(), ret=U)
        m = Module(name=m.name, externs=m.externs, fns=m.fns + (dup,))
        problems = validate_module(m)
        self.assertTrue(any("duplicate function name: pay" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
