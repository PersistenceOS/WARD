"""Unit tests for the ward-core effects pass (Phase-2 week 4, T5/E4).

Covers: extern-driven inference (direct + transitive through fn calls),
escape hard error (calling an undeclared effect), declared-but-unused hard
error, all five kinds, the week-4 boundary (undeclared fns unconstrained —
E1 parity), and the `effect:`/`effects:` surface annotations.
"""

from __future__ import annotations

import unittest

from wardcore.effects_pass import EffectsPass, parse_effect_set
from wardcore.elaborator import Elaborator, ElaborationError
from wardcore.ir import EffectKind

# A module with three externs (net/db/fs) and fns that declare subsets.
SRC = """\
extern fn net_call(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: net
trust: "oracle reference stub"

extern fn db_get(key: int) -> Result<int, str>
  requires key > 0
ensures is_ok(result) == (key < 1000);
effect: db
trust: "oracle reference stub"

extern fn fs_read(path: str) -> Result<Unit, str>
  ensures is_ok(result);
effect: fs
trust: "oracle reference stub"

effects: net, db
fn handle(user_id: int, key: int) -> Result<int, str>
  requires user_id > 0
  requires key > 0
  ensures is_ok(result) == (user_id < 1000 and key < 1000)
{
    var a: Result<int, str> = net_call(user_id);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    var b: Result<int, str> = db_get(key);
    if is_err(b) {
        return Err(unwrap_err(b));
    }
    return Ok(unwrap_ok(b));
}

effects: fs
fn read(path: str) -> Result<Unit, str>
  ensures is_ok(result)
{
    var r: Result<Unit, str> = fs_read(path);
    return r;
}
"""


