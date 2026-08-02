"""ward-core elaborator front-end (Phase-2 week 2).

Pipeline (files/ward-phase2-scoping.md §5, weeks 1-2 scope):

    model output
      -> [extract]  strip fences / `dafny` echo / leading prose   (R2: one canonical step)
      -> [parse]    ward0 grammar (existing ward0.lark)           (R1: contract/statement distinction)
      -> [desugar]  surface tree -> ward-core IR Module           (R1/T1/T2: contracts become annotation nodes;
                                                                    hard ElaborationError on `;` after a contract)
      -> [typecheck] name + callee resolution with hard-error-on-ambiguity;
                     builtin arity; validate_module (T1/T2/T3/T7/T8; T4 deferred to the
                     week-3 extern pass via check_t4=False, T5 deferred to the week-4
                     effects pass via check_t5=False)        (design doc §5.2)
      -> [emit]     IR Module -> Dafny 4, byte-identical to the Phase-0/1
                     Ward0Transpiler output for the same source     (E1: no regression)

E1 gate (scoping doc §6): 100% of the 62 Phase-0 references + 8 w-task references
type-check + emit through this pipeline and the emitted Dafny still verifies.
The emission port deliberately reproduces the transpiler's exact output (hoisted
`var wN := call;` lines, `var n := n;` param-shadowing, `{:extern}{:axiom}` stubs,
`_checked` wrappers, Result datatype gating) so E1 is provable by byte-parity.

Week-2 scope notes (deferred, per scoping doc §5/§6): dependency resolution
(E4b), effect *inference* (T5), per-path linearity (T7), full tier routing
(T6), and Dafny-error translation (R8) land in later weeks.
"""

from __future__ import annotations

from pathlib import Path

from lark import Lark, Token, Tree

from wardcore.dep_pass import DepPass
from wardcore.effects_pass import EffectsPass, parse_effect_set
from wardcore.error_translation import StructuredError, annotate_tightness, translate_errors
from wardcore.extern_pass import ExternPass
from wardcore.linearity_pass import LinearPass
from wardcore.tier_pass import TierPass
from wardcore.tightness_pass import TightnessPass
from wardcore.ir import (
    CONTRACT_PURE_CALLEES,
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
    Indexed,
    IntLit,
    Loop,
    Module,
    Param,
    Paren,
    Quant,
    Return,
    StrLit,
    TBool,
    TInt,
    TList,
    TResult,
    TStr,
    TUnit,
    Tier,
    Type,
    Unary,
    UnitLit,
    Var,
    VarDecl,
    expr_calls,
    expr_names,
    stmt_names,
    validate_module,
)

GRAMMAR_PATH = Path(__file__).resolve().parent.parent / "grammar" / "ward0.lark"

RESERVED = {"result", "len", "is_ok", "is_err", "unwrap_ok", "unwrap_err"}

# builtins legal in expression position -> IR callee name (mirrors transpiler BUILTINS)
BUILTINS = {"len", "is_ok", "is_err", "unwrap_ok", "unwrap_err"}
# constructors legal in expression position (not method calls)
CONSTRUCTORS = {"Ok", "Err"}

BUILTIN_ARITY = {"len": 1, "is_ok": 1, "is_err": 1, "unwrap_ok": 1, "unwrap_err": 1}
# contract terms may only call builtins/constructors (T1) — ir.py's
# CONTRACT_PURE_CALLEES is the same set; imported for the checker.

INDENT = "  "

_TRUST_RE = r'(?m)^[ \t]*trust[ \t]*:[ \t]*"([^"]*)"[ \t]*$'

# R6/T6: `tier: Proven|Contracted|Tested` is a toolchain annotation (like
# `trust:`), stripped pre-parse and attached to fn defs in declaration order.
_TIER_RE = r'(?m)^[ \t]*tier[ \t]*:[ \t]*(Proven|Contracted|Tested)[ \t]*$'

# T5 (week 4): `effect:` on an extern def and `effects:` on a fn def are
# toolchain annotations (like trust/tier), stripped pre-parse and attached in
# declaration order. `effects:` values are a comma-separated kind list
# (net/db/fs/mut/partial); parsed by wardcore.effects_pass.parse_effect_set.
_EFFECT_RE = r'(?m)^[ \t]*effect[ \t]*:[ \t]*(net|db|fs|mut|partial)[ \t]*$'
_EFFECTS_RE = r'(?m)^[ \t]*effects[ \t]*:[ \t]*([^\r\n]+?)[ \t]*$'

# E4b (week 5): `dep: name@spec` is a toolchain annotation in TWO positions:
# before the first definition -> a module-level pinned range declaration
# (populates Module.deps, e.g. `dep: ledger@^2.0.0`); after an extern def ->
# that extern's dependency reference (attached in declaration order like
# trust/effect, e.g. `dep: ledger@2.4.1`). The week-5 dep pass resolves each
# reference against the declared ranges (unresolved / out-of-range /
# ambiguous = hard error).
_DEP_RE = r'(?m)^[ \t]*dep[ \t]*:[ \t]*([A-Za-z_][A-Za-z0-9_-]*)@([^\s\r\n]+?)[ \t]*$'

# T7 (week 6): `linear: name` on an extern def marks that extern parameter as
# the linear-typed capability boundary (money/token/capability — design doc
# §4: linearity is INFERRED by the elaborator for values flowing through a
# linear-typed capability; the model never annotates its own params). The
# extern param is the only capability in v0.1; the linearity pass infers fn
# params from flow into these. Attached to the extern by SOURCE POSITION (the
# line follows its extern def), not declaration order — externs without a
# linear param must not shift indexes (unlike effect/dep, which are
# declaration-ordered because every corpus extern carries one).
_LINEAR_RE = r'(?m)^[ \t]*linear[ \t]*:[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*$'


class ElaborationError(Exception):
    """A hard elaboration error — the elaborator never makes a silent choice
    (design doc §5). Raised for parse failures, contract-rule violations,
    unknown/ambiguous names, and IR structural violations."""


# ---------------------------------------------------------------------------
# R2: canonical candidate extraction
# ---------------------------------------------------------------------------


