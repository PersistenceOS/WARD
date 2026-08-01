import glob
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from transpiler.transpiler import TranspileError, Ward0Transpiler

TASKS = Path(__file__).resolve().parent.parent / "benchmarks" / "tasks"

DAFNY = shutil.which("dafny")


def find_z3() -> str | None:
    z3 = shutil.which("z3")
    if z3:
        return z3
    z3_dir = Path.home() / ".z3"
    if z3_dir.is_dir():
        for exe in z3_dir.glob("*/bin/z3.exe"):
            return str(exe)
    return None


Z3 = find_z3()


def solver_args() -> list[str]:
    return [DAFNY, "verify", "--verification-time-limit:30"] + (
        ["--solver-path", Z3] if Z3 else []
    )


class TestTranspilerUnit(unittest.TestCase):
    def setUp(self):
        self.t = Ward0Transpiler()

    def test_chained_comparison(self):
        out = self.t.transpile(
            """
fn sum_range(lo: int, hi: int) -> int
  requires 0 <= lo <= hi
  ensures result == (lo + hi) * (hi - lo + 1) / 2
{
    return (lo + hi) * (hi - lo + 1) / 2; }
"""
        )
        self.assertIn("requires 0 <= lo <= hi", out)

    def test_apply_discount(self):
        out = self.t.transpile(
            """
fn apply_discount(price: int, discount_pct: int) -> int
  requires price >= 0
  requires discount_pct >= 0 and discount_pct <= 100
  ensures result <= price
{
    return price - (price * discount_pct) / 100;
}
"""
        )
        self.assertIn("method apply_discount(price: int, discount_pct: int) returns (result: int)", out)
        self.assertIn("requires price >= 0", out)
        self.assertIn("discount_pct >= 0 && discount_pct <= 100", out)
        self.assertIn("return price - (price * discount_pct) / 100;", out)

    def test_list_mapping(self):
        out = self.t.transpile(
            """
fn max_of_list(xs: List<int>) -> int
  requires len(xs) > 0
  ensures result >= xs[0]
{
    var m: int = xs[0];
    for i in range(1, len(xs))
      invariant m >= xs[0]
    {
        if xs[i] > m {
            m = xs[i];
        }
    }
    return m;
}
"""
        )
        self.assertIn("xs: seq<int>", out)
        self.assertIn("requires |xs| > 0", out)
        self.assertIn("var m := xs[0];", out)
        self.assertIn("for i := 1 to |xs|", out)
        self.assertIn("  invariant m >= xs[0]", out)
        self.assertIn("m := xs[i];", out)

    def test_compound_assign(self):
        out = self.t.transpile("fn inc(n: int) -> int { n += 1; return n; }")
        self.assertIn("n := n + 1;", out)

    def test_param_shadowing(self):
        out = self.t.transpile("fn inc(n: int) -> int { n += 1; return n; }")
        self.assertIn("var n := n;", out)

    def test_result_datatype(self):
        out = self.t.transpile(
            'fn f(x: int) -> Result<Unit, str> { if x > 0 { return Ok(()); } return Err("bad"); }'
        )
        self.assertIn("datatype Result<T, E> = Ok(value: T) | Err(error: E)", out)
        self.assertIn("returns (result: Result<(), string>)", out)

    def test_no_datatype_when_unused(self):
        out = self.t.transpile("fn f(x: int) -> int { return x; }")
        self.assertNotIn("datatype Result", out)

    def test_unit_return(self):
        out = self.t.transpile("fn f() -> Unit { return; }")
        self.assertIn("returns (result: ())", out)
        self.assertIn("return;", out)

    def test_boolean_ops(self):
        out = self.t.transpile("fn f(a: bool, b: bool) -> bool { return a and not b or false; }")
        self.assertIn("return a && !(b) || false;", out)

    def test_negative_literals(self):
        out = self.t.transpile("fn f(x: int) -> int { return -x + -5; }")
        self.assertIn("return -x + -5;", out)

    def test_empty_else_chain(self):
        out = self.t.transpile("fn f(x: int) -> int { if x > 0 { return 1; } else { return 0; } }")
        self.assertIn("if x > 0 {", out)
        self.assertIn("} else {", out)

    def test_call_statement(self):
        out = self.t.transpile("fn f(x: int) -> int { g(x); return x; }")
        self.assertIn("var discard := g(x);", out)

    def test_loop_invariant(self):
        out = self.t.transpile(
            "fn f(xs: List<int>) -> int { var n: int = 0; for i in range(0, len(xs)) invariant n >= 0 { n += 1; } return n; }"
        )
        self.assertIn("for i := 0 to |xs|", out)
        self.assertIn("  invariant n >= 0", out)

    def test_forall_quantifier(self):
        out = self.t.transpile(
            "fn f(xs: List<int>, v: int) -> bool requires len(xs) > 0 "
            "ensures result == (forall i in range(0, len(xs)) :: xs[i] > v) { var b: bool = true; return b; }"
        )
        self.assertIn("ensures result == (forall i :: 0 <= i < |xs| ==> (xs[i] > v))", out)

    def test_exists_quantifier(self):
        out = self.t.transpile(
            "fn f(xs: List<int>) -> bool "
            "ensures result == (exists i in range(0, len(xs)) :: xs[i] == 0) { var b: bool = true; return b; }"
        )
        self.assertIn("ensures result == (exists i :: 0 <= i < |xs| && (xs[i] == 0))", out)

    def test_result_builtins(self):
        out = self.t.transpile(
            "fn g(balance: int, amount: int) -> Result<int, str> requires amount > 0 "
            "ensures is_err(result) == (balance < amount) "
            "ensures not is_ok(result) or unwrap_ok(result) == balance - amount "
            '{ if balance < amount { return Err("insufficient"); } return Ok(balance - amount); }'
        )
        self.assertIn("ensures result.Err? == (balance < amount)", out)
        self.assertIn("ensures !(result.Ok?) || result.value == balance - amount", out)
        self.assertIn('return Err("insufficient");', out)
        self.assertIn("return Ok(balance - amount);", out)

    def test_builtin_wrong_arity(self):
        with self.assertRaises(TranspileError):
            self.t.transpile("fn f(xs: List<int>) -> int { return len(xs, 1); }")

    def test_enforce_wrapper_generated(self):
        t = Ward0Transpiler(enforce_boundary=True)
        out = t.transpile(
            """extern fn stripe_charge(amount: int, token: str) -> Result<Unit, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 100)
trust: "is_ok(result) == (amount <= 100)"
;
fn pay(amount: int, token: str) -> Result<Unit, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 100)
{
    return stripe_charge(amount, token);
}
"""
        )
        self.assertIn("method stripe_charge_checked(amount: int, token: string) returns (result: Result<(), string>)", out)
        self.assertIn("  requires amount > 0", out)
        self.assertIn("  ensures result.Ok? == (amount <= 100)", out)
        self.assertIn("  var r := stripe_charge(amount, token);", out)
        self.assertIn("if !((r.Ok? == (amount <= 100)))", out)
        self.assertIn('return Err("contract violation");', out)
        self.assertIn("var w0 := stripe_charge_checked(amount, token);", out)
        pay_body = out.split("method pay")[1]
        self.assertIn("stripe_charge_checked(amount, token)", pay_body)
        self.assertNotIn("stripe_charge(amount, token);", pay_body)

    def test_enforce_off_no_wrapper(self):
        out = self.t.transpile(
            """extern fn stripe_charge(amount: int, token: str) -> Result<Unit, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 100)
trust: "is_ok(result) == (amount <= 100)"
;
fn pay(amount: int, token: str) -> Result<Unit, str>
  requires amount > 0
  ensures is_ok(result) == (amount <= 100)
{
    return stripe_charge(amount, token);
}
"""
        )
        self.assertNotIn("stripe_charge_checked", out)
        self.assertNotIn("contract violation", out)

    def test_enforce_wrapper_vacuous_without_contract(self):
        t = Ward0Transpiler(enforce_boundary=True)
        out = t.transpile(
            """extern fn stripe_charge(amount: int, token: str) -> Result<Unit, str>
;
fn pay(amount: int, token: str) -> Result<Unit, str>
  requires amount > 0
{
    return stripe_charge(amount, token);
}
"""
        )
        self.assertIn("method stripe_charge_checked(amount: int, token: string)", out)
        self.assertNotIn("if !(", out)
        self.assertIn("return r;", out)
        self.assertIn("var w0 := stripe_charge_checked(amount, token);", out)

    def test_enforce_trust_report(self):
        t = Ward0Transpiler(enforce_boundary=True)
        t.transpile(
            """extern fn db_get_balance(user_id: int) -> Result<int, str>
  requires user_id > 0
  ensures is_ok(result) == (user_id < 100)
trust: "is_ok(result) == (user_id < 100)"
;
fn get_balance(user_id: int) -> Result<int, str>
  requires user_id > 0
{
    return db_get_balance(user_id);
}
"""
        )
        self.assertEqual(
            t.trust_report,
            [{"stub": "db_get_balance", "trust": "is_ok(result) == (user_id < 100)"}],
        )


