import glob
import unittest

from lark import Lark, ParseError, UnexpectedInput

GRAMMAR_PATH = "grammar/ward0.lark"


def parse(text: str):
    parser = Lark.open(GRAMMAR_PATH, parser="lalr", start="start")
    return parser.parse(text)


class TestGrammarAccepts(unittest.TestCase):
    def test_max_of_list(self):
        parse("""
fn max_of_list(xs: List<int>) -> int
  requires len(xs) > 0
  ensures result >= xs[0]
{
    var m: int = xs[0];
    for i in range(1, len(xs)) {
        if xs[i] > m {
            m = xs[i]; }
    }
    return m; }
""")

    def test_is_sorted(self):
        parse("""
fn is_sorted(xs: List<int>) -> bool
{
    for i in range(0, len(xs) - 1) {
        if xs[i] > xs[i + 1] {
            return false; }
    }
    return true; }
""")

    def test_apply_discount(self):
        parse("""
fn apply_discount(price: int, discount_pct: int) -> int
  requires price >= 0
  requires discount_pct >= 0 and discount_pct <= 100
  ensures result <= price
{
    return price - (price * discount_pct) / 100; }
""")

    def test_chained_comparison(self):
        parse("""
fn sum_range(lo: int, hi: int) -> int
  requires 0 <= lo <= hi
  ensures result == (lo + hi) * (hi - lo + 1) / 2
{
    return (lo + hi) * (hi - lo + 1) / 2; }
""")

    def test_count_positive(self):
        parse("""
fn count_positive(xs: List<int>) -> int
  ensures result >= 0
  ensures result <= len(xs)
{
    var n: int = 0;
    for i in range(0, len(xs)) {
        if xs[i] > 0 {
            n += 1; }
    }
    return n; }
""")

    def test_empty_body_and_void_return(self):
        parse("fn f() -> Unit { return; }")

    def test_result_with_unit_literal(self):
        parse("fn f(x: int) -> Result<Unit, str> { if x > 0 { return Ok(()); } return Err(\"bad\"); }")

    def test_boolean_logic(self):
        parse("fn f(a: bool, b: bool) -> bool { return a and not b or false; }")

    def test_comments_ignored(self):
        parse("// leading comment\nfn f() -> int { return 1; } // trailing")

    def test_negative_literals(self):
        parse("fn f(x: int) -> int { return -x + -5; }")

    def test_nested_expressions(self):
        parse("fn f(a: int, b: int) -> int { var x: int = (a + b) * (a - b) / 2; return x; }")


class TestGrammarRejects(unittest.TestCase):
    def setUp(self):
        self.parser = Lark.open(GRAMMAR_PATH, parser="lalr", start="start")

    def assert_rejects(self, text):
        with self.assertRaises((ParseError, UnexpectedInput)):
            self.parser.parse(text)

    def test_classes_forbidden(self):
        self.assert_rejects("class Foo { }")

    def test_missing_semicolon(self):
        self.assert_rejects("fn f(a: int) -> int { return a }")

    def test_while_forbidden(self):
        self.assert_rejects("fn f() -> int { while true { } return 0 }")

    def test_unbounded_for_forbidden(self):
        self.assert_rejects("fn f(xs: List<int>) -> int { for i in xs { } return 0 }")

    def test_closures_forbidden(self):
        self.assert_rejects("fn f() -> int { var g: int = (x) => x; return 0 }")

    def test_unknown_type_forbidden(self):
        self.assert_rejects("fn f(x: float) -> int { return 0 }")

    def test_bare_expression_statement_forbidden(self):
        self.assert_rejects("fn f() -> int { 1 + 2; return 0 }")

    def test_lambda_arrow_forbidden(self):
        self.assert_rejects("fn f() -> int { var g: int = x => x; return 0 }")


class TestSampleTasks(unittest.TestCase):
    def test_all_benchmark_tasks_parse(self):
        parser = Lark.open(GRAMMAR_PATH, parser="lalr", start="start")
        files = glob.glob("benchmarks/tasks/*.ward0")
        self.assertTrue(len(files) >= 4, f"expected sample tasks, found {len(files)}")
        for path in files:
            with open(path, encoding="utf-8") as fh:
                parser.parse(fh.read())


if __name__ == "__main__":
    unittest.main()



