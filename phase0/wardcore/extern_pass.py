"""ward-core extern pass (Phase-2 week 3) — R3/T3/T4 as a core transformation.

Pre-registered scope (files/ward-phase2-scoping.md §2 R3, §4 T3/T4, §6 E3):

- **T4 (routing):** every extern call site in every function body is rewritten
  to `Call(checked=True)`. After this pass no direct stub call survives, and
  `validate_module(check_t4=True)` proves it. (Phase-1 C2: 0 leaks enforce-on
  vs 1 leak enforce-off on w3 — the rule is now structural, not transpiler
  convention.)
- **T3 (trust):** externs require a contract (enforced by type-check since
  week 2) AND a `trust:` annotation — the week-2 type-check deferred the trust
  half exactly as it deferred T4; *this pass is the enforcement point*
  (`validate_module(check_t3_trust=True)`).
- **R3 (core-level, not model convention):** the routing decision lives here as
  an IR-to-IR rewrite; the emitter only renders `_checked` for `checked=True`
  calls and emits the wrapper methods when `enforce` is on.

The pass runs inside `Elaborator.transpile` (and `elaborate`): desugar →
type_check (structural) → ExternPass.run (rewrite + validate) → emit. With
`enforce=False` (the W-enforce measurement arm) no rewrite happens and T4 is
not checked — direct stub calls are legal by design there — but T3-trust still
applies to the extern declarations (an extern without `trust:` is a hard
elaboration error in both arms).

E3 gate (scoping doc §6): contract-less extern = hard error (probe), direct
stub call = hard error (probe), 0 boundary_okleak on enforce-on runs — the
gate runner lives in wardcore/e3_gate.py.
"""

from __future__ import annotations

from dataclasses import replace

from wardcore.ir import (
    Assign,
    Binary,
    Block,
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
    VarDecl,
    validate_module,
)


class ExternPass:
    """The extern-call rule as a first-class core pass (R3/T3/T4)."""

    def __init__(self, enforce: bool = True):
        self.enforce = enforce

    # ------------------------------------------------------------- T4 rewrite

    def rewrite(self, module: Module) -> Module:
        """Return a new module with every extern call site marked checked=True.

        With enforce=False the module is returned unchanged (the W-enforce
        measurement arm keeps direct stub calls by design). The rewrite is a
        pure IR-to-IR transformation (frozen IR, dataclasses.replace) so it is
        auditable and re-runnable: rewrite(rewrite(m)) == rewrite(m).
        """
        if not self.enforce:
            return module
        extern_names = {e.name for e in module.externs}
        fns = tuple(replace(fn, body=_rewrite_block(fn.body, extern_names)) for fn in module.fns)
        return replace(module, fns=fns)

    # ------------------------------------------------------- T3/T4 validation

    def validate(self, module: Module) -> list[str]:
        """T3 (contract mandatory + trust mandatory) + T4 (routing) problems.

        T4 is checked only under enforce (the W-enforce arm deliberately keeps
        direct stub calls). T3-trust is checked in both arms.
        """
        # T5 deferred (check_t5=False): the week-4 effects pass (EffectsPass)
        # is the authoritative T5 in the pipeline — escape + unused, all five
        # kinds, transitively through fn calls. The IR-level check here only
        # sees direct extern calls and net/db/fs, so running it would both
        # duplicate and pre-empt the core pass.
        return validate_module(module, check_t4=self.enforce, check_t3_trust=True, check_t5=False)

    def run(self, module: Module) -> Module:
        """Rewrite (T4) then validate (T3/T4); raise on any problem.

        Returns the rewritten module (== the input module when enforce=False).
        """
        rewritten = self.rewrite(module)
        problems = self.validate(rewritten)
        if problems:
            from wardcore.elaborator import ElaborationError

            raise ElaborationError(
                "; ".join(problems[:5]) + (" ..." if len(problems) > 5 else "")
            )
        return rewritten


# ---------------------------------------------------------------------------
# IR rewriters (frozen dataclasses -> dataclasses.replace)
# ---------------------------------------------------------------------------


def _rewrite_expr(e, extern_names: set[str]):
    if isinstance(e, Call):
        args = tuple(_rewrite_expr(a, extern_names) for a in e.args)
        if e.callee in extern_names and not e.checked:
            return replace(e, args=args, checked=True)
        return replace(e, args=args)
    if isinstance(e, Unary):
        return replace(e, operand=_rewrite_expr(e.operand, extern_names))
    if isinstance(e, Indexed):
        return replace(e, index=_rewrite_expr(e.index, extern_names))
    if isinstance(e, Quant):
        return replace(
            e,
            lo=_rewrite_expr(e.lo, extern_names),
            hi=_rewrite_expr(e.hi, extern_names),
            body=_rewrite_expr(e.body, extern_names),
        )
    if isinstance(e, Binary):
        return replace(
            e,
            left=_rewrite_expr(e.left, extern_names),
            right=_rewrite_expr(e.right, extern_names),
        )
    # literals, Var, UnitLit are terminal
    return e


def _rewrite_stmt(s, extern_names: set[str]):
    if isinstance(s, VarDecl):
        return replace(s, value=_rewrite_expr(s.value, extern_names))
    if isinstance(s, Assign):
        return replace(s, value=_rewrite_expr(s.value, extern_names))
    if isinstance(s, If):
        return replace(
            s,
            cond=_rewrite_expr(s.cond, extern_names),
            then_branch=_rewrite_block(s.then_branch, extern_names),
            else_branch=_rewrite_block(s.else_branch, extern_names) if s.else_branch is not None else None,
        )
    if isinstance(s, Loop):
        return replace(
            s,
            lo=_rewrite_expr(s.lo, extern_names),
            hi=_rewrite_expr(s.hi, extern_names),
            invariants=tuple(_rewrite_expr(i, extern_names) for i in s.invariants),
            body=_rewrite_block(s.body, extern_names),
        )
    if isinstance(s, Return):
        if s.value is not None:
            return replace(s, value=_rewrite_expr(s.value, extern_names))
        return s
    if isinstance(s, CallStmt):
        return replace(s, call=_rewrite_expr(s.call, extern_names))
    return s


def _rewrite_block(block: Block, extern_names: set[str]) -> Block:
    return replace(block, stmts=tuple(_rewrite_stmt(st, extern_names) for st in block.stmts))
