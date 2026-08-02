"""z3_backend — the first slice of the '3+' roadmap row (standalone SMT checker).

Ward standing alone: this module verifies ward-core IR Modules by emitting
verification conditions DIRECTLY to Z3 — no Dafny, no `dafny` CLI, no harness,
no model in the loop. The borrowed proving engine is still an SMT solver (Z3,
the very solver Dafny bundles), but the pipeline that produces the VCs and
consumes the answers is entirely Ward's own:

    ward-core IR -> VC (path-walk symbolic execution) -> z3 -> verdict / cex

Scope (pre-registered as gate E10, first slice of the '3+' roadmap row; the
multi-target backends + composition-first library rows remain future work):

- **Types** map 1:1 onto z3 sorts: int/bool/str -> Int/Bool/String, Unit and
  Result<T,E> -> z3 datatypes (cached per signature), List<T> -> z3 seqs.
- **Externs are axiom methods** — exactly the way Dafny treats
  `{:extern}{:axiom}`: a call site must PROVE the extern's requires (a VC
  obligation) and may ASSUME its ensures (conjoined to the path condition).
  The `_checked` wrapper is a runtime artifact; the verification view of an
  extern is its contract. Function calls are modular in the same way (the
  callee is verified separately, so the caller assumes its contract).
- **Path-walk symbolic execution** over var/assign/if/return. Every `return v`
  emits the obligation `pc => ensures[result := v]`; every extern call emits
  `pc => requires(args)` and conjoins `ensures(args, result)` to pc. Paths that
  fall off the end of a non-Unit fn emit `pc => false` (missing return -> the
  verifier fails it honestly, matching Dafny).
- **Loops** (IR `Loop`, T8): with invariants -> the base/inductive/exit rule;
  without invariants but constant bounds -> unroll (cap 100); otherwise a
  NotProved marker is raised and recorded honestly (never a silent pass).
- **Effort**: per-function solver seconds measured around the checks, reported
  in the R7 EffortRecord schema (fn/tier/route/budget_s/verify_s/outcome).

Design posture: this is a *verification backend*, not a proof certificate — it
re-checks the same obligations Dafny checks, by an independent route. Parity
with `dafny verify` on the same emitted source is a gate probe (E10-D), not an
assumption.

Dependencies: z3-solver only (the project venv already carries it). No lark,
no transpiler, no dafny.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import z3

from wardcore.ir import (
    Assign,
    Binary,
    Block,
    BoolLit,
    Call,
    CallStmt,
    Contract,
    Indexed,
    IntLit,
    If,
    Loop,
    Module,
    Paren,
    Param,
    Quant,
    Return,
    StrLit,
    TUnit,
    Tier,
    Type,
    Unary,
    UnitLit,
    Var,
    VarDecl,
)
from wardcore.tier_pass import CONTRACTED_VERIFY_LIMIT_S, TierPass

UNROLL_CAP = 100  # constant-bound loops without invariants: unroll up to here

_AND_OR = {"and": z3.And, "or": z3.Or}
_CMP = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
        "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
# z3py on IntRefs: `/` and `%` are overloaded to Div/Mod with Int result —
# the `z3.Div`/`z3.Mod` names are NOT exported by the installed z3-solver.
_ARITH = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "*": lambda a, b: a * b,
          "/": lambda a, b: a / b, "%": lambda a, b: a % b}
_COMPOUND = {"=": lambda cur, v: v, "+=": lambda cur, v: cur + v, "-=": lambda cur, v: cur - v,
             "*=": lambda cur, v: cur * v, "/=": lambda cur, v: cur / v, "%=": lambda cur, v: cur % v}


class NotProved(Exception):
    """The verifier cannot discharge this obligation in the v0.1 slice
    (e.g. a loop with symbolic bounds and no invariant). Never a silent pass —
    the caller records it as outcome `not_proved` with the reason."""


# ---------------------------------------------------------------------------
# z3 sort / datatype plumbing (cached per signature)
# ---------------------------------------------------------------------------


class _Sorts:
    """Owns all z3 sorts/datatypes for one module verification. Caches the
    Result datatypes by (ok, err) signature so repeated modules share them."""

    def __init__(self) -> None:
        self._result_cache: dict[tuple[Type, Type], z3.DatatypeSortRef] = {}
        self._dt_meta: dict[z3.SortRef, dict] = {}
        self._unit: z3.DatatypeSortRef | None = None
        self._unit_const: z3.ExprRef | None = None
        self._counter = 0

    def _fresh(self) -> int:
        self._counter += 1
        return self._counter

    def unit_sort(self) -> z3.DatatypeSortRef:
        if self._unit is None:
            u = z3.Datatype(f"WardUnit_{self._fresh()}")
            u.declare("U")
            self._unit = u.create()
            self._unit_const = self._unit.U  # the single inhabitant
        return self._unit

    def unit_const(self) -> z3.ExprRef:
        self.unit_sort()
        return self._unit_const

    def sort_of(self, t: Type) -> z3.SortRef:
        if t.name == "int":
            return z3.IntSort()
        if t.name == "bool":
            return z3.BoolSort()
        if t.name == "str":
            return z3.StringSort()
        if t.name == "Unit":
            return self.unit_sort()
        if t.name == "List":
            return z3.SeqSort(self.sort_of(t.args[0]))
        if t.name == "Result":
            ok_t, err_t = t.args
            key = (ok_t, err_t)
            if key not in self._result_cache:
                R = z3.Datatype(f"Result_{self._fresh()}")
                R.declare("Ok", ("value", self.sort_of(ok_t)))
                R.declare("Err", ("error", self.sort_of(err_t)))
                dt = R.create()
                self._result_cache[key] = dt
                self._dt_meta[dt] = {
                    "Ok": dt.Ok, "Err": dt.Err,
                    "is_Ok": dt.is_Ok, "is_Err": dt.is_Err,
                    "value": dt.value, "error": dt.error,
                }
            return self._result_cache[key]
        raise NotProved(f"no z3 sort for type {t.name!r}")

    def meta_for(self, sort: z3.SortRef) -> dict:
        meta = self._dt_meta.get(sort)
        if meta is None:
            raise NotProved(f"no Result datatype meta for sort {sort}")
        return meta


# ---------------------------------------------------------------------------
# VC construction state
# ---------------------------------------------------------------------------


@dataclass
class _Path:
    """One symbolic path: variable environment + path condition.

    `obligs` is the shared accumulator for VC obligations (pc => goal). The
    env maps IR variable names to z3 expressions; `result` is bound to the
    function's return constant wherever the contract references it.
    """

    env: dict[str, z3.ExprRef]
    pc: z3.BoolRef
    obligs: list[z3.BoolRef] = field(default_factory=list)

    def clone(self) -> "_Path":
        return _Path(dict(self.env), self.pc, self.obligs)


# ---------------------------------------------------------------------------
# The verifier
# ---------------------------------------------------------------------------


class Z3ModuleVerifier:
    """Verify every function of a ward-core Module directly with Z3.

    Usage:
        verifier = Z3ModuleVerifier(module, tier_plan)   # plan optional
        records = verifier.verify_all()                   # per-fn R7 records
    """

    def __init__(self, module: Module, tier_plan=None):
        self.module = module
        self.tier_plan = tier_plan or TierPass().plan(module)
        self.sorts = _Sorts()
        self._externs = {e.name: e for e in module.externs}
        self._fns = {f.name: f for f in module.fns}
        self._param_consts: dict[str, z3.ExprRef] = {}
        self._fresh_counter = 0

    # ------------------------------------------------------------- plumbing

    def _fresh(self, prefix: str, sort: z3.SortRef) -> z3.ExprRef:
        self._fresh_counter += 1
        return z3.Const(f"{prefix}__{self._fresh_counter}", sort)

    # ------------------------------------------------------------- expressions

    def _eval(self, e, env: dict[str, z3.ExprRef], expected: Type | None = None,
              obligs: list[z3.BoolRef] | None = None):
        """Evaluate an IR expression to a z3 expr.

        Returns (z3expr, obligations, assumptions): the obligations/assumptions
        come from any extern/fn call inside the expression. `expected` is the
        static type of the whole expression when known (used only to pick the
        Result datatype for the Ok/Err constructors).
        """
        obs: list[z3.BoolRef] = []
        ass: list[z3.BoolRef] = []
        if obligs is None:
            obligs = []

        def rec(expr, scope: dict[str, z3.ExprRef], exp: Type | None) -> z3.ExprRef:
            """Evaluate `expr` against `scope`. `scope` is threaded explicitly
            so an extern/fn contract can be evaluated under its own call-site
            environment (params -> args, result -> fresh const) without
            leaking into — or from — the caller's environment."""
            if isinstance(expr, IntLit):
                return z3.IntVal(expr.value)
            if isinstance(expr, BoolLit):
                return z3.BoolVal(expr.value)
            if isinstance(expr, StrLit):
                return z3.StringVal(expr.value)
            if isinstance(expr, UnitLit):
                return self.sorts.unit_const()
            if isinstance(expr, Var):
                if expr.name not in scope:
                    raise NotProved(f"free variable {expr.name!r} in expression")
                return scope[expr.name]
            if isinstance(expr, Paren):
                return rec(expr.inner, scope, exp)
            if isinstance(expr, Unary):
                v = rec(expr.operand, scope, exp)
                return z3.Not(v) if expr.op == "not" else (-v)
            if isinstance(expr, Binary):
                l = rec(expr.left, scope, exp)
                r = rec(expr.right, scope, exp)
                if expr.op in _AND_OR:
                    return _AND_OR[expr.op](l, r)
                if expr.op in _CMP:
                    return _CMP[expr.op](l, r)
                if expr.op in _ARITH:
                    return _ARITH[expr.op](l, r)
                raise NotProved(f"unsupported binary op {expr.op!r}")
            if isinstance(expr, Indexed):
                seq = scope.get(expr.base)
                if seq is None:
                    raise NotProved(f"indexed base {expr.base!r} not in scope")
                idx = rec(expr.index, scope, None)
                return z3.Select(seq, idx)
            if isinstance(expr, Quant):
                iv = z3.Int(f"q{self._fresh_counter}"); self._fresh_counter += 1
                lo = rec(expr.lo, scope, None)
                hi = rec(expr.hi, scope, None)
                env2 = dict(scope); env2[expr.var] = iv
                body = rec(expr.body, env2, None)
                bound = z3.And(lo <= iv, iv < hi)
                if expr.kw == "forall":
                    return z3.ForAll([iv], z3.Implies(bound, body))
                return z3.Exists([iv], z3.And(bound, body))
            if isinstance(expr, Call):
                args = [rec(a, scope, None) for a in expr.args]
                if expr.callee in ("len",):
                    return z3.Length(args[0])
                if expr.callee in ("is_ok", "is_err", "unwrap_ok", "unwrap_err"):
                    meta = self.sorts.meta_for(args[0].sort())
                    key = {"is_ok": "is_Ok", "is_err": "is_Err",
                           "unwrap_ok": "value", "unwrap_err": "error"}[expr.callee]
                    return meta[key](args[0])
                if expr.callee in ("Ok", "Err"):
                    if expected is None or expected.name != "Result":
                        raise NotProved(
                            f"{expr.callee} constructor needs a Result type context"
                        )
                    meta = self.sorts.meta_for(self.sorts.sort_of(expected))
                    return meta[expr.callee](args[0])
                # extern / fn call: obligation requires, assumption ensures
                callee = self._externs.get(expr.callee) or self._fns.get(expr.callee)
                if callee is None:
                    raise NotProved(f"call to unknown callee {expr.callee!r}")
                ret_sort = self.sorts.sort_of(callee.ret)
                res = self._fresh(expr.callee, ret_sort)
                call_env = dict(scope)
                for p, a in zip(callee.params, args):
                    call_env[p.name] = a
                call_env["result"] = res
                for c in callee.requires:
                    obs.append(rec(c.expr, call_env, callee.ret))
                for c in callee.ensures:
                    ass.append(rec(c.expr, call_env, callee.ret))
                return res
            raise NotProved(f"cannot evaluate IR expression {expr!r}")

        z = rec(e, env, expected)
        return z, obs, ass

    # ------------------------------------------------------------- statements

    def _walk(self, block: Block, paths: list[_Path]) -> list[_Path]:
        """Apply every statement in the block to every path, forking as
        needed. Returns the surviving paths (those that fell off the end)."""
        for st in block.stmts:
            if not paths:
                break
            nxt: list[_Path] = []
            for p in paths:
                nxt += self._step(p, st)
            paths = nxt
        return paths

    def _step(self, p: _Path, st) -> list[_Path]:
        if isinstance(st, VarDecl):
            z, obs, ass = self._eval(st.value, p.env, expected=st.type)
            for o in obs:
                p.obligs.append(z3.Implies(p.pc, o))
            p2 = p.clone()
            p2.env[st.name] = z
            p2.pc = z3.And(p.pc, *ass) if ass else p.pc
            return [p2]

        if isinstance(st, Assign):
            cur = p.env.get(st.target)
            if cur is None:
                raise NotProved(f"assignment to undeclared {st.target!r}")
            z, obs, ass = self._eval(st.value, p.env, expected=self._scope_types.get(st.target))
            for o in obs:
                p.obligs.append(z3.Implies(p.pc, o))
            p2 = p.clone()
            p2.env[st.target] = _COMPOUND[st.op](cur, z)
            p2.pc = z3.And(p.pc, *ass) if ass else p.pc
            return [p2]

        if isinstance(st, If):
            c, obs, ass = self._eval(st.cond, p.env)
            for o in obs:
                p.obligs.append(z3.Implies(p.pc, o))
            base = z3.And(p.pc, *ass) if ass else p.pc
            then_p = _Path(dict(p.env), z3.And(base, c), p.obligs)
            else_p = _Path(dict(p.env), z3.And(base, z3.Not(c)), p.obligs)
            out = self._walk(st.then_branch, [then_p])
            if st.else_branch is not None:
                out += self._walk(st.else_branch, [else_p])
            else:
                out.append(else_p)
            return out

        if isinstance(st, Loop):
            return self._loop(p, st)

        if isinstance(st, Return):
            if st.value is not None:
                z, obs, ass = self._eval(st.value, p.env, expected=self._current_ret)
            else:
                z, obs, ass = self.sorts.unit_const(), [], []
            for o in obs:
                p.obligs.append(z3.Implies(p.pc, o))
            env2 = dict(p.env)
            env2["result"] = z
            goal = self._goal(self._ensures_exprs(), env2)
            p.obligs.append(z3.Implies(z3.And(p.pc, *ass) if ass else p.pc, goal))
            return []  # path terminates here

        if isinstance(st, CallStmt):
            _, obs, ass = self._eval(st.call, p.env)
            for o in obs:
                p.obligs.append(z3.Implies(p.pc, o))
            p2 = p.clone()
            p2.pc = z3.And(p.pc, *ass) if ass else p.pc
            return [p2]

        raise NotProved(f"unsupported statement {st!r}")

    def _loop(self, p: _Path, st: Loop) -> list[_Path]:
        """Base/inductive/exit rule for loops with invariants; unroll for
        constant bounds without invariants; otherwise NotProved (honest).

        Soundness: the exit path HAVOCS every body-assigned variable (fresh
        consts) and re-assumes the invariant at var = hi in the post-env —
        the post-loop values are never carried over from the pre-loop env, so
        the postcondition cannot be proved from a stale pre-loop value (the
        vacuous-verification trap)."""
        lo, lo_obs, lo_ass = self._eval(st.lo, p.env)
        hi, hi_obs, hi_ass = self._eval(st.hi, p.env)
        for o in lo_obs + hi_obs:
            p.obligs.append(z3.Implies(p.pc, o))
        entry = z3.And(p.pc, *lo_ass, *hi_ass) if (lo_ass or hi_ass) else p.pc
        assigned = _assigned_names(st.body)

        if st.invariants:
            iv = z3.Int(f"iv{self._fresh_counter}"); self._fresh_counter += 1
            # The inductive step must reason about SYMBOLIC state, not the
            # pre-loop concrete values: havoc every body-assigned variable to
            # a fresh const BEFORE computing I(i) and walking the body. With
            # pre-loop values the preservation obligation degrades to a
            # concrete constant (e.g. `4 >= 0`) and `acc = acc - 1` with
            # `invariant acc >= 0` would spuriously verify — the same
            # vacuous-verification trap as the exit, on the loop's premise.
            env_i = dict(p.env)
            for name in assigned:
                cur = env_i.get(name)
                if cur is not None:
                    env_i[name] = self._fresh(name, cur.sort())
            env_i[st.var] = iv
            inv_i = self._goal_exprs(st.invariants, env_i)
            env_lo = dict(p.env); env_lo[st.var] = lo
            # base: invariant holds at entry (var = lo)
            p.obligs.append(z3.Implies(entry, self._goal_exprs(st.invariants, env_lo)))
            # inductive: entry & lo <= i < hi & I(i) ==> body obligations, I(i+1)
            body_pc = z3.And(entry, lo <= iv, iv < hi, inv_i)
            fresh_obligs: list[z3.BoolRef] = []
            body_paths = self._walk(st.body, [_Path(dict(env_i), body_pc, fresh_obligs)])
            for o in fresh_obligs:
                p.obligs.append(z3.ForAll([iv], o))
            for bp in body_paths:
                # I(i+1) must hold in the POST-body env (assignments inside
                # the body updated bp.env) — never the pre-loop env
                env_next = dict(bp.env); env_next[st.var] = iv + 1
                inv_next = self._goal_exprs(st.invariants, env_next)
                p.obligs.append(z3.ForAll([iv], z3.Implies(bp.pc, inv_next)))
            # exit: havoc body-assigned vars, then assume I(hi) in the post-env
            post_env = dict(p.env)
            for name in assigned:
                cur = post_env.get(name)
                if cur is not None:
                    post_env[name] = self._fresh(name, cur.sort())
            post_env.pop(st.var, None)
            env_hi = dict(post_env); env_hi[st.var] = hi
            p2 = p.clone()
            p2.env = post_env
            p2.pc = z3.And(entry, self._goal_exprs(st.invariants, env_hi))
            return [p2]

        # no invariants: unroll only constant bounds, carrying ALL paths
        try:
            lo_v, hi_v = lo.as_long(), hi.as_long()
        except Exception:
            raise NotProved("loop with symbolic bounds and no invariant")
        if hi_v - lo_v > UNROLL_CAP or hi_v < lo_v:
            raise NotProved("loop bounds out of unroll cap / negative trip count")
        paths = [_Path(dict(p.env), entry, p.obligs)]
        for k in range(lo_v, hi_v):
            nxt: list[_Path] = []
            for pp in paths:
                pp.env[st.var] = z3.IntVal(k)
                nxt += self._walk(st.body, [_Path(dict(pp.env), pp.pc, p.obligs)])
            paths = nxt
            if not paths:
                break
        for pp in paths:
            pp.env.pop(st.var, None)
        return paths

    # ------------------------------------------------------------- contracts

    def _ensures_exprs(self) -> tuple[Contract, ...]:
        # bound per-function by the verify loop; stored here for _step access
        return self._current_ensures

    def _goal(self, contracts: tuple[Contract, ...], env: dict[str, z3.ExprRef]) -> z3.BoolRef:
        goals = []
        for c in contracts:
            z, _, _ = self._eval(c.expr, env)
            goals.append(z)
        if not goals:
            return z3.BoolVal(True)
        return z3.And(*goals) if len(goals) > 1 else goals[0]

    def _goal_exprs(self, exprs: tuple, env: dict[str, z3.ExprRef]) -> z3.BoolRef:
        """Conjunction of raw Expr terms (loop invariants are Expr, not
        Contract — T8 Loop.invariants carries expressions directly)."""
        goals = []
        for e in exprs:
            z, _, _ = self._eval(e, env)
            goals.append(z)
        if not goals:
            return z3.BoolVal(True)
        return z3.And(*goals) if len(goals) > 1 else goals[0]

    # ------------------------------------------------------------- entry point

    def verify_all(self) -> dict[str, dict]:
        records = {}
        for fn in self.module.fns:
            records[fn.name] = self.verify_fn(fn)
        return records

    def verify_fn(self, fn) -> dict:
        """Verify one function. Honors T6: Tested -> not_run, never reaches a
        solver. Proven/Contracted -> full/bounded effort, budgeted."""
        obl = self.tier_plan.for_fn(fn.name)
        if obl.route.value == "no_proof":
            return {
                "fn": fn.name, "tier": fn.tier.value, "route": obl.route.value,
                "budget_s": obl.verify_limit_s, "verify_s": 0.0,
                "outcome": "not_run", "counterexample": None,
            }
        budget_s = obl.verify_limit_s  # None for Proven, 30 for Contracted

        self._param_consts = {p.name: z3.Const(p.name, self.sorts.sort_of(p.type))
                              for p in fn.params}
        env = dict(self._param_consts)
        # static scope type map: params + every var_decl (incl. nested blocks)
        # — used only to pick the Result datatype for Ok/Err constructors
        self._scope_types: dict[str, Type] = {p.name: p.type for p in fn.params}
        for st in _all_stmts(fn.body):
            if isinstance(st, VarDecl):
                self._scope_types.setdefault(st.name, st.type)
        self._current_ret = fn.ret
        self._current_ensures = fn.ensures
        res = self._fresh("result", self.sorts.sort_of(fn.ret))
        env["result"] = res

        start = _Path(env, z3.BoolVal(True), [])
        # assume the function's requires as the initial path condition
        req = self._goal(fn.requires, env)
        start.pc = req

        t0 = time.monotonic()
        try:
            survived = self._walk(fn.body, [start])
        except NotProved as exc:
            return {
                "fn": fn.name, "tier": fn.tier.value, "route": obl.route.value,
                "budget_s": budget_s, "verify_s": round(time.monotonic() - t0, 3),
                "outcome": "not_proved", "counterexample": str(exc),
            }
        # fall-off-the-end paths: non-Unit = missing return (obligation false);
        # Unit = implicit `return;`
        for sp in survived:
            if fn.ret == TUnit():
                env2 = dict(sp.env); env2["result"] = self.sorts.unit_const()
                goal = self._goal(fn.ensures, env2)
                sp.obligs.append(z3.Implies(sp.pc, goal))
            else:
                sp.obligs.append(z3.Implies(sp.pc, z3.BoolVal(False)))

        cex = None
        for i, obl_formula in enumerate(start.obligs):
            s = z3.Solver()
            if budget_s is not None:
                remaining_ms = max(1, int((budget_s - (time.monotonic() - t0)) * 1000))
                s.set("timeout", remaining_ms)
            s.add(z3.Not(obl_formula))
            if s.check() == z3.sat:
                cex = self._counterexample(s)
                break

        outcome = "verified" if cex is None else "failed"
        return {
            "fn": fn.name, "tier": fn.tier.value, "route": obl.route.value,
            "budget_s": budget_s, "verify_s": round(time.monotonic() - t0, 3),
            "outcome": outcome, "counterexample": cex,
        }

    # ------------------------------------------------------------- counterexamples

    def _counterexample(self, solver: z3.Solver) -> dict:
        """Render a model of the violated obligation as param -> value."""
        m = solver.model()
        out: dict[str, object] = {}
        for name, c in self._param_consts.items():
            out[name] = self._render(m.eval(c))
        return out

    def _render(self, v: z3.ExprRef):
        if v is None:
            return None
        if z3.is_int_value(v):
            return int(v.as_long())
        if z3.is_bool(v):
            return bool(z3.is_true(v))
        if z3.is_string_value(v):
            return v.as_string()
        if z3.is_app(v):
            decl = v.decl().name()
            if decl == "Ok":
                return ("Ok", self._render(v.arg(0)))
            if decl == "Err":
                return ("Err", self._render(v.arg(0)))
            if decl == "U":
                return "()"
        return str(v)


def _all_stmts(block: Block) -> list:
    """Every statement in a block, depth-first across nested if/loop blocks
    (used to build the static scope type map for Ok/Err constructors)."""
    out: list = []
    for st in block.stmts:
        out.append(st)
        if isinstance(st, If):
            out += _all_stmts(st.then_branch)
            if st.else_branch is not None:
                out += _all_stmts(st.else_branch)
        elif isinstance(st, Loop):
            out += _all_stmts(st.body)
    return out


def _assigned_names(block: Block) -> set[str]:
    """Every variable ASSIGNED or declared inside a block (Assign targets +
    VarDecl names, incl. nested if/loop blocks). Used by the loop rule to
    havoc post-loop values: after a for-loop, body-assigned variables are
    fresh (arbitrary), constrained only by the invariant at var = hi — never
    carried over from the pre-loop environment (the vacuous-verification
    trap: proving the postcondition from a stale pre-loop value)."""
    names: set[str] = set()
    for st in _all_stmts(block):
        if isinstance(st, Assign):
            names.add(st.target)
        elif isinstance(st, VarDecl):
            names.add(st.name)
    return names
