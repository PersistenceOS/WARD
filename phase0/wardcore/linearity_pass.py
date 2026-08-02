"""ward-core linearity pass (Phase-2 week 6) — T7 as a core pass.

Pre-registered scope (files/ward-phase2-scoping.md §2 R3-note/§3.1, §4 T7,
§6 E6; design doc §4, §5 resp. 4):

- **T7 (linearity):** a `linear`-typed value (money, token, one-time
  capability) is consumed EXACTLY ONCE on every path. Minimal v0.1 semantics
  (scoping doc §3.1): "linear values may not be copied or dropped implicitly."
- **Inferred, never annotated by the model** (design doc §5 resp. 4): the
  elaborator infers ownership/linearity for any value flowing through a
  *linear-typed capability*. The only capability boundary in v0.1 is the
  extern: `linear: name` on an extern def marks that extern parameter as the
  capability (toolchain annotation, same family as `effect:`/`dep:`/`trust:`).
  A fn parameter is linear iff it can reach a linear consuming position —
  directly, transitively through fn calls with linear parameters, or through
  local moves — computed as a module-level fixpoint.
- **v0.1 boundary (scoping doc §7 risk row; E6 is pre-registered as a scope
  cut if it fails):**
  - consuming positions are ONLY: a bare argument to a linear callee
    parameter, a move into a local (`var x = v` / `x = v`), and a return;
  - every OTHER mention of a linear value (condition, arithmetic,
    comparison, indexing, non-linear call argument) is a COPY -> hard error;
  - a linear value consumed twice on a path is a DOUBLE-USE -> hard error;
  - a linear value never consumed on a path is a DROP -> hard error;
  - linear values may not appear inside a loop (bounds, invariants, body) —
    the consumption count across iterations is not trackable in v0.1;
  - call results and return-value linearity at the call site are NOT tracked
    (documented limitation — the extern is the only capability boundary).

E6 gate (scoping doc §6): "Linear values are consumed exactly once on every
path; probes for copy / drop / double-use all fail; money-transfer oracle
passes." Gate runner: wardcore/e6_gate.py.
"""

from __future__ import annotations

from wardcore.ir import (
    Assign,
    Binary,
    Call,
    CallStmt,
    If,
    Indexed,
    Loop,
    Module,
    Paren,
    Quant,
    Return,
    Unary,
    Var,
    VarDecl,
    expr_names,
    stmt_calls,
    stmt_names,
)


# ---------------------------------------------------------------------------
# T7: consume-exactly-once as a core pass
# ---------------------------------------------------------------------------


