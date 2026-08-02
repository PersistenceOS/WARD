"""Unit tests for the Python emitter (E11 — first multi-target backend slice).

No Dafny, no z3, no harness: these tests elaborate tiny ward0 modules and check
the emitted Python both structurally (shape rules) and behaviorally (exec the
emitted code and run a call — including the runtime `_checked`-style contract
wrapper on externs and the enforce on/off delta).

Run:  python -m unittest wardcore.test_py_backend -v
"""

from __future__ import annotations

import unittest

from wardcore.elaborator import Elaborator
from wardcore.py_backend import PyEmitter

# NOTE: the extern's ensures must be `is_ok(result) == (...)`-shaped — an
# unconditional `unwrap_err(result)` in an extern contract is evaluated by the
# runtime wrapper on EVERY call (mapping to `r.error`, None on Ok) and would
# spuriously flag a violation; the caller exercises unwrap_err in its Err
# branch instead (the real w1-w8 corpus contracts are is_ok-shaped).
EXTERN_PAY = (
    "extern fn charge(amount: int, token: str) -> Result<Unit, str>\n"
    "  requires amount > 0\n"
    "  ensures is_ok(result) == (amount <= 100)\n"
    ";\n"
    "trust: \"oracle reference stub\"\n"
    "\n"
)

CALLER_PAY = (
    "fn pay(amount: int, token: str) -> Result<Unit, str>\n"
    "  requires amount > 0\n"
    "  ensures is_ok(result) == (amount <= 100)\n"
    "{\n"
    "    var r: Result<Unit, str> = charge(amount, token);\n"
    "    if is_err(r) {\n"
    "        return Err(unwrap_err(r));\n"
    "    }\n"
    "    return Ok(());\n"
    "}\n"
)

# a stub that honors the contract (amount > 100 -> declined)
STUB_OK = (
    "def charge_stub(amount, token):\n"
    "    if amount > 100:\n"
    "        return (\"err\", \"declined\")\n"
    "    return (\"ok\", None)\n"
)

# a stub that VIOLATES the contract (accepts up to 120; ok in (100, 120])
STUB_VIOLATING = (
    "def charge_stub(amount, token):\n"
    "    if amount > 120:\n"
    "        return (\"err\", \"declined\")\n"
    "    return (\"ok\", None)\n"
)


def elaborate(src: str) -> Elaborator:
    elab = Elaborator(enforce_boundary=True)
    elab.transpile(src)
    return elab


def run_python(py_src: str, fn: str, *args):
    """exec the emitted Python and call fn(*args) in that namespace."""
    ns: dict = {}
    exec(py_src, ns)  # noqa: S102 — emitted code, test-only
    return ns[fn](*args)


class TestEmissionShape(unittest.TestCase):
    def test_result_preamble_present_when_result_used(self):
        elab = elaborate(EXTERN_PAY + CALLER_PAY)
        py = PyEmitter().emit(elab.module)
        self.assertIn("class Result:", py)
        self.assertIn("def Ok(value):", py)
        self.assertIn("def Err(error):", py)

    def test_no_result_preamble_for_plain_int_module(self):
        src = "fn double(x: int) -> int\n  ensures result == 2 * x\n{\n    return 2 * x;\n}\n"
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertNotIn("class Result:", py)
        self.assertIn("def double(x):", py)
        self.assertIn("return (2) * (x)", py)

    def test_int_division_is_floor(self):
        src = "fn half(x: int) -> int\n  ensures result == x / 2\n{\n    return x / 2;\n}\n"
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertIn("(x) // (2)", py)

    def test_quantifier_maps_to_all_any(self):
        src = (
            "fn ok(xs: List<int>) -> bool\n"
            "  ensures result == (forall i in range(0, len(xs)) :: xs[i] >= 0)\n"
            "{\n"
            "    return forall i in range(0, len(xs)) :: xs[i] >= 0;\n"
            "}\n"
        )
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertIn("all(((xs[i]) >= (0)) for i in range(0, len(xs)))", py)

    def test_loop_emits_python_for_with_invariant_comment(self):
        src = (
            "fn sum_up(n: int) -> int\n"
            "  requires n >= 0\n"
            "  ensures result >= 0\n"
            "{\n"
            "    var acc: int = 0;\n"
            "    for i in range(0, n)\n"
            "      invariant acc >= 0\n"
            "    {\n"
            "        acc += 1;\n"
            "    }\n"
            "    return acc;\n"
            "}\n"
        )
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertIn("for i in range(0, n):", py)
        self.assertIn("# invariant: (acc) >= (0)", py)

    def test_tier_tested_ships_unchecked(self):
        src = (
            "fn peek(x: int) -> int\n"
            "  tier: Tested\n"
            "  requires x > 0\n"
            "  ensures result > 0\n"
            "{\n"
            "    return x;\n"
            "}\n"
        )
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertNotIn("assert", py)

    def test_tier_proven_ships_input_checks(self):
        src = (
            "fn peek(x: int) -> int\n"
            "  requires x > 0\n"
            "  ensures result > 0\n"
            "{\n"
            "    return x;\n"
            "}\n"
        )
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertIn("assert (x) > (0)", py)

    def test_unit_and_result_constructors(self):
        elab = elaborate(EXTERN_PAY + CALLER_PAY)
        py = PyEmitter().emit(elab.module)
        # wrapper routes the stub's tuple to a Result and checks the contract
        self.assertIn("def charge_stub(amount, token):", py)
        self.assertIn("def charge(amount, token):", py)
        self.assertIn("r = Ok(out[1]) if out[0] == 'ok' else Err(out[1])", py)
        self.assertIn('return Err("contract violation")', py)
        # caller's Ok(()) -> Ok(None)
        self.assertIn("return Ok(None)", py)


