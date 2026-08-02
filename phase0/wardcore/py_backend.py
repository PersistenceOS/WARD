"""py_backend — the first multi-target backend slice (README roadmap row 4).

Ward's core calculus compiles to existing host runtimes rather than inventing a
parallel ecosystem (design doc §4d, mirroring Dafny's own multi-target
compiler). This module emits **Python** from the ward-core IR Module — the
simplest real backend, gated by FUNCTIONAL PARITY (E11): the hidden tests run
against the emitted Python must produce the same pass set / per-case markers as
the Dafny path on the same sources.

Emission contract (each rule mirrors the Dafny emitter's so the two backends
agree behaviorally):

- **Types:** int/bool/str -> native; Unit -> `None`; `Result<T,E>` -> a small
  `Result` class with `Ok`/`Err` constructors, `is_ok`/`is_err` flags and
  `value`/`error` fields (+ `__eq__` so hidden-test comparisons work);
  `List<T>` -> Python `list`.
- **Externs are runtime-contract-checked stubs** (the user's requirement): the
  raw stub (`<name>_stub`, from the task's `impl` source) is wrapped by
  `<name>` which calls the stub, converts its `("ok"|"err", v)` tuple to a
  `Result`, and — exactly like the Dafny `_checked` wrapper — returns
  `Err("contract violation")` when the stub's output violates the extern's
  ward0 `ensures`. Call sites in the IR are routed through this checked
  wrapper (T4: no direct stub call survives the enforced pipeline).
- **Functions:** `def name(params):` with the body translated statement by
  statement. `requires` clauses compile to `assert`s for Proven/Contracted
  functions ("verified code ships with its input checks") and to comments for
  Tested functions (T6: no proof obligation, ships unchecked). `ensures`
  clauses compile to trailing comments (proof-time only — matching Dafny's
  compiled output, which does not check postconditions at runtime).
- **Statements:** var_decl -> `name = expr`; assign -> `name op expr` with
  `/=` mapped to `//=`; if/else -> Python if/else; `for i in range(lo, hi)`
  (T8 loop) -> the identical Python loop with invariant lines as comments;
  return -> `return expr`; call_stmt -> bare call.
- **Expressions:** arithmetic/cmp/`and`/`or` map 1:1; `/` -> `//` (ward0 int
  division) and `%` -> Python `%` — both agree with Dafny on non-negative
  operands (the entire E11 corpus; negative operands are out of scope, the
  same honesty boundary the z3 backend declares); `len(x)` -> `len(x)`;
  `is_ok`/`is_err`/`unwrap_ok`/`unwrap_err`
  -> `x.is_ok`/`x.is_err`/`x.value`/`x.error`; `Ok(v)`/`Err(v)` -> the
  constructors; quantifiers (`forall/exists i in range(lo,hi)`) -> `all(...)`
  / `any(...)` generator expressions; calls -> `name(args)`.

Dependencies: stdlib only. No lark, no Dafny, no z3 — this is the *target
language* side of the compiler, not a checker.
"""

from __future__ import annotations

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
    Quant,
    Return,
    StrLit,
    Tier,
    Type,
    Unary,
    UnitLit,
    Var,
    VarDecl,
)

INDENT = "    "

# ward0 operators -> Python (int division must become floor division)
_BINOP = {
    "+": " + ", "-": " - ", "*": " * ", "/": " // ", "%": " % ",
    "==": " == ", "!=": " != ", "<": " < ", "<=": " <= ",
    ">": " > ", ">=": " >= ", "and": " and ", "or": " or ",
}
_COMPOUND = {"=": "=", "+=": "+=", "-=": "-=", "*=": "*=",
             "/=": "//=", "%=": "%="}

_RESULT_PREAMBLE = '''class Result:
    """ward-core Result<T,E> — a disjoint sum with runtime equality."""

    __slots__ = ("is_ok", "value", "error")

    def __init__(self, ok, value=None, error=None):
        self.is_ok = ok
        self.value = value
        self.error = error

    @property
    def is_err(self):
        return not self.is_ok

    def __eq__(self, other):
        return (
            isinstance(other, Result)
            and self.is_ok == other.is_ok
            and self.value == other.value
            and self.error == other.error
        )

    def __repr__(self):
        return f"Ok({self.value!r})" if self.is_ok else f"Err({self.error!r})"


def Ok(value):
    return Result(True, value=value)


def Err(error):
    return Result(False, error=error)
'''


