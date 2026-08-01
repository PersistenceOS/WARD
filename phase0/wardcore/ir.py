"""ward-core IR v0.1 — the typed core calculus for Ward Phase 2 (week 1).

Design source: files/ward-phase2-scoping.md §4 (ward-core v0.1 calculus sketch
and typing rules T1–T8), §5 (elaborator pipeline).
Grounding: phase0/grammar/ward0.lark (the surface subset) and
phase0/transpiler/transpiler.py (the current syntax-directed pass this IR
formalizes), plus the real w-task shape (benchmarks/w_tasks/w1_payment_chain).

This is the *target* the elaborator will produce from ward0 surface syntax and
the checker validates before Dafny emission. Week-1 scope: the data model plus
the structural obligations (T1–T8) that are checkable without a full
elaborator. Parsing / elaboration / Dafny emission are weeks 2+.

Key structural decisions carried from Phase-1 findings (scoping doc §2):
- R1/T2: contracts are ANNOTATION nodes (Contract), never statements; a
  Contract can never appear in a Block. No trailing semicolon — the node has no
  such field.
- R3/T3: `extern fn` requires a contract AND a `trust:` string (forward
  decision: contract-less externs are structurally impossible).
- R6: per-function `tier` and per-function `effects` live on the Function node.
- T4: every call to an extern carries `checked: bool` — the elaborator's extern
  pass rewrites stub calls to `_checked`; validation rejects unchecked direct
  stub calls.
- T8: the only loop node is `Loop` (bounded `for` over range) — there is no
  `while` node; no recursion.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Tier(enum.Enum):
    """Per-function verification tier (design doc §4c; scoping doc T6/R6)."""

    PROVEN = "Proven"
    CONTRACTED = "Contracted"
    TESTED = "Tested"


class EffectKind(enum.Enum):
    """Effect vocabulary (design doc §4f subset; scoping doc T5).

    async/render are deferred per scoping doc §3.
    """

    NET = "net"
    DB = "db"
    FS = "fs"
    MUT = "mut"
    PARTIAL = "partial"


class ContractKind(enum.Enum):
    REQUIRES = "requires"
    ENSURES = "ensures"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Type:
    """A ward-core type. name is one of int|bool|str|Unit|Result|List|Record;
    args carries element/error/field types (empty for nullary types)."""

    name: str
    args: tuple["Type", ...] = ()


def TInt() -> Type:
    return Type("int")


def TBool() -> Type:
    return Type("bool")


def TStr() -> Type:
    return Type("str")


def TUnit() -> Type:
    return Type("Unit")


def TResult(ok: Type, err: Type) -> Type:
    return Type("Result", (ok, err))


def TList(elem: Type) -> Type:
    return Type("List", (elem,))


# ---------------------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Expr:
    """Base class for ward-core expressions."""


@dataclass(frozen=True)
class IntLit(Expr):
    value: int


@dataclass(frozen=True)
class BoolLit(Expr):
    value: bool


@dataclass(frozen=True)
class StrLit(Expr):
    value: str


@dataclass(frozen=True)
class Var(Expr):
    name: str


@dataclass(frozen=True)
class Unary(Expr):
    op: str  # "not" | "-"
    operand: Expr


@dataclass(frozen=True)
class Binary(Expr):
    op: str  # + - * / % == != < <= > >= and or
    left: Expr
    right: Expr


@dataclass(frozen=True)
class Indexed(Expr):
    base: str
    index: Expr


@dataclass(frozen=True)
class Quant(Expr):
    """Quantified contract term (ward0: `forall/exists x in range(lo,hi) :: e`)."""

    kw: str  # "forall" | "exists"
    var: str
    lo: Expr
    hi: Expr
    body: Expr


@dataclass(frozen=True)
class Call(Expr):
    """A call expression. `checked` is T4: routed through the generated
    `_checked` wrapper for extern stubs (elaborator's extern pass sets it)."""

    callee: str
    args: tuple[Expr, ...] = ()
    checked: bool = False


# Builtins/constructors legal in contract (pure) position — mirrors the
# transpiler's BUILTINS/CONSTRUCTORS so the checker agrees with the emitter.
CONTRACT_PURE_CALLEES = {"len", "is_ok", "is_err", "unwrap_ok", "unwrap_err", "Ok", "Err"}


def expr_calls(e: Expr) -> Iterator[Call]:
    """Yield every Call node inside an expression, depth-first."""
    if isinstance(e, Call):
        yield e
    for child in _expr_children(e):
        yield from expr_calls(child)


def _expr_children(e: Expr) -> tuple[Expr, ...]:
    if isinstance(e, Unary):
        return (e.operand,)
    if isinstance(e, Binary):
        return (e.left, e.right)
    if isinstance(e, Indexed):
        return (e.index,)
    if isinstance(e, Quant):
        return (e.lo, e.hi, e.body)
    if isinstance(e, Call):
        return e.args
    return ()


def expr_names(e: Expr) -> Iterator[str]:
    """Yield every variable name referenced in an expression."""
    if isinstance(e, Var):
        yield e.name
    for child in _expr_children(e):
        yield from expr_names(child)


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Block:
    stmts: tuple["Stmt", ...] = ()


@dataclass(frozen=True)
class Stmt:
    """Base class for ward-core statements.

    Note: Contract is NOT a Stmt subclass — contracts are annotations on
    Function/ExternFn (R1/T2), structurally incapable of appearing in a Block.
    """


@dataclass(frozen=True)
class VarDecl(Stmt):
    name: str
    type: Type
    value: Expr


@dataclass(frozen=True)
class Assign(Stmt):
    target: str
    op: str  # = += -= *= /= %=
    value: Expr


@dataclass(frozen=True)
class If(Stmt):
    cond: Expr
    then_branch: Block
    else_branch: Block | None = None


@dataclass(frozen=True)
class Loop(Stmt):
    """The ONLY loop node (T8): bounded `for var in range(lo, hi)`. No while."""

    var: str
    lo: Expr
    hi: Expr
    invariants: tuple[Expr, ...] = ()
    body: Block = field(default_factory=Block)


@dataclass(frozen=True)
class Return(Stmt):
    value: Expr | None = None


@dataclass(frozen=True)
class CallStmt(Stmt):
    call: Call


def stmt_calls(s: Stmt) -> Iterator[Call]:
    """Yield every Call reachable from a statement (incl. nested blocks)."""
    if isinstance(s, VarDecl):
        yield from expr_calls(s.value)
    elif isinstance(s, Assign):
        yield from expr_calls(s.value)
    elif isinstance(s, If):
        yield from expr_calls(s.cond)
        for st in s.then_branch.stmts:
            yield from stmt_calls(st)
        if s.else_branch:
            for st in s.else_branch.stmts:
                yield from stmt_calls(st)
    elif isinstance(s, Loop):
        yield from expr_calls(s.lo)
        yield from expr_calls(s.hi)
        for inv in s.invariants:
            yield from expr_calls(inv)
        for st in s.body.stmts:
            yield from stmt_calls(st)
    elif isinstance(s, Return):
        if s.value is not None:
            yield from expr_calls(s.value)
    elif isinstance(s, CallStmt):
        yield from expr_calls(s.call)


def stmt_names(s: Stmt) -> Iterator[str]:
    """Yield every variable name referenced anywhere in a statement."""
    if isinstance(s, VarDecl):
        yield s.name
        yield from expr_names(s.value)
    elif isinstance(s, Assign):
        yield s.target
        yield from expr_names(s.value)
    elif isinstance(s, If):
        yield from expr_names(s.cond)
        for st in s.then_branch.stmts:
            yield from stmt_names(st)
        if s.else_branch:
            for st in s.else_branch.stmts:
                yield from stmt_names(st)
    elif isinstance(s, Loop):
        yield s.var
        yield from expr_names(s.lo)
        yield from expr_names(s.hi)
        for inv in s.invariants:
            yield from expr_names(inv)
        for st in s.body.stmts:
            yield from stmt_names(st)
    elif isinstance(s, Return):
        if s.value is not None:
            yield from expr_names(s.value)
    elif isinstance(s, CallStmt):
        yield from expr_names(s.call)


# ---------------------------------------------------------------------------
# Contracts, params, functions, externs, modules
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Contract:
    """An annotation node (R1/T2). Never a statement; no trailing semicolon."""

    kind: ContractKind
    expr: Expr


@dataclass(frozen=True)
class Param:
    name: str
    type: Type
    linear: bool = False  # T7: money/token/capability — consume-exactly-once


@dataclass(frozen=True)
class Function:
    name: str
    params: tuple[Param, ...] = ()
    ret: Type = TUnit()
    requires: tuple[Contract, ...] = ()
    ensures: tuple[Contract, ...] = ()
    effects: frozenset[EffectKind] = frozenset()  # T5 declared effect set
    tier: Tier = Tier.PROVEN  # R6/T6 per-function tier
    body: Block = field(default_factory=Block)


@dataclass(frozen=True)
class ExternFn:
    """An unverified library stub. T3: contract AND trust are mandatory."""

    name: str
    params: tuple[Param, ...] = ()
    ret: Type = TUnit()
    requires: tuple[Contract, ...] = ()
    ensures: tuple[Contract, ...] = ()
    trust: str = ""
    effect: EffectKind = EffectKind.NET


@dataclass(frozen=True)
class Module:
    """A compilation unit: multi-function, with externs and per-fn metadata."""

    name: str
    externs: tuple[ExternFn, ...] = ()
    fns: tuple[Function, ...] = ()
    deps: tuple[str, ...] = ()  # pinned dependency references (design doc §5 resp. 5)


# ---------------------------------------------------------------------------
# Structural validation (the checkable T1–T8 obligations)
# ---------------------------------------------------------------------------

def validate_module(module: Module) -> list[str]:
    """Return a list of structural problems (empty = module is well-formed).

    Encodes the T1–T8 obligations that are checkable at the IR level without a
    full elaborator. Per-path linearity (T7 full), effect *inference* (T5), and
    termination analysis are deferred to later weeks and noted where relevant.
    """
    problems: list[str] = []
    extern_names = {e.name for e in module.externs}
    fn_names = {f.name for f in module.fns}

    # ---- duplicate names -------------------------------------------------
    dup_externs = _duplicates(e.name for e in module.externs)
    dup_fns = _duplicates(f.name for f in module.fns)
    problems += [f"duplicate extern name: {n}" for n in dup_externs]
    problems += [f"duplicate function name: {n}" for n in dup_fns]

    # ---- T3: extern contract + trust mandatory ---------------------------
    for ext in module.externs:
        if not (ext.requires or ext.ensures):
            problems.append(f"extern {ext.name}: contract is mandatory (T3)")
        if not ext.trust.strip():
            problems.append(f"extern {ext.name}: trust annotation is mandatory (T3)")

    for fn in module.fns:
        # ---- T1/T2: contract terms are pure annotations ------------------
        for contract in (*fn.requires, *fn.ensures):
            for call in expr_calls(contract.expr):
                if call.callee not in CONTRACT_PURE_CALLEES:
                    problems.append(
                        f"{fn.name}: contract term calls {call.callee!r} — "
                        "no method calls in contracts (T1/T2)"
                    )

        # ---- T4: every extern call is routed through _checked ------------
        for call in _fn_calls(fn):
            if call.callee in extern_names and not call.checked:
                problems.append(
                    f"{fn.name}: direct stub call to {call.callee} is not "
                    "routed through _checked wrapper (T4)"
                )

        # ---- T5: declared effects must be used (net/db/fs checkable) -----
        used_effects = _used_effects(fn, module)
        for eff in fn.effects:
            if eff in (EffectKind.NET, EffectKind.DB, EffectKind.FS):
                if eff not in used_effects:
                    problems.append(
                        f"{fn.name}: declared effect {eff.value} is unused (T5)"
                    )

        # ---- T7 (structural pre-check): linear params must be used -------
        used_names = set(_fn_names(fn))
        for param in fn.params:
            if param.linear and param.name not in used_names:
                problems.append(
                    f"{fn.name}: linear param {param.name} is never used — "
                    "cannot be dropped (T7 structural)"
                )

        # ---- T8: no recursion --------------------------------------------
        for call in _fn_calls(fn):
            if call.callee == fn.name:
                problems.append(f"{fn.name}: recursion is forbidden (T8)")

    return problems


def _duplicates(names: Iterator[str]) -> list[str]:
    seen: set[str] = set()
    return [n for n in names if n in seen or seen.add(n)]


def _fn_calls(fn: Function) -> Iterator[Call]:
    for st in fn.body.stmts:
        yield from stmt_calls(st)


def _fn_names(fn: Function) -> Iterator[str]:
    for st in fn.body.stmts:
        yield from stmt_names(st)


def _used_effects(fn: Function, module: Module) -> set[EffectKind]:
    """Effects actually exercised: an extern call whose extern declares the effect."""
    extern_effect = {e.name: e.effect for e in module.externs}
    used: set[EffectKind] = set()
    for call in _fn_calls(fn):
        eff = extern_effect.get(call.callee)
        if eff is not None:
            used.add(eff)
    return used