class TestBehavioral(unittest.TestCase):
    def test_honest_stub_roundtrip(self):
        elab = elaborate(EXTERN_PAY + CALLER_PAY)
        py = PyEmitter().emit(elab.module, extern_impls={"charge": STUB_OK})
        ok_res = run_python(py, "pay", 50, "tok")
        decl = run_python(py, "pay", 150, "tok")
        self.assertTrue(ok_res.is_ok)
        self.assertEqual(ok_res.value, None)
        self.assertFalse(decl.is_ok)
        self.assertEqual(decl.error, "declined")

    def test_enforce_catches_violating_stub(self):
        """The stub accepts (100, 120] but the contract says <= 100 — the
        emitted wrapper (enforce on) must convert the over-grant to
        Err("contract violation")."""
        elab = elaborate(EXTERN_PAY + CALLER_PAY)
        py = PyEmitter().emit(elab.module, extern_impls={"charge": STUB_VIOLATING})
        leaked = run_python(py, "pay", 110, "tok")
        self.assertFalse(leaked.is_ok)
        self.assertEqual(leaked.error, "contract violation")

    def test_enforce_off_passes_through(self):
        """enforce off: the same violating stub leaks the over-grant (Ok) —
        this is the Dafny no-enforce-equivalent behavior the gate compares."""
        elab = elaborate(EXTERN_PAY + CALLER_PAY)
        py = PyEmitter(enforce_boundary=False).emit(
            elab.module, extern_impls={"charge": STUB_VIOLATING}
        )
        self.assertNotIn("contract violation", py)
        leaked = run_python(py, "pay", 110, "tok")
        self.assertTrue(leaked.is_ok)

    def test_plain_int_fn_exec(self):
        src = "fn double(x: int) -> int\n  ensures result == 2 * x\n{\n    return 2 * x;\n}\n"
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertEqual(run_python(py, "double", 21), 42)

    def test_loop_fn_exec(self):
        src = (
            "fn sum_up(n: int) -> int\n"
            "  requires n >= 0\n"
            "  ensures result >= 0\n"
            "{\n"
            "    var acc: int = 0;\n"
            "    for i in range(0, n)\n"
            "      invariant acc >= 0\n"
            "    {\n"
            "        acc += 1;\n"
            "    }\n"
            "    return acc;\n"
            "}\n"
        )
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertEqual(run_python(py, "sum_up", 5), 5)
        self.assertEqual(run_python(py, "sum_up", 0), 0)

    def test_list_indexing_and_len(self):
        src = (
            "fn sum(xs: List<int>) -> int\n"
            "  requires len(xs) >= 0\n"
            "  ensures result >= 0\n"
            "{\n"
            "    var acc: int = 0;\n"
            "    for i in range(0, len(xs))\n"
            "      invariant acc >= 0\n"
            "    {\n"
            "        acc += xs[i];\n"
            "    }\n"
            "    return acc;\n"
            "}\n"
        )
        elab = elaborate(src)
        py = PyEmitter().emit(elab.module)
        self.assertEqual(run_python(py, "sum", [1, 2, 3]), 6)
        self.assertEqual(run_python(py, "sum", []), 0)


if __name__ == "__main__":
    unittest.main()