def extract_candidate(raw: str) -> str:
    """One canonical candidate-extraction step (R2).

    Strips markdown fences, a leading `dafny`/` ```dafny ` echo line, and any
    leading prose up to the first line that looks like code (starts with
    `fn `, `extern fn `, or `method `). Applied uniformly to every arm so the
    elaborator consumes 'model output', not 'code' (Phase-1 Finding 2: the
    raw-Dafny arm broke on a leading `dafny` echo line).
    """
    content = raw.strip()
    if "```" in content:
        content = content[content.index("```") + 3 :]
        if content.startswith("\n"):
            content = content[1:]
        elif content[:12].startswith(("ward0\n", "dafny\n")):
            content = content.split("\n", 1)[1]
        if "```" in content:
            content = content[: content.index("```")]
    lines = content.splitlines()
    while lines and lines[0].strip().lower() in ("dafny", "```dafny"):
        lines.pop(0)
    while lines and not _looks_like_code(lines[0]):
        lines.pop(0)
    return "\n".join(lines).strip() + "\n"


def _looks_like_code(line: str) -> bool:
    s = line.strip()
    return s.startswith(("fn ", "extern fn ", "method ")) or s.startswith("datatype ")


# ---------------------------------------------------------------------------
# Elaborator
# ---------------------------------------------------------------------------