def _uses_result(module: Module) -> bool:
    """Does the module mention Result anywhere? (gates the preamble).

    Scans type annotations AND every expression in bodies/contracts so a
    Result that appears only in a VarDecl type or a contract term still
    brings the preamble (the emitted code would otherwise NameError)."""
    def uses(t: Type) -> bool:
        return t.name == "Result" or any(uses(a) for a in t.args)

    def expr_uses(e) -> bool:
        from wardcore.ir import (
            Binary, Call, Indexed, Paren, Quant, Unary,
        )
        if isinstance(e, Call):
            if e.callee in ("Ok", "Err", "is_ok", "is_err", "unwrap_ok", "unwrap_err"):
                return True
            return any(expr_uses(a) for a in e.args)
        if isinstance(e, Paren):
            return expr_uses(e.inner)
        if isinstance(e, Unary):
            return expr_uses(e.operand)
        if isinstance(e, Binary):
            return expr_uses(e.left) or expr_uses(e.right)
        if isinstance(e, Indexed):
            return expr_uses(e.index)
        if isinstance(e, Quant):
            return expr_uses(e.lo) or expr_uses(e.hi) or expr_uses(e.body)
        return False

    def stmt_uses(s) -> bool:
        from wardcore.ir import (
            Assign, CallStmt, If, Loop, Return, VarDecl,
        )
        if isinstance(s, VarDecl):
            return uses(s.type) or expr_uses(s.value)
        if isinstance(s, Assign):
            return expr_uses(s.value)
        if isinstance(s, If):
            return expr_uses(s.cond) or block_uses(s.then_branch) or (
                s.else_branch is not None and block_uses(s.else_branch)
            )
        if isinstance(s, Loop):
            return expr_uses(s.lo) or expr_uses(s.hi) or block_uses(s.body)
        if isinstance(s, Return):
            return s.value is not None and expr_uses(s.value)
        if isinstance(s, CallStmt):
            return expr_uses(s.call)
        return False

    def block_uses(b) -> bool:
        return any(stmt_uses(s) for s in b.stmts)

    for e in module.externs:
        if uses(e.ret) or any(uses(p.type) for p in e.params):
            return True
        if any(expr_uses(c.expr) for c in e.ensures) or any(expr_uses(c.expr) for c in e.requires):
            return True
    for f in module.fns:
        if uses(f.ret) or any(uses(p.type) for p in f.params):
            return True
        if any(expr_uses(c.expr) for c in f.ensures) or any(expr_uses(c.expr) for c in f.requires):
            return True
        if block_uses(f.body):
            return True
    return False