class LinearPass:
    """T7: linearity inference + consume-exactly-once enforcement.

    `infer` computes, per function, the set of param NAMES that are linear
    (can reach a linear capability position through calls/moves). `validate`
    reports T7 problems on functions that HAVE linear params (copy / drop /
    double-use / loop-use, path-sensitively). `run` = validate (hard error on
    problems) + return the inferred map.
    """

    # ------------------------------------------------------------- inference

    def infer(self, module: Module) -> dict[str, set[str]]:
        """Per-fn set of linear param names (module-level fixpoint).

        A param of fn F is linear iff it can reach a linear consuming
        position: an argument to a callee parameter that is linear (extern:
        declared `linear:`; fn: already inferred), possibly through local
        moves (`var x = v` / `x = v`). Fixpoint over the module call graph.
        """
        fns = {f.name: f for f in module.fns}
        externs = {e.name: e for e in module.externs}
        fn_linear: dict[str, set[str]] = {f.name: set() for f in module.fns}
        changed = True
        while changed:
            changed = False
            for f in module.fns:
                reaches = set()
                for call in _fn_calls(f):
                    for i, arg in enumerate(call.args):
                        if isinstance(arg, Var) and _param_linear(externs, fns, fn_linear, call.callee, i):
                            reaches.add(arg.name)
                # backward through moves: if a local reaches a linear position,
                # its source does too (e.g. `var x = amount; ledger_debit(x)`)
                moves = _moves(f)
                moved = True
                while moved:
                    moved = False
                    for target, src in moves:
                        if target in reaches and src not in reaches:
                            reaches.add(src)
                            moved = True
                new_lin = {p.name for p in f.params if p.name in reaches}
                if new_lin != fn_linear[f.name]:
                    fn_linear[f.name] = new_lin
                    changed = True
        return fn_linear

    # ------------------------------------------------------------- T7 checks

    def validate(self, module: Module, fn_linear: dict | None = None) -> list[str]:
        """T7 problems: copy / drop / double-use / loop-use, path-sensitive.

        No `linear:` capability on any extern = the pass is a no-op (E1
        byte-parity untouched). EVERY fn is walked — the drop checks are gated
        on inferred linear params, but minting / copy-at-call-site checks at
        LINEAR extern positions must fire even in fns with no inferred linear
        param (a literal or non-linear local at a linear capability param is
        always a hard error; the extern is the only capability boundary).
        """
        problems: list[str] = []
        if fn_linear is None:
            fn_linear = self.infer(module)
        linear_pos = _linear_arg_positions(module, fn_linear)
        for f in module.fns:
            lin = fn_linear[f.name]
            entry = {p: frozenset((0,)) for p in lin}  # 0 = alive, 1 = consumed
            end_states = self._walk_block(f, f.body.stmts, entry, linear_pos, problems)
            for s in end_states:
                for p in lin:
                    if 0 in s[p]:
                        problems.append(
                            f"{f.name}: linear value {p} is dropped — never "
                            "consumed on some path (T7)"
                        )
        return problems

    def run(self, module: Module) -> dict[str, set[str]]:
        fn_linear = self.infer(module)
        problems = self.validate(module, fn_linear)
        if problems:
            from wardcore.elaborator import ElaborationError

            raise ElaborationError(
                "; ".join(problems[:5]) + (" ..." if len(problems) > 5 else "")
            )
        return fn_linear

    # ------------------------------------------------------------- walkers

    def _walk_block(self, f, stmts, state, linear_pos, problems) -> list[dict]:
        """Walk a statement list; returns per-path end states.

        A state maps each linear name in scope to its possible consumption
        counts across the paths reaching this point ({0} alive, {1} consumed,
        {0,1} alive on some paths / consumed on others). Names introduced by
        moves inside this block must be consumed by block end (they are
        block-scoped) and are removed before returning.
        """
        states = [dict(state)]
        introduced: set[str] = set()
        for st in stmts:
            nxt: list[dict] = []
            for s in states:
                nxt.extend(self._walk_stmt(f, st, s, linear_pos, problems, introduced))
            states = nxt
        out: list[dict] = []
        for s in states:
            for name in introduced:
                if 0 in s.get(name, frozenset((1,))):
                    problems.append(
                        f"{f.name}: linear value {name} is dropped — never "
                        "consumed before block end (T7)"
                    )
                s.pop(name, None)
            out.append(s)
        return out

    def _walk_stmt(self, f, st, state, linear_pos, problems, introduced) -> list[dict]:
        if isinstance(st, VarDecl):
            return [self._walk_vardecl(f, st, state, linear_pos, problems, introduced)]
        if isinstance(st, Assign):
            return [self._walk_assign(f, st, state, linear_pos, problems, introduced)]
        if isinstance(st, If):
            return self._walk_if(f, st, state, linear_pos, problems)
        if isinstance(st, Loop):
            return [self._walk_loop(f, st, state, linear_pos, problems)]
        if isinstance(st, Return):
            return self._walk_return(f, st, state, linear_pos, problems)
        if isinstance(st, CallStmt):
            self._scan_expr(f, st.call, state, linear_pos, problems)
            return [state]
        return [state]

    def _walk_vardecl(self, f, st, state, linear_pos, problems, introduced) -> dict:
        s = dict(state)
        if isinstance(st.value, Var) and st.value.name in s:
            # move: consuming the source; the target becomes the linear holder
            self._consume(f, st.value.name, s, problems)
            s[st.name] = frozenset((0,))
            introduced.add(st.name)
        else:
            self._scan_expr(f, st.value, s, linear_pos, problems)
        return s

    def _walk_assign(self, f, st, state, linear_pos, problems, introduced) -> dict:
        s = dict(state)
        if st.target in s:
            # overwriting a linear slot drops its current value (T7)
            problems.append(
                f"{f.name}: linear value {st.target} is overwritten — "
                "would drop its current value (T7)"
            )
        if st.op == "=" and isinstance(st.value, Var) and st.value.name in s:
            self._consume(f, st.value.name, s, problems)
            s[st.target] = frozenset((0,))
            introduced.add(st.target)
        else:
            self._scan_expr(f, st.value, s, linear_pos, problems)
        return s

    def _walk_if(self, f, st, state, linear_pos, problems) -> list[dict]:
        s = dict(state)
        # the condition is a non-consuming position: any linear mention = copy
        self._scan_expr(f, st.cond, s, linear_pos, problems)
        then_states = self._walk_block(f, st.then_branch.stmts, s, linear_pos, problems)
        if st.else_branch is not None:
            else_states = self._walk_block(f, st.else_branch.stmts, s, linear_pos, problems)
        else:
            else_states = [dict(s)]
        merged: list[dict] = []
        if then_states and else_states:
            for t in then_states:
                for e in else_states:
                    m = {
                        name: t.get(name, frozenset()) | e.get(name, frozenset())
                        for name in set(t) | set(e)
                    }
                    merged.append(m)
        else:
            # one side returned on every path — its continuation is the other
            # side's states (a cross-product with an empty list would silently
            # drop the fall-through path, missing double-use / path-split drop)
            merged = then_states or else_states
        return merged

    def _walk_loop(self, f, st, state, linear_pos, problems) -> dict:
        s = dict(state)
        names: set[str] = set(expr_names(st.lo)) | set(expr_names(st.hi))
        for inv in st.invariants:
            names |= set(expr_names(inv))
        for body_st in st.body.stmts:
            names |= set(stmt_names(body_st))
        for n in sorted(names):
            if n in s:
                problems.append(
                    f"{f.name}: linear value {n} used inside a loop — "
                    "consumption count across iterations is not trackable in "
                    "v0.1 (T7)"
                )
        return s

    def _walk_return(self, f, st, state, linear_pos, problems) -> list[dict]:
        s = dict(state)
        if st.value is not None:
            if isinstance(st.value, Var) and st.value.name in s:
                self._consume(f, st.value.name, s, problems)
            else:
                self._scan_expr(f, st.value, s, linear_pos, problems)
        # the return moves only one value out: everything still alive is dropped
        for name in sorted(s):
            if 0 in s[name]:
                problems.append(
                    f"{f.name}: linear value {name} is dropped — never "
                    "consumed on some path (T7)"
                )
        return []  # path terminates here

    def _consume(self, f, name: str, s: dict, problems) -> None:
        poss = s.get(name, frozenset())
        if 1 in poss:
            problems.append(
                f"{f.name}: linear value {name} is consumed more than once "
                "on a path (T7)"
            )
        s[name] = frozenset((1,))

    def _scan_expr(self, f, e, s: dict, linear_pos, problems) -> None:
        """Scan an expression for linear-value mentions.

        Call arguments at linear callee positions are CONSUMING (bare Var) or
        hard errors (expression / non-linear value); every other mention of a
        linear value is a COPY (hard error).
        """
        if isinstance(e, Var):
            if e.name in s:
                problems.append(
                    f"{f.name}: linear value {e.name} is copied — used in a "
                    "non-consuming position (T7)"
                )
            return
        if isinstance(e, Call):
            pos = linear_pos.get(e.callee, ())
            for i, arg in enumerate(e.args):
                if i in pos:
                    if isinstance(arg, Var) and arg.name in s:
                        self._consume(f, arg.name, s, problems)
                    elif isinstance(arg, Var):
                        problems.append(
                            f"{f.name}: passing a non-linear value "
                            f"{arg.name} to a linear parameter of "
                            f"{e.callee} (T7)"
                        )
                    else:
                        problems.append(
                            f"{f.name}: cannot consume a non-linear "
                            f"expression into a linear parameter of "
                            f"{e.callee} (T7)"
                        )
                else:
                    self._scan_expr(f, arg, s, linear_pos, problems)
            return
        if isinstance(e, Unary):
            self._scan_expr(f, e.operand, s, linear_pos, problems)
            return
        if isinstance(e, Binary):
            self._scan_expr(f, e.left, s, linear_pos, problems)
            self._scan_expr(f, e.right, s, linear_pos, problems)
            return
        if isinstance(e, Indexed):
            if e.base in s:
                problems.append(
                    f"{f.name}: linear value {e.base} is copied — used in a "
                    "non-consuming position (T7)"
                )
            self._scan_expr(f, e.index, s, linear_pos, problems)
            return
        if isinstance(e, Quant):
            self._scan_expr(f, e.lo, s, linear_pos, problems)
            self._scan_expr(f, e.hi, s, linear_pos, problems)
            self._scan_quant_body(f, e, s, linear_pos, problems)
            return
        if isinstance(e, Paren):
            self._scan_expr(f, e.inner, s, linear_pos, problems)
            return
        # literals (IntLit/BoolLit/StrLit/UnitLit) mention nothing

    def _scan_quant_body(self, f, q, s: dict, linear_pos, problems) -> None:
        """Scan a quantifier body, ignoring the bound variable itself."""

        def walk(e) -> None:
            if isinstance(e, Var):
                if e.name != q.var and e.name in s:
                    problems.append(
                        f"{f.name}: linear value {e.name} is copied — used "
                        "in a non-consuming position (T7)"
                    )
                return
            if isinstance(e, Call):
                pos = linear_pos.get(e.callee, ())
                for i, arg in enumerate(e.args):
                    if i in pos and isinstance(arg, Var) and arg.name in s:
                        self._consume(f, arg.name, s, problems)
                    else:
                        walk(arg)
                return
            if isinstance(e, Unary):
                walk(e.operand)
            elif isinstance(e, Binary):
                walk(e.left)
                walk(e.right)
            elif isinstance(e, Indexed):
                if e.base in s and e.base != q.var:
                    problems.append(
                        f"{f.name}: linear value {e.base} is copied — used "
                        "in a non-consuming position (T7)"
                    )
                walk(e.index)
            elif isinstance(e, Paren):
                walk(e.inner)
            elif isinstance(e, Quant):
                walk(e.lo)
                walk(e.hi)
                walk(e.body)

        walk(q.body)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _param_linear(externs: dict, fns: dict, fn_linear: dict, callee: str, i: int) -> bool:
    """Is the callee's parameter at index i linear?"""
    if callee in externs:
        e = externs[callee]
        return i < len(e.params) and e.params[i].linear
    if callee in fns:
        f = fns[callee]
        return i < len(f.params) and f.params[i].name in fn_linear[callee]
    return False