class Elaborator:
    """ward0 surface -> ward-core IR Module -> Dafny 4, with hard errors.

    Mirrors the `Ward0Transpiler` interface (transpile/enforce_boundary/
    trust_report) so the harness can swap to the typed pipeline behind the same
    calls, while the emission output stays byte-identical (E1 gate).
    """

    def __init__(self, grammar_path: Path = GRAMMAR_PATH, enforce_boundary: bool = False):
        self.parser = Lark.open(grammar_path, parser="lalr", start="start", keep_all_tokens=True)
        self.enforce_boundary = enforce_boundary
        self.trust_report: list[dict] = []
        self.tier_plan = None  # set by transpile/elaborate (R6/T6, week 3)
        self.effects_inferred = None  # set by transpile/elaborate (T5, week 4)
        self.dep_resolution = None  # set by transpile/elaborate (E4b, week 5)
        self.linearity_inferred = None  # set by transpile/elaborate (T7, week 6)
        self.tightness = None  # set by transpile/elaborate (I1, advisory)
        self.module: Module | None = None
        self._hoist_counter = 0
        self._used_names: set[str] = set()

    # ------------------------------------------------------------- R2

    def extract(self, raw: str) -> str:
        return extract_candidate(raw)

    # ------------------------------------------------------------- desugar

    def desugar(self, source: str) -> Module:
        """Parse ward0 source and build the IR Module (R1 desugar).

        Contract lines become Contract annotation nodes (never statements —
        T2); a `;` after a contract is a hard ElaborationError (R1). `trust:`
        lines are toolchain annotations pulled off the source, exactly as the
        transpiler does, and attached to externs in declaration order.
        """
        import re

        trust_pairs = re.findall(_TRUST_RE, source)
        tier_values = re.findall(_TIER_RE, source)
        effect_values = re.findall(_EFFECT_RE, source)
        effects_values = re.findall(_EFFECTS_RE, source)
        # T7 (week 6): `linear: name` attaches to the extern whose def it
        # FOLLOWS in the source (positional, not declaration-order — an extern
        # without a linear param must not shift the index of a later one).
        # The named extern param becomes the linear-typed capability boundary.
        extern_defs = [
            (m.start(), m.group(1))
            for m in re.finditer(r'(?m)^[ \t]*extern[ \t]+fn[ \t]+([A-Za-z_][A-Za-z0-9_]*)', source)
        ]
        linear_by_extern: dict[str, str] = {}
        for m in re.finditer(_LINEAR_RE, source):
            preceding = [name for pos, name in extern_defs if pos < m.start()]
            if not preceding:
                raise ElaborationError(
                    f"linear: annotation at line {source[:m.start()].count(chr(10)) + 1} "
                    "appears before any extern def (T7): `linear: name` must "
                    "follow the extern whose param it marks"
                )
            owner = preceding[-1]
            if owner in linear_by_extern:
                raise ElaborationError(
                    f"extern {owner}: multiple linear: annotations (T7) — v0.1 "
                    "marks one capability param per extern"
                )
            linear_by_extern[owner] = m.group(1)
        # E4b (week 5): `dep: name@spec` in two positions — before the first
        # definition they are MODULE pinned ranges (Module.deps); after it they
        # are EXTERN references (ExternFn.dep, attached in declaration order
        # like trust/effect). Positional split: find the first def offset.
        dep_matches = list(re.finditer(_DEP_RE, source))
        first_def = re.search(r'(?m)^[ \t]*(?:fn |extern fn |method |datatype )', source)
        first_def_pos = first_def.start() if first_def else len(source)
        module_deps = [
            m.group(1) + "@" + m.group(2)
            for m in dep_matches
            if m.start() < first_def_pos
        ]
        extern_dep_refs = [
            m.group(1) + "@" + m.group(2)
            for m in dep_matches
            if m.start() >= first_def_pos
        ]
        cleaned = re.sub(_TRUST_RE, "", source)
        cleaned = re.sub(_TIER_RE, "", cleaned)
        cleaned = re.sub(_EFFECT_RE, "", cleaned)
        cleaned = re.sub(_EFFECTS_RE, "", cleaned)
        cleaned = re.sub(_DEP_RE, "", cleaned)
        cleaned = re.sub(_LINEAR_RE, "", cleaned)
        try:
            tree = self.parser.parse(cleaned)
        except Exception as exc:  # lark.UnexpectedToken / UnexpectedInput
            raise ElaborationError(
                f"parse error: {exc}\n"
                "HINT: contracts are annotations, NOT statements — they take no "
                "trailing semicolon; the `{` follows the last requires/ensures "
                "line directly (R1)."
            ) from exc
        file_node = tree.children[0]
        defs = [
            d.children[0]
            for d in file_node.children
            if isinstance(d, Tree) and d.data == "definition"
        ]
        if not defs:
            raise ElaborationError("no fn definitions found")

        externs: list[ExternFn] = []
        fns: list[Function] = []
        trust_idx = 0
        fn_tier_idx = 0
        effect_idx = 0
        effects_idx = 0
        dep_ref_idx = 0
        extern_count = 0
        for d in defs:
            if d.data == "extern_def":
                extern_count += 1
                info = self._extern_info(d)
                trust = trust_pairs[trust_idx] if trust_idx < len(trust_pairs) else ""
                trust_idx += 1
                effect = (
                    EffectKind(effect_values[effect_idx])
                    if effect_idx < len(effect_values)
                    else EffectKind.NET
                )
                effect_idx += 1
                dep = (
                    extern_dep_refs[dep_ref_idx]
                    if dep_ref_idx < len(extern_dep_refs)
                    else ""
                )
                dep_ref_idx += 1
                # T7 (week 6): mark the extern's linear capability param (the
                # name must be one of the extern's params, else hard error).
                lin_name = linear_by_extern.get(info["name"])
                params = info["params"]
                if lin_name is not None:
                    pnames = {p.name for p in params}
                    if lin_name not in pnames:
                        raise ElaborationError(
                            f"extern {info['name']}: linear: names unknown "
                            f"param {lin_name!r} (T7)"
                        )
                    params = tuple(
                        Param(p.name, p.type, linear=(p.name == lin_name)) for p in params
                    )
                externs.append(
                    ExternFn(
                        name=info["name"],
                        params=params,
                        ret=info["ret"],
                        requires=info["requires"],
                        ensures=info["ensures"],
                        trust=trust,
                        effect=effect,
                        dep=dep,
                    )
                )
            elif d.data == "fn_def":
                tier = (
                    Tier(tier_values[fn_tier_idx])
                    if fn_tier_idx < len(tier_values)
                    else Tier.PROVEN
                )
                fn_tier_idx += 1
                effects = (
                    parse_effect_set(effects_values[effects_idx])
                    if effects_idx < len(effects_values)
                    else frozenset()
                )
                effects_idx += 1
                fns.append(self._fn_info(d, tier, effects))

        if dep_ref_idx < len(extern_dep_refs):
            # a `dep:` reference with no extern left to attach to — never a
            # silent drop (design doc §5); the model gets it back immediately
            raise ElaborationError(
                f"{len(extern_dep_refs) - dep_ref_idx} dependency reference(s) "
                "after the last extern def (E4b): each `dep:` line must follow "
                "an extern it belongs to"
            )
        self.trust_report = [{"stub": e.name, "trust": e.trust} for e in externs]
        self.module = Module(name="main", externs=tuple(externs), fns=tuple(fns), deps=tuple(module_deps))
        return self.module

    def _extern_info(self, fn: Tree) -> dict:
        name = next(c.value for c in fn.children if isinstance(c, Token) and c.type == "NAME")
        params = next((c for c in fn.children if isinstance(c, Tree) and c.data == "params"), None)
        ret_type = next(c for c in fn.children if isinstance(c, Tree) and c.data == "type")
        contracts = [c for c in fn.children if isinstance(c, Tree) and c.data == "contract"]
        param_list = [] if params is None else params.children
        params_ir = [
            Param(p.children[0].value, self._type_ir(p.children[2]))
            for p in param_list
            if isinstance(p, Tree)
        ]
        requires, ensures = self._contracts_ir(contracts)
        return {
            "name": name,
            "params": tuple(params_ir),
            "ret": self._type_ir(ret_type),
            "requires": tuple(requires),
            "ensures": tuple(ensures),
        }

    def _fn_info(self, fn: Tree, tier: Tier = Tier.PROVEN, effects: frozenset[EffectKind] = frozenset()) -> Function:
        name = next(c.value for c in fn.children if isinstance(c, Token) and c.type == "NAME")
        if name in RESERVED:
            raise ElaborationError(f"identifier {name!r} is reserved")
        params = next((c for c in fn.children if isinstance(c, Tree) and c.data == "params"), None)
        ret_type = next(c for c in fn.children if isinstance(c, Tree) and c.data == "type")
        contracts = [c for c in fn.children if isinstance(c, Tree) and c.data == "contract"]
        block = next(c for c in fn.children if isinstance(c, Tree) and c.data == "block")
        param_list = [] if params is None else params.children
        params_ir = tuple(
            Param(p.children[0].value, self._type_ir(p.children[2]))
            for p in param_list
            if isinstance(p, Tree)
        )
        requires, ensures = self._contracts_ir(contracts)
        return Function(
            name=name,
            params=params_ir,
            ret=self._type_ir(ret_type),
            requires=tuple(requires),
            ensures=tuple(ensures),
            effects=effects,
            tier=tier,
            body=self._block_ir(block),
        )

    def _contracts_ir(self, contracts: list[Tree]) -> tuple[list[Contract], list[Contract]]:
        requires, ensures = [], []
        for c in contracts:
            if c.children[0].type == "REQUIRES":
                requires.append(Contract(ContractKind.REQUIRES, self._expr_ir(c.children[1])))
            elif c.children[0].type == "ENSURES":
                ensures.append(Contract(ContractKind.ENSURES, self._expr_ir(c.children[1])))
        return requires, ensures

    def _type_ir(self, t: Tree) -> Type:
        if not isinstance(t, Tree):
            raise ElaborationError(f"expected type node, got {t!r}")
        first = t.children[0]
        if isinstance(first, Token):
            if first.type == "INT_TYPE":
                return TInt()
            if first.type == "BOOL_TYPE":
                return TBool()
            if first.type == "STR_TYPE":
                return TStr()
            if first.type == "UNIT_TYPE":
                return TUnit()
            if first.type == "RESULT_TYPE":
                return TResult(self._type_ir(t.children[2]), self._type_ir(t.children[4]))
            if first.type == "LIST_TYPE":
                return TList(self._type_ir(t.children[2]))
        raise ElaborationError(f"unsupported type node {t!r}")

    def _expr_ir(self, node) -> object:
        if isinstance(node, Token):
            return self._token_ir(node)
        data = node.data
        if data == "call":
            name = node.children[0].value
            args = self._args_ir(node)
            return Call(name, tuple(args))
        if data == "indexed":
            base = node.children[0].value
            return Indexed(base, self._expr_ir(node.children[2]))
        if data == "unit_lit":
            return UnitLit()
        if data == "quantifier":
            kw = node.children[0].children[0].value
            var = node.children[1].value
            lo = self._expr_ir(node.children[5])
            hi = self._expr_ir(node.children[7])
            body = self._expr_ir(node.children[10])
            return Quant(kw, var, lo, hi, body)
        if data in ("or_expr", "and_expr", "sum", "product"):
            return self._fold_chain_ir(node.children)
        if data == "not_expr":
            if len(node.children) != 2:
                return self._expr_ir(node.children[0])
            return Unary("not", self._expr_ir(node.children[1]))
        if data == "comparison":
            return self._fold_chain_ir(node.children)
        if data == "comp_op":
            return node.children[0].value
        if data == "factor":
            if len(node.children) == 1:
                return self._expr_ir(node.children[0])
            if node.children[0].type == "MINUS":
                return Unary("-", self._expr_ir(node.children[1]))
            # parenthesized expression — preserve it (Paren) for byte-exact emission
            return Paren(self._expr_ir(node.children[1]))
        if data == "expr":
            return self._expr_ir(node.children[0])
        raise ElaborationError(f"unsupported expression node {data!r}")

    def _token_ir(self, tok: Token) -> object:
        if tok.type == "TRUE":
            return BoolLit(True)
        if tok.type == "FALSE":
            return BoolLit(False)
        if tok.type == "NUM":
            return IntLit(int(tok.value))
        if tok.type == "STRING":
            return StrLit(tok.value[1:-1])
        if tok.type == "NAME":
            return Var(tok.value)
        raise ElaborationError(f"unsupported token {tok!r}")

    def _args_ir(self, call: Tree) -> list[object]:
        args = next((c for c in call.children if isinstance(c, Tree) and c.data == "args"), None)
        if args is None:
            return []
        return [self._expr_ir(e) for e in args.children if isinstance(e, (Tree, Token)) and not (isinstance(e, Token) and e.type == "COMMA")]

    def _fold_chain_ir(self, items: list) -> object:
        """Left-fold a chain into nested Binary nodes.

        The transpiler folds the same chains flat (`a + b + c`); left-nesting
        Binary and emitting without parens reproduces the flat output because
        ward0's grammar is left-associative and its precedence is 1:1 with
        Dafny's (the transpiler's contract).
        """
        out = self._expr_ir(items[0])
        i = 1
        while i < len(items):
            op = items[i]
            opval = op.children[0].value if isinstance(op, Tree) else op.value
            right = self._expr_ir(items[i + 1])
            out = Binary(opval, out, right)
            i += 2
        return out

    def _block_ir(self, block: Tree) -> Block:
        stmts = []
        for s in block.children:
            if not isinstance(s, Tree):
                continue
            if s.data == "stmt":
                s = s.children[0]
            stmts.append(self._stmt_ir(s))
        return Block(stmts=tuple(stmts))

    def _stmt_ir(self, s: Tree) -> object:
        data = s.data
        if data == "var_decl":
            return VarDecl(s.children[1].value, self._type_ir(s.children[3]), self._expr_ir(s.children[5]))
        if data == "assign":
            lvalue, op, expr = s.children[0], s.children[1], s.children[2]
            if isinstance(lvalue, Tree) and len(lvalue.children) > 1:
                raise ElaborationError("record field assignment not supported in ward0 v0.1")
            opval = op.children[0].value if isinstance(op, Tree) else op.value
            return Assign(lvalue.children[0].value, opval, self._expr_ir(expr))
        if data == "if_stmt":
            cond = self._expr_ir(s.children[1])
            then_b = self._block_ir(s.children[2])
            else_b = self._block_ir(s.children[4]) if len(s.children) > 4 else None
            return If(cond, then_b, else_b)
        if data == "for_stmt":
            var = s.children[1].value
            lo = self._expr_ir(s.children[5])
            hi = self._expr_ir(s.children[7])
            invs = [
                self._expr_ir(c.children[1])
                for c in s.children[9:-1]
                if isinstance(c, Tree) and c.data == "loop_invariant"
            ]
            return Loop(var, lo, hi, tuple(invs), self._block_ir(s.children[-1]))
        if data == "return_stmt":
            if len(s.children) == 3 and s.children[1] is not None:
                return Return(self._expr_ir(s.children[1]))
            return Return(None)
        if data == "call_stmt":
            call = next(c for c in s.children if isinstance(c, Tree) and c.data == "call")
            return CallStmt(self._expr_ir(call))
        raise ElaborationError(f"unsupported statement node {data!r}")

    # ------------------------------------------------------------- type check

    def type_check(self, module: Module) -> list[str]:
        """Return a list of type/elaboration problems (empty = clean).

        Hard-error-on-ambiguity (design doc §5.2): every name and every callee
        must resolve to exactly one thing; unknown or ambiguous references are
        problems, never silent choices. Plus builtin arity and the structural
        T1/T2/T3/T7/T8 obligations (T4 deferred to the week-3 extern pass via
        check_t4=False, T5 deferred to the week-4 effects pass via
        check_t5=False — EffectsPass is the single authoritative T5).
        """
        problems: list[str] = []
        extern_names = {e.name for e in module.externs}
        fn_names = {f.name for f in module.fns}

        for ext in module.externs:
            if ext.name in RESERVED:
                problems.append(f"extern {ext.name!r} is reserved")
        for fn in module.fns:
            problems += self._check_fn_names(fn, extern_names, fn_names)

        # structural obligations. T4 deferred (desugar keeps plain stub calls;
        # the week-3 extern pass rewrites them and re-validates). T3-trust
        # also deferred: the Phase-0/1 reference corpus composes externs from
        # JSON descriptors without `trust:` lines; the week-3 extern pass
        # (R3/T3) attaches and validates trust. The contract-mandatory half
        # of T3 stays enforced (check_t3_trust only relaxes the trust string).
        problems += validate_module(module, check_t4=False, check_t3_trust=False, check_t5=False)
        return problems

    def _check_fn_names(self, fn: Function, extern_names: set[str], fn_names: set[str]) -> list[str]:
        problems: list[str] = []
        scope = [set(p.name for p in fn.params)]  # param NAMES in scope; nested blocks push/pop
        callee_ok = BUILTINS | CONSTRUCTORS | extern_names | fn_names

        def declared(n: str) -> bool:
            return any(n in s for s in scope)

        def walk_expr(e) -> None:
            if isinstance(e, Var):
                if not declared(e.name) and e.name not in RESERVED:
                    problems.append(f"{fn.name}: undefined name {e.name!r}")
            elif isinstance(e, Indexed):
                # the base name is a reference too — check it like a Var
                if not declared(e.base) and e.base not in RESERVED:
                    problems.append(f"{fn.name}: undefined name {e.base!r}")
                walk_expr(e.index)
            elif isinstance(e, Call):
                if e.callee in (BUILTINS & extern_names) or e.callee in (fn_names & extern_names):
                    problems.append(
                        f"{fn.name}: ambiguous callee {e.callee!r} (builtin/extern/function overlap)"
                    )
                elif e.callee not in callee_ok:
                    problems.append(f"{fn.name}: undefined callee {e.callee!r}")
                if e.callee in BUILTIN_ARITY and len(e.args) != BUILTIN_ARITY[e.callee]:
                    problems.append(
                        f"{fn.name}: {e.callee}() takes exactly {BUILTIN_ARITY[e.callee]} argument(s)"
                    )
                for a in e.args:
                    walk_expr(a)
            elif isinstance(e, Quant):
                # quant var is bound within the body only — not in lo/hi
                walk_expr(e.lo)
                walk_expr(e.hi)
                scope.append({e.var})
                walk_expr(e.body)
                scope.pop()
            else:
                for child in _expr_children_ir(e):
                    walk_expr(child)

        def walk_stmt(s) -> None:
            if isinstance(s, VarDecl):
                walk_expr(s.value)
                scope[-1].add(s.name)
            elif isinstance(s, Assign):
                walk_expr(s.value)
                if not declared(s.target):
                    problems.append(f"{fn.name}: undefined target {s.target!r}")
            elif isinstance(s, If):
                walk_expr(s.cond)
                scope.append(set())
                for st in s.then_branch.stmts:
                    walk_stmt(st)
                scope.pop()
                if s.else_branch:
                    scope.append(set())
                    for st in s.else_branch.stmts:
                        walk_stmt(st)
                    scope.pop()
            elif isinstance(s, Loop):
                # the loop var is visible in bounds, invariants AND body — put
                # it in scope before walking the header (t1_all_positive's
                # `invariant n == i` proved this ordering matters)
                scope.append({s.var})
                walk_expr(s.lo)
                walk_expr(s.hi)
                for inv in s.invariants:
                    walk_expr(inv)
                for st in s.body.stmts:
                    walk_stmt(st)
                scope.pop()
            elif isinstance(s, Return):
                if s.value is not None:
                    walk_expr(s.value)
            elif isinstance(s, CallStmt):
                walk_expr(s.call)

        # contracts see params + result only. T1: contract terms may call ONLY
        # pure builtins/constructors (ir.py's CONTRACT_PURE_CALLEES) — never
        # methods, and that includes extern stubs (an extern in `ensures` is an
        # extern method call in expression position: Dafny rejects it with the
        # exact Phase-1 R1 error "expression is not allowed to invoke a method"
        # that this rule pre-registered to hard-error on).
        for contract in (*fn.requires, *fn.ensures):
            for call in expr_calls(contract.expr):
                if call.callee not in CONTRACT_PURE_CALLEES:
                    problems.append(
                        f"{fn.name}: contract term calls {call.callee!r} — no method calls in contracts (T1)"
                    )

        for st in fn.body.stmts:
            walk_stmt(st)
        return problems

    # ------------------------------------------------------------- emit

    def emit(self, module: Module, enforce: bool | None = None) -> str:
        """Emit Dafny 4 from the IR, byte-identical to Ward0Transpiler output."""
        if enforce is None:
            enforce = self.enforce_boundary
        externs = {e.name: e for e in module.externs}
        self._hoist_counter = 0
        self._used_names = self._collect_names(module, externs)

        out = []
        if _module_uses_result(module):
            out.append("datatype Result<T, E> = Ok(value: T) | Err(error: E)")
        for e in module.externs:
            out.append(self._extern_dafny(e))
        if enforce:
            for e in module.externs:
                out.append(self._wrapper_dafny(e))
        for fn in module.fns:
            out.append(self._fn_dafny(fn, externs, enforce))
        emitted = "\n".join(out) + "\n"
        self._last_emitted = emitted
        return emitted

    def transpile(self, source: str) -> str:
        """Convenience: desugar + type-check + extern pass + emit (throws on
        problems). The extern pass is the R3/T3/T4 core pass: it rewrites every
        extern call site to the `_checked` wrapper (T4) and enforces the trust
        half of T3, so no direct stub call can survive elaboration."""
        module = self.desugar(source)
        problems = self.type_check(module)
        if problems:
            raise ElaborationError("; ".join(problems[:5]) + (" ..." if len(problems) > 5 else ""))
        module = ExternPass(enforce=self.enforce_boundary).run(module)
        # R6/T6: per-function tier routing is a core pass (week 3). Validates
        # the T6 cross-tier rule and exposes the per-fn verification plan.
        self.tier_plan = TierPass().run(module)
        # T5 (week 4): effect inference + declared-set enforcement. Exposes
        # the per-fn inferred effect map.
        self.effects_inferred = EffectsPass().run(module)
        # E4b (week 5): dependency resolution against pinned ranges. Exposes
        # the per-extern resolution plan (unresolved / out-of-range /
        # ambiguous = hard error).
        self.dep_resolution = DepPass().run(module)
        # T7 (week 6): linearity inference + consume-exactly-once. Exposes the
        # per-fn inferred linear param names. No `linear:` capability on any
        # extern = no-op (E1 parity untouched).
        self.linearity_inferred = LinearPass().run(module)
        # I1 (advisory): Specification Tightness measured per fn from the
        # SURFACE source (same parser as the calibration — pipeline tau ==
        # calibrated tau). Never blocks, never changes tiers.
        self.tightness = TightnessPass().run(module, source)
        self.module = module
        return self.emit(module)

    # ------------------------------------------------------------- R8: error translation

    def diagnose(self, dafny_detail: str, emitted: str | None = None) -> list[StructuredError]:
        """R8: translate raw `dafny verify` output into (location, violated-
        obligation, counterexample) triples in ward0 surface terms.

        `emitted` defaults to the last emission of this elaborator (from
        transpile/elaborate); the surface line map is built from the emitted
        text itself (what Dafny actually verified — E1 byte-parity sound), so
        no emit internals change and the E1 gate is untouched.

        I1 (advisory): if the advisory tightness pass ran (transpile set
        `self.tightness`), every Proven fn scoring tau < TAU0 gets its tau and
        the specific weak ensures clause(s) appended to the triples — a
        concrete spec-fixing target for the repair loop. Never changes
        kinds/tiers; the advisory rides on the triples' surface text. NOTE:
        the result is no longer strictly "Dafny's errors" — a vacuous Proven
        fn verified cleanly yields a standalone kind="tightness" advisory
        triple even when Dafny reported no error (the anti-slop case).
        """
        if emitted is None:
            emitted = getattr(self, "_last_emitted", "")
        triples = translate_errors(
            dafny_detail,
            emitted=emitted,
            module=self.module,
            extern_names={e.name for e in (self.module.externs if self.module else ())},
        )
        if self.tightness:
            return annotate_tightness(triples, self.tightness)
        return triples

    def transpile(self, source: str) -> str:
        """Convenience: desugar + type-check + extern pass + emit (throws on
        problems). The extern pass is the R3/T3/T4 core pass: it rewrites every
        extern call site to the `_checked` wrapper (T4) and enforces the trust
        half of T3, so no direct stub call can survive elaboration."""
        module = self.desugar(source)
        problems = self.type_check(module)
        if problems:
            raise ElaborationError("; ".join(problems[:5]) + (" ..." if len(problems) > 5 else ""))
        module = ExternPass(enforce=self.enforce_boundary).run(module)
        # R6/T6: per-function tier routing is a core pass (week 3). Validates
        # the T6 cross-tier rule and exposes the per-fn verification plan.
        self.tier_plan = TierPass().run(module)
        # T5 (week 4): effect inference + declared-set enforcement. Exposes
        # the per-fn inferred effect map.
        self.effects_inferred = EffectsPass().run(module)
        # E4b (week 5): dependency resolution against pinned ranges. Exposes
        # the per-extern resolution plan (unresolved / out-of-range /
        # ambiguous = hard error).
        self.dep_resolution = DepPass().run(module)
        # T7 (week 6): linearity inference + consume-exactly-once. Exposes the
        # per-fn inferred linear param names. No `linear:` capability on any
        # extern = no-op (E1 parity untouched).
        self.linearity_inferred = LinearPass().run(module)
        # I1 (advisory): Specification Tightness measured per fn from the
        # SURFACE source (same parser as the calibration — pipeline tau ==
        # calibrated tau). Never blocks, never changes tiers.
        self.tightness = TightnessPass().run(module, source)
        self.module = module
        emitted = self.emit(module)
        self._last_emitted = emitted
        return emitted

    # ------------------------------------------------------------- emit helpers

    def _collect_names(self, module: Module, externs: dict[str, ExternFn]) -> set[str]:
        """Every NAME token the source could contain — mirrors the transpiler's
        scan_values over all defs (extern names, params, contract terms, fn
        bodies) so hoisted `wN` names never collide in either pipeline (E1
        byte-parity requires the identical used-name set)."""
        names = set(RESERVED) | {f"{n}_checked" for n in externs} | set(externs)
        for fn in module.fns:
            names.add(fn.name)
            names.update(p.name for p in fn.params)
            for c in (*fn.requires, *fn.ensures):
                names.update(expr_names(c.expr))
            for st in fn.body.stmts:
                names.update(stmt_names(st))
        for e in module.externs:
            names.update(p.name for p in e.params)
            for c in (*e.requires, *e.ensures):
                names.update(expr_names(c.expr))
        return names

    def _fresh_hoist_name(self) -> str:
        while f"w{self._hoist_counter}" in self._used_names:
            self._hoist_counter += 1
        name = f"w{self._hoist_counter}"
        self._hoist_counter += 1
        self._used_names.add(name)
        return name

    def _extern_dafny(self, e: ExternFn) -> str:
        params = ", ".join(f"{p.name}: {self._type_dafny(p.type)}" for p in e.params)
        sig = f"method {{:extern}}{{:axiom}} {e.name}({params}) returns (result: {self._type_dafny(e.ret)})"
        for r in e.requires:
            sig += f"\n  requires {self._expr_dafny(r.expr)}"
        for en in e.ensures:
            sig += f"\n  ensures {self._expr_dafny(en.expr)}"
        return sig

    def _wrapper_dafny(self, e: ExternFn) -> str:
        params = ", ".join(f"{p.name}: {self._type_dafny(p.type)}" for p in e.params)
        args = ", ".join(p.name for p in e.params)
        lines = [f"method {e.name}_checked({params}) returns (result: {self._type_dafny(e.ret)})"]
        for r in e.requires:
            lines.append(f"  requires {self._expr_dafny(r.expr)}")
        for en in e.ensures:
            lines.append(f"  ensures {self._expr_dafny(en.expr)}")
        lines.append("{")
        lines.append(f"  var r := {e.name}({args});")
        checks = [f"({self._expr_dafny(en.expr, subst={'result': 'r'})})" for en in e.ensures]
        if checks:
            lines.append(f"  if !({' && '.join(checks)}) {{")
            lines.append('    return Err("contract violation");')
            lines.append("  }")
        lines.append("  return r;")
        lines.append("}")
        return "\n".join(lines)

    def _fn_dafny(self, fn: Function, externs: dict[str, ExternFn], enforce: bool) -> str:
        params = ", ".join(f"{p.name}: {self._type_dafny(p.type)}" for p in fn.params)
        # R6/T6: Tested functions carry no proof obligation. `{:verify false}`
        # is the tier's declared semantics (T6), not a wrapper-cheapening hack
        # (R10 — the wrapper stays verified); a Tested fn must never block the
        # module's proof, and this is how the E5 gate proves it.
        attr = " {:verify false}" if fn.tier is Tier.TESTED else ""
        sig = f"method{attr} {fn.name}({params}) returns (result: {self._type_dafny(fn.ret)})"
        for r in fn.requires:
            sig += f"\n  requires {self._expr_dafny(r.expr)}"
        for en in fn.ensures:
            sig += f"\n  ensures {self._expr_dafny(en.expr)}"

        assigned = _assigned_names_ir(fn.body)
        shadows = [p.name for p in fn.params if p.name in assigned]
        body = [f"{INDENT}var {n} := {n};" for n in shadows]
        body += self._block_dafny(fn.body, externs, enforce, indent=1)
        return sig + "\n{\n" + "\n".join(body) + "\n}"

    def _block_dafny(self, block: Block, externs: dict[str, ExternFn], enforce: bool, indent: int) -> list[str]:
        lines = []
        for st in block.stmts:
            pre, emitted = self._hoist_and_emit_stmt(st, externs, enforce, indent)
            lines += pre
            lines += emitted
        return lines

    def _hoist_and_emit_stmt(self, st, externs: dict[str, ExternFn], enforce: bool, indent: int) -> tuple[list[str], list[str]]:
        """Return (pre_lines, stmt_lines). Hoists non-builtin calls in
        expression position into `var wN := call;` lines first, mirroring the
        transpiler's in-place hoisting (innermost-first)."""
        pad = INDENT * indent
        if isinstance(st, (VarDecl, Assign, Return)):
            pre, new_value = self._hoist_expr(st.value, externs, enforce, pad=pad)
            if isinstance(st, Return):
                emitted = [f"{pad}return {self._expr_dafny(new_value)};" if new_value is not None else f"{pad}return;"]
            elif isinstance(st, Assign):
                emitted = self._emit_assign(st, new_value, pad)
            else:
                emitted = [f"{pad}var {st.name} := {self._expr_dafny(new_value)};"]
            return pre, emitted
        if isinstance(st, If):
            pre, new_cond = self._hoist_expr(st.cond, externs, enforce, pad=pad)
            lines = [f"{pad}if {self._expr_dafny(new_cond)} {{"]
            lines += self._block_dafny(st.then_branch, externs, enforce, indent + 1)
            if st.else_branch is not None:
                lines.append(f"{pad}}} else {{")
                lines += self._block_dafny(st.else_branch, externs, enforce, indent + 1)
            lines.append(f"{pad}}}")
            return pre, lines
        if isinstance(st, Loop):
            # the transpiler does not hoist inside the loop header (bounds /
            # invariants) — mirror that exactly
            lines = [f"{pad}for {st.var} := {self._expr_dafny(st.lo)} to {self._expr_dafny(st.hi)}"]
            for inv in st.invariants:
                lines.append(f"{pad}  invariant {self._expr_dafny(inv)}")
            lines.append(f"{pad}{{")
            lines += self._block_dafny(st.body, externs, enforce, indent + 1)
            lines.append(f"{pad}}}")
            return [], lines
        if isinstance(st, CallStmt):
            # a call statement's own call is NOT hoisted (mirrors transpiler's
            # `call_stmt and idx == 0` skip); nested calls in its args are.
            pre, emitted = self._emit_call_stmt(st, externs, enforce, pad)
            return pre, emitted
        return [], []

    def _hoist_expr(self, e, externs: dict[str, ExternFn], enforce: bool, pad: str = INDENT) -> tuple[list[str], object]:
        """Innermost-first hoisting of non-builtin/non-constructor calls.
        Returns (pre_lines, rewritten_expr). Hoisted `var wN := call;` lines
        are emitted at the *containing statement's* indent (the transpiler's
        behavior — `pad = INDENT * block_depth`), so byte-parity holds inside
        nested blocks (E1, w6_crud_handler)."""
        if isinstance(e, Call):
            pre: list[str] = []
            new_args = []
            for a in e.args:
                p, na = self._hoist_expr(a, externs, enforce, pad)
                pre += p
                new_args.append(na)
            new_call = Call(e.callee, tuple(new_args), checked=e.checked)
            if e.callee not in BUILTINS and e.callee not in CONSTRUCTORS:
                name = self._fresh_hoist_name()
                rendered = self._expr_dafny(new_call, externs=externs, enforce=enforce)
                pre.append(f"{pad}var {name} := {rendered};")
                return pre, Var(name)
            return pre, new_call
        if isinstance(e, Unary):
            p, op = self._hoist_expr(e.operand, externs, enforce, pad)
            return p, Unary(e.op, op)
        if isinstance(e, Binary):
            p1, l = self._hoist_expr(e.left, externs, enforce, pad)
            p2, r = self._hoist_expr(e.right, externs, enforce, pad)
            return p1 + p2, Binary(e.op, l, r)
        if isinstance(e, Indexed):
            p, idx = self._hoist_expr(e.index, externs, enforce, pad)
            return p, Indexed(e.base, idx)
        if isinstance(e, Quant):
            p1, lo = self._hoist_expr(e.lo, externs, enforce, pad)
            p2, hi = self._hoist_expr(e.hi, externs, enforce, pad)
            p3, b = self._hoist_expr(e.body, externs, enforce, pad)
            return p1 + p2 + p3, Quant(e.kw, e.var, lo, hi, b)
        if isinstance(e, Paren):
            p, inner = self._hoist_expr(e.inner, externs, enforce, pad)
            return p, Paren(inner)
        return [], e

    def _emit_assign(self, st: Assign, new_value, pad: str) -> list[str]:
        if st.op == "=":
            return [f"{pad}{st.target} := {self._expr_dafny(new_value)};"]
        return [f"{pad}{st.target} := {st.target} {st.op[:-1]} {self._expr_dafny(new_value)};"]

    def _emit_call_stmt(self, st: CallStmt, externs: dict[str, ExternFn], enforce: bool, pad: str) -> tuple[list[str], list[str]]:
        pre: list[str] = []
        new_args = []
        for a in st.call.args:
            p, na = self._hoist_expr(a, externs, enforce, pad)
            pre += p
            new_args.append(na)
        new_call = Call(st.call.callee, tuple(new_args), checked=st.call.checked)
        rendered = self._expr_dafny(new_call, externs=externs, enforce=enforce)
        return pre, [f"{pad}var discard := {rendered};"]

    # ------------------------------------------------------------- expr render

    def _expr_dafny(self, e, externs: dict[str, ExternFn] | None = None, enforce: bool | None = None, subst: dict[str, str] | None = None) -> str:
        if externs is None:
            externs = {x.name: x for x in (self.module.externs if self.module else ())}
        if enforce is None:
            enforce = self.enforce_boundary
        if isinstance(e, IntLit):
            return str(e.value)
        if isinstance(e, BoolLit):
            return "true" if e.value else "false"
        if isinstance(e, StrLit):
            return f'"{e.value}"'
        if isinstance(e, UnitLit):
            return "()"
        if isinstance(e, Var):
            if subst and e.name in subst:
                return subst[e.name]
            return e.name
        if isinstance(e, Unary):
            if e.op == "not":
                return f"!({self._expr_dafny(e.operand, externs, enforce, subst)})"
            return f"-{self._expr_dafny(e.operand, externs, enforce, subst)}"
        if isinstance(e, Binary):
            op = {"and": "&&", "or": "||"}.get(e.op, e.op)
            return (
                f"{self._expr_dafny(e.left, externs, enforce, subst)} {op} "
                f"{self._expr_dafny(e.right, externs, enforce, subst)}"
            )
        if isinstance(e, Indexed):
            return f"{e.base}[{self._expr_dafny(e.index, externs, enforce, subst)}]"
        if isinstance(e, Quant):
            bound = f"{self._expr_dafny(e.lo, externs, enforce, subst)} <= {e.var} < {self._expr_dafny(e.hi, externs, enforce, subst)}"
            body = self._expr_dafny(e.body, externs, enforce, subst)
            if e.kw == "forall":
                return f"forall {e.var} :: {bound} ==> ({body})"
            return f"exists {e.var} :: {bound} && ({body})"
        if isinstance(e, Paren):
            return f"({self._expr_dafny(e.inner, externs, enforce, subst)})"
        if isinstance(e, Call):
            args = ", ".join(self._expr_dafny(a, externs, enforce, subst) for a in e.args)
            if e.callee == "len":
                return f"|{args}|"
            if e.callee == "is_ok":
                return f"{args}.Ok?"
            if e.callee == "is_err":
                return f"{args}.Err?"
            if e.callee == "unwrap_ok":
                return f"{args}.value"
            if e.callee == "unwrap_err":
                return f"{args}.error"
            if e.callee in externs and (e.checked or enforce):
                return f"{e.callee}_checked({args})"
            return f"{e.callee}({args})"
        raise ElaborationError(f"cannot render expression node {e!r}")

    def _type_dafny(self, t: Type) -> str:
        if t.name == "int":
            return "int"
        if t.name == "bool":
            return "bool"
        if t.name == "str":
            return "string"
        if t.name == "Unit":
            return "()"
        if t.name == "Result":
            return f"Result<{self._type_dafny(t.args[0])}, {self._type_dafny(t.args[1])}>"
        if t.name == "List":
            return f"seq<{self._type_dafny(t.args[0])}>"
        raise ElaborationError(f"unsupported type {t!r}")