class PyEmitter:
    """ward-core IR Module -> Python source (the first non-Dafny backend)."""

    def __init__(self, enforce_boundary: bool = True):
        self.enforce_boundary = enforce_boundary

    # ------------------------------------------------------------- entry

    def emit(self, module: Module, extern_impls: dict[str, str] | None = None) -> str:
        """Emit Python for a ward-core Module.

        `extern_impls` maps extern name -> the stub Python source from the task
        descriptor's `impl` field (a `def <name>_stub(...):` returning
        `("ok", v)` / `("err", s)`). Without it, stubs raise
        NotImplementedError (used when only emission shape matters).
        """
        impls = extern_impls or {}
        out = ["# ward-core -> Python (py_backend, E11 parity backend)"]
        if _uses_result(module):
            out.append(_RESULT_PREAMBLE)
        for e in module.externs:
            out.append(self._extern_python(e, impls.get(e.name)))
        for fn in module.fns:
            out.append(self._fn_python(fn))
        return "\n\n".join(out) + "\n"

    # ------------------------------------------------------------- externs

    def _extern_python(self, e, impl: str | None) -> str:
        params = ", ".join(p.name for p in e.params)
        args = ", ".join(p.name for p in e.params)
        if impl:
            stub = impl.rstrip()
        else:
            stub = (
                f"def {e.name}_stub({params}):\n"
                f"{INDENT}raise NotImplementedError('{e.name} stub not supplied')\n"
            )
        lines = [stub, ""]
        lines.append(f"def {e.name}({params}):")
        lines.append(f"{INDENT}out = {e.name}_stub({args})")
        lines.append(f"{INDENT}r = Ok(out[1]) if out[0] == 'ok' else Err(out[1])")
        # enforce_boundary: the runtime contract check (mirrors the Dafny
        # `_checked` wrapper — contradiction -> Err("contract violation")).
        # Off: pass-through (mirrors the no-enforce arm: direct stub result).
        if self.enforce_boundary:
            checks = [self._expr_py(c.expr, subst={"result": "r"}) for c in e.ensures]
            if checks:
                joined = " and ".join(f"({c})" for c in checks)
                lines.append(f"{INDENT}if not ({joined}):")
                lines.append(f'{INDENT}{INDENT}return Err("contract violation")')
        lines.append(f"{INDENT}return r")
        return "\n".join(lines)

    # ------------------------------------------------------------- functions

    def _fn_python(self, fn) -> str:
        params = ", ".join(p.name for p in fn.params)
        lines = [f"def {fn.name}({params}):"]
        # T6: Proven/Contracted ship with their input checks; Tested ships
        # unchecked (no proof obligation, no runtime assert)
        if fn.tier is not Tier.TESTED:
            for c in fn.requires:
                lines.append(f"{INDENT}assert {self._expr_py(c.expr)}")
        if fn.ensures:
            lines.append(
                f"{INDENT}# ensures: "
                + " and ".join(self._expr_py(c.expr) for c in fn.ensures)
            )
        body = self._block_python(fn.body, 1)
        if not body:
            lines.append(f"{INDENT}return None")
        else:
            lines += body
        return "\n".join(lines)

    # ------------------------------------------------------------- blocks / stmts

    def _block_python(self, block: Block, indent: int) -> list[str]:
        lines = []
        for st in block.stmts:
            lines += self._stmt_python(st, indent)
        return lines

    def _stmt_python(self, st, indent: int) -> list[str]:
        pad = INDENT * indent
        if isinstance(st, VarDecl):
            return [f"{pad}{st.name} = {self._expr_py(st.value)}"]
        if isinstance(st, Assign):
            return [f"{pad}{st.target} {_COMPOUND[st.op]} {self._expr_py(st.value)}"]
        if isinstance(st, If):
            lines = [f"{pad}if {self._expr_py(st.cond)}:"]
            lines += self._block_python(st.then_branch, indent + 1)
            if st.else_branch is not None:
                lines.append(f"{pad}else:")
                lines += self._block_python(st.else_branch, indent + 1)
            return lines
        if isinstance(st, Loop):
            lines = [f"{pad}for {st.var} in range({self._expr_py(st.lo)}, {self._expr_py(st.hi)}):"]
            for inv in st.invariants:
                lines.append(f"{pad}{INDENT}# invariant: {self._expr_py(inv)}")
            lines += self._block_python(st.body, indent + 1)
            return lines
        if isinstance(st, Return):
            if st.value is not None:
                return [f"{pad}return {self._expr_py(st.value)}"]
            return [f"{pad}return None"]
        if isinstance(st, CallStmt):
            return [f"{pad}{self._expr_py(st.call)}"]
        raise NotImplementedError(f"unsupported statement for Python backend: {st!r}")

    # ------------------------------------------------------------- expressions

    def _expr_py(self, e, subst: dict[str, str] | None = None) -> str:
        if isinstance(e, IntLit):
            return str(e.value)
        if isinstance(e, BoolLit):
            return "True" if e.value else "False"
        if isinstance(e, StrLit):
            return repr(e.value)
        if isinstance(e, UnitLit):
            return "None"
        if isinstance(e, Var):
            if subst and e.name in subst:
                return subst[e.name]
            return e.name
        if isinstance(e, Paren):
            return f"({self._expr_py(e.inner, subst)})"
        if isinstance(e, Unary):
            inner = f"({self._expr_py(e.operand, subst)})"
            return f"not {inner}" if e.op == "not" else f"-{inner}"
        if isinstance(e, Binary):
            l = f"({self._expr_py(e.left, subst)})"
            r = f"({self._expr_py(e.right, subst)})"
            return l + _BINOP[e.op] + r
        if isinstance(e, Indexed):
            return f"{e.base}[{self._expr_py(e.index, subst)}]"
        if isinstance(e, Quant):
            var = e.var
            gen = f"({self._expr_py(e.body, subst)}) for {var} in range({self._expr_py(e.lo, subst)}, {self._expr_py(e.hi, subst)})"
            return f"all({gen})" if e.kw == "forall" else f"any({gen})"
        if isinstance(e, Call):
            args = ", ".join(self._expr_py(a, subst) for a in e.args)
            if e.callee == "len":
                return f"len({args})"
            if e.callee == "is_ok":
                return f"{args}.is_ok"
            if e.callee == "is_err":
                return f"{args}.is_err"
            if e.callee == "unwrap_ok":
                return f"{args}.value"
            if e.callee == "unwrap_err":
                return f"{args}.error"
            if e.callee in ("Ok", "Err"):
                return f"{e.callee}({args})"
            return f"{e.callee}({args})"
        raise NotImplementedError(f"unsupported expression for Python backend: {e!r}")