def _linear_arg_positions(module: Module, fn_linear: dict) -> dict[str, frozenset]:
    """callee -> arg indices whose parameters are linear (extern + inferred fn)."""
    out: dict[str, frozenset] = {}
    for e in module.externs:
        idx = frozenset(i for i, p in enumerate(e.params) if p.linear)
        if idx:
            out[e.name] = idx
    for f in module.fns:
        idx = frozenset(i for i, p in enumerate(f.params) if p.name in fn_linear[f.name])
        if idx:
            out[f.name] = idx
    return out


def _fn_calls(f) -> list:
    """Every Call node in a function body (extern + fn callees, nested blocks)."""
    calls = []
    for st in f.body.stmts:
        calls.extend(stmt_calls(st))
    return calls


def _moves(f) -> list[tuple[str, str]]:
    """(target, source) pairs for every `var x = v` / `x = v` with a bare Var RHS."""

    out: list[tuple[str, str]] = []

    def walk(sts) -> None:
        for st in sts:
            if isinstance(st, VarDecl) and isinstance(st.value, Var):
                out.append((st.name, st.value.name))
            elif isinstance(st, Assign) and st.op == "=" and isinstance(st.value, Var):
                out.append((st.target, st.value.name))
            elif isinstance(st, If):
                walk(st.then_branch.stmts)
                if st.else_branch is not None:
                    walk(st.else_branch.stmts)
            elif isinstance(st, Loop):
                walk(st.body.stmts)

    walk(f.body.stmts)
    return out