# ---------------------------------------------------------------------------
# IR walkers shared by type-check and emit
# ---------------------------------------------------------------------------


def _expr_children_ir(e) -> list:
    """IR child walk used by the scope-walking type-check. ir.py's private
    `_expr_children` does the same; kept here so the scope walker doesn't cross
    the underscore boundary. Public `expr_calls`/`stmt_names`/`expr_names` from
    ir.py are reused directly by the checker and name collector."""
    if isinstance(e, Unary):
        return [e.operand]
    if isinstance(e, Binary):
        return [e.left, e.right]
    if isinstance(e, Indexed):
        return [e.index]
    if isinstance(e, Quant):
        return [e.lo, e.hi, e.body]
    if isinstance(e, Call):
        return list(e.args)
    if isinstance(e, Paren):
        return [e.inner]
    return []


def _assigned_names_ir(block: Block) -> set[str]:
    names: set[str] = set()

    def walk_stmt(st) -> None:
        if isinstance(st, Assign):
            names.add(st.target)
        elif isinstance(st, If):
            for x in st.then_branch.stmts:
                walk_stmt(x)
            if st.else_branch:
                for x in st.else_branch.stmts:
                    walk_stmt(x)
        elif isinstance(st, Loop):
            for x in st.body.stmts:
                walk_stmt(x)

    for st in block.stmts:
        walk_stmt(st)
    return names