class TestInference(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()
        self.module = self.elab.desugar(SRC)

    def test_direct_extern_inference(self):
        inferred = EffectsPass().infer(self.module)
        self.assertEqual(inferred["handle"], frozenset({EffectKind.NET, EffectKind.DB}))
        self.assertEqual(inferred["read"], frozenset({EffectKind.FS}))

    def test_transitive_through_fn_calls(self):
        src = SRC + """\

effects: db
fn wrapper(key: int) -> Result<int, str>
  requires key > 0
  ensures is_ok(result) == (key < 1000)
{
    var b: Result<int, str> = db_get(key);
    if is_err(b) {
        return Err(unwrap_err(b));
    }
    return Ok(unwrap_ok(b));
}

effects: db
fn caller(key: int) -> Result<int, str>
  requires key > 0
  ensures is_ok(result) == (key < 1000)
{
    return wrapper(key);
}
"""
        module = self.elab.desugar(src)
        inferred = EffectsPass().infer(module)
        self.assertEqual(inferred["caller"], frozenset({EffectKind.DB}))

    def test_cycle_guard_terminates(self):
        # T8 forbids recursion, but the inference guard must not hang either
        src = """\
effects: net
fn a(x: int) -> int
  requires x > 0
  ensures result > 0
{
    return b(x);
}

effects: net
fn b(x: int) -> int
  requires x > 0
  ensures result > 0
{
    return a(x);
}
"""
        module = self.elab.desugar(src)
        inferred = EffectsPass().infer(module)
        self.assertIn("a", inferred)  # terminated, no exception


class TestT5Checks(unittest.TestCase):
    def setUp(self):
        self.elab = Elaborator()

    def test_clean_declared_matches_inferred(self):
        problems = EffectsPass().validate(self.elab.desugar(SRC))
        self.assertEqual(problems, [])

    def test_escape_undeclared_effect_is_error(self):
        # E4 pre-registered probe: calling a net extern without declaring net
        bad = """\
extern fn net_call(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: net
trust: "oracle reference stub"

effects: fs
fn leak(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    var a: Result<int, str> = net_call(x);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    return Ok(unwrap_ok(a));
}
"""
        problems = EffectsPass().validate(self.elab.desugar(bad))
        self.assertTrue(any("calls undeclared effect net" in p for p in problems))

    def test_escape_hard_error_through_pipeline(self):
        bad = """\
extern fn net_call(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: net
trust: "oracle reference stub"

effects: fs
fn leak(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    var a: Result<int, str> = net_call(x);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    return Ok(unwrap_ok(a));
}
"""
        with self.assertRaises(ElaborationError) as ctx:
            Elaborator().transpile(bad)
        self.assertIn("calls undeclared effect net (T5)", str(ctx.exception))

    def test_declared_but_unused_is_error(self):
        bad = """\
extern fn net_call(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: net
trust: "oracle reference stub"

effects: net, fs
fn ghost(x: int) -> int
  requires x > 0
  ensures result > 0
{
    return x;
}
"""
        problems = EffectsPass().validate(self.elab.desugar(bad))
        self.assertTrue(any("declared effect fs is unused" in p for p in problems))

    def test_undeclared_fn_unconstrained_week4(self):
        # week-4 boundary (scoping doc §7): no effects: annotation -> no check.
        # This is what keeps the E1 corpus (no declarations) passing.
        bare = """\
extern fn net_call(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: net
trust: "oracle reference stub"

fn no_decl(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{
    var a: Result<int, str> = net_call(x);
    if is_err(a) {
        return Err(unwrap_err(a));
    }
    return Ok(unwrap_ok(a));
}
"""
        problems = EffectsPass().validate(self.elab.desugar(bare))
        self.assertEqual(problems, [])

    def test_all_five_kinds_are_checked(self):
        kinds = {"net": EffectKind.NET, "db": EffectKind.DB, "fs": EffectKind.FS,
                 "mut": EffectKind.MUT, "partial": EffectKind.PARTIAL}
        for name, kind in kinds.items():
            src = f"""\
extern fn stub_{name}(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
effect: {name}
trust: "oracle reference stub"

effects: {name}
fn use_{name}(x: int) -> Result<int, str>
  requires x > 0
  ensures is_ok(result) == (x < 1000)
{{
    var a: Result<int, str> = stub_{name}(x);
    if is_err(a) {{
        return Err(unwrap_err(a));
    }}
    return Ok(unwrap_ok(a));
}}
"""
            module = self.elab.desugar(src)
            problems = EffectsPass().validate(module)
            self.assertEqual(problems, [], f"kind {name} should be clean")

    def test_effects_annotation_attaches_in_declaration_order(self):
        module = self.elab.desugar(SRC)
        by_name = {f.name: f for f in module.fns}
        self.assertEqual(by_name["handle"].effects, frozenset({EffectKind.NET, EffectKind.DB}))
        self.assertEqual(by_name["read"].effects, frozenset({EffectKind.FS}))

    def test_extern_effect_annotation_attaches(self):
        module = self.elab.desugar(SRC)
        by_name = {e.name: e for e in module.externs}
        self.assertIs(by_name["net_call"].effect, EffectKind.NET)
        self.assertIs(by_name["db_get"].effect, EffectKind.DB)
        self.assertIs(by_name["fs_read"].effect, EffectKind.FS)

    def test_extern_default_effect_is_net(self):
        # no effect: line -> NET (the pre-week-4 default, E1 corpus unaffected)
        bare = """\
extern fn stub(x: int) -> Result<int, str>
  requires x > 0
ensures is_ok(result) == (x < 1000);
trust: "oracle reference stub"

effects: net
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
        self.assertIs(module.externs[0].effect, EffectKind.NET)
        self.assertEqual(EffectsPass().validate(module), [])


class TestParseEffectSet(unittest.TestCase):
    def test_parse_list(self):
        self.assertEqual(
            parse_effect_set("net, db, fs"),
            frozenset({EffectKind.NET, EffectKind.DB, EffectKind.FS}),
        )

    def test_parse_spaces(self):
        self.assertEqual(parse_effect_set(" net ,  db "), frozenset({EffectKind.NET, EffectKind.DB}))

    def test_unknown_kind_hard_error(self):
        from wardcore.elaborator import ElaborationError

        with self.assertRaises(ElaborationError) as ctx:
            parse_effect_set("net, quantum")
        self.assertIn("unknown effect kind", str(ctx.exception))

    def test_pipeline_exposes_inferred_map(self):
        elab = Elaborator()
        elab.transpile(SRC)
        self.assertIsNotNone(elab.effects_inferred)
        self.assertEqual(
            elab.effects_inferred["handle"],
            frozenset({EffectKind.NET, EffectKind.DB}),
        )


if __name__ == "__main__":
    unittest.main()