class TestTranspileErrors(unittest.TestCase):
    def setUp(self):
        self.t = Ward0Transpiler()

    def test_record_field_assignment_rejected(self):
        with self.assertRaises(TranspileError):
            self.t.transpile("fn f(p: int) -> int { p.x = 1; return p; }")

    def test_reserved_name_result_rejected(self):
        with self.assertRaises(TranspileError):
            self.t.transpile("fn result() -> int { return 0; }")


class TestDafnyIntegration(unittest.TestCase):
    @unittest.skipIf(DAFNY is None or Z3 is None, "dafny or z3 not found")
    def test_sample_tasks_verify(self):
        for path in sorted(glob.glob(str(TASKS / "*.ward0"))):
            name = Path(path).name
            with self.subTest(task=name):
                source = Path(path).read_text(encoding="utf-8")
                dafny_src = Ward0Transpiler().transpile(source)
                with tempfile.TemporaryDirectory() as td:
                    dfy = Path(td) / "task.dfy"
                    dfy.write_text(dafny_src, encoding="utf-8")
                    proc = subprocess.run(
                        solver_args() + [str(dfy)],
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        proc.returncode, 0, f"dafny verify failed for {name}:\n{proc.stdout}{proc.stderr}"
                    )


if __name__ == "__main__":
    unittest.main()