def _module_uses_result(module: Module) -> bool:
    def uses(t: Type) -> bool:
        return t.name == "Result" or any(uses(a) for a in t.args)

    for e in module.externs:
        if uses(e.ret) or any(uses(p.type) for p in e.params):
            return True
    for f in module.fns:
        if uses(f.ret) or any(uses(p.type) for p in f.params):
            return True
    return False


def elaborate(source: str, enforce: bool = False) -> tuple[Module, str]:
    """Full pipeline convenience: extract is a no-op for source that is already
    code; returns (module, dafny). The extern pass runs inside, so enforce=True
    routes every extern call through the generated `_checked` wrapper (R3/T4)
    and enforces the T3 trust annotation."""
    elab = Elaborator(enforce_boundary=enforce)
    module = elab.desugar(source)
    problems = elab.type_check(module)
    if problems:
        raise ElaborationError("; ".join(problems[:5]) + (" ..." if len(problems) > 5 else ""))
    module = ExternPass(enforce=enforce).run(module)
    elab.tier_plan = TierPass().run(module)
    elab.effects_inferred = EffectsPass().run(module)
    elab.dep_resolution = DepPass().run(module)
    elab.linearity_inferred = LinearPass().run(module)
    # I1 (advisory): Specification Tightness per fn (surface source — pipeline
    # tau == calibrated tau). Never blocks, never changes tiers.
    elab.tightness = TightnessPass().run(module, source)
    return module, elab.emit(module)
