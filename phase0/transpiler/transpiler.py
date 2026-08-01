"""ward0 -> Dafny 4.x transpiler (Ward Phase 0).

Deterministic, syntax-directed translation. Hard rule: anything that cannot be
translated unambiguously raises TranspileError — the program never reaches Dafny
in an ambiguous state.
"""

import argparse
import sys
from pathlib import Path

from lark import Lark, Token, Tree

GRAMMAR_PATH = Path(__file__).resolve().parent.parent / "grammar" / "ward0.lark"

RESERVED = {"result", "len", "is_ok", "is_err", "unwrap_ok", "unwrap_err"}

# constructors that are legal in Dafny expression position (not method calls)
CONSTRUCTORS = {"Ok", "Err"}

BUILTINS = {
    "len": (1, lambda args: f"|{args[0]}|"),
    "is_ok": (1, lambda args: f"{args[0]}.Ok?"),
    "is_err": (1, lambda args: f"{args[0]}.Err?"),
    "unwrap_ok": (1, lambda args: f"{args[0]}.value"),
    "unwrap_err": (1, lambda args: f"{args[0]}.error"),
}

TYPE_MAP = {
    "int": "int",
    "bool": "bool",
    "str": "string",
    "Unit": "()",
}

INDENT = "  "


class TranspileError(Exception):
    pass


_TRUST_RE = r'(?m)^[ \t]*trust[ \t]*:[ \t]*"([^"]*)"[ \t]*$'


class Ward0Transpiler:
    def __init__(self, grammar_path: Path = GRAMMAR_PATH, enforce_boundary: bool = False):
        self.parser = Lark.open(grammar_path, parser="lalr", start="start", keep_all_tokens=True)
        self.enforce_boundary = enforce_boundary
        self.trust_report: list[dict] = []
        self._externs: dict[str, dict] = {}
        self._subst: dict[str, str] = {}

    # ---------------------------------------------------------------- public

    def _strip_trusts(self, source: str) -> tuple[str, list[tuple[str, str]]]:
        """Pull `trust: "..."` lines (toolchain annotations, not ward0 syntax)."""
        import re

        pairs = re.findall(_TRUST_RE, source)
        cleaned = re.sub(_TRUST_RE, "", source)
        return cleaned, pairs

    def transpile(self, source: str) -> str:
        cleaned, trusts = self._strip_trusts(source)
        tree = self.parser.parse(cleaned)
        file_node = tree.children[0]
        defs = [
            d.children[0]
            for d in file_node.children
            if isinstance(d, Tree) and d.data == "definition"
        ]
        if not defs:
            raise TranspileError("no fn definitions found")
        self._externs = {}
        trust_idx = 0
        for d in defs:
            if d.data == "extern_def":
                info = self._extern_info(d)
                if trust_idx < len(trusts):
                    info["trust"] = trusts[trust_idx]
                    trust_idx += 1
                self._externs[info["name"]] = info
        self.trust_report = [
            {"stub": info["name"], "trust": info.get("trust", "")}
            for info in self._externs.values()
        ]
        out = []
        if any(self.def_uses_result(d) for d in defs):
            out.append("datatype Result<T, E> = Ok(value: T) | Err(error: E)")
        self._call_counter = 0
        self._used_names = {
            v.value
            for d in defs
            for v in d.scan_values(lambda t: isinstance(t, Token) and t.type == "NAME")
        } | RESERVED | {f"{n}_checked" for n in self._externs}
        for d in defs:
            if d.data == "extern_def":
                out.append(self.extern_def(d))
        if self.enforce_boundary:
            for info in self._externs.values():
                out.append(self._wrapper_dafny(info))
        for d in defs:
            if d.data == "fn_def":
                out.append(self.fn_def(d))
        return "\n".join(out) + "\n"

    def _extern_info(self, fn: Tree) -> dict:
        """Extract the reusable parts of an extern declaration."""
        name = next(c.value for c in fn.children if isinstance(c, Token) and c.type == "NAME")
        params = next((c for c in fn.children if isinstance(c, Tree) and c.data == "params"), None)
        ret_type = next(c for c in fn.children if isinstance(c, Tree) and c.data == "type")
        contracts = [c for c in fn.children if isinstance(c, Tree) and c.data == "contract"]
        param_list = [] if params is None else params.children
        params_d = [
            (p.children[0].value, self.type_to_dafny(p.children[2]))
            for p in param_list
            if isinstance(p, Tree)
        ]
        self._subst = {}
        requires = [self.expr_to_dafny(c.children[1]) for c in contracts if c.children[0].type == "REQUIRES"]
        ensures = [self.expr_to_dafny(c.children[1]) for c in contracts if c.children[0].type == "ENSURES"]
        return {
            "name": name,
            "params_d": params_d,
            "ret": self.type_to_dafny(ret_type),
            "requires": requires,
            "ensures": ensures,
            "requires_tree": [c.children[1] for c in contracts if c.children[0].type == "REQUIRES"],
            "ensures_tree": [c.children[1] for c in contracts if c.children[0].type == "ENSURES"],
        }

    def _wrapper_dafny(self, info: dict) -> str:
        """The generated runtime contract-check wrapper for an extern stub.

        The verifier sees the stub's contract as the wrapper's postcondition
        (proved via the extern axiom); at runtime the wrapper evaluates the
        contract expression against the actual stub result and converts a
        violation into Err("contract violation") — the enforcement happens
        regardless of what the caller wrote.
        """
        name = info["name"]
        params = ", ".join(f"{n}: {t}" for n, t in info["params_d"])
        args = ", ".join(n for n, _ in info["params_d"])
        lines = [f"method {name}_checked({params}) returns (result: {info['ret']})"]
        for r in info["requires"]:
            lines.append(f"  requires {r}")
        for e in info["ensures"]:
            lines.append(f"  ensures {e}")
        lines.append("{")
        lines.append(f"  var r := {name}({args});")
        checks = []
        if info["ensures_tree"]:
            self._subst = {"result": "r"}
            try:
                checks = [f"({self.expr_to_dafny(t)})" for t in info["ensures_tree"]]
            finally:
                self._subst = {}
        if checks:
            lines.append(f"  if !({' && '.join(checks)}) {{")
            lines.append('    return Err("contract violation");')
            lines.append("  }")
        lines.append("  return r;")
        lines.append("}")
        return "\n".join(lines)

    # ------------------------------------------------------------- functions

    def def_uses_result(self, fn: Tree) -> bool:
        ret = next(c for c in fn.children if isinstance(c, Tree) and c.data == "type")
        if self.type_mentions_result(ret):
            return True
        params = next((c for c in fn.children if isinstance(c, Tree) and c.data == "params"), None)
        if params is None:
            return False
        return any(
            isinstance(p, Tree) and self.type_mentions_result(p.children[2])
            for p in params.children
        )

    def type_mentions_result(self, t: Tree) -> bool:
        if not isinstance(t, Tree):
            return False
        return any(isinstance(c, Token) and c.type == "RESULT_TYPE" for c in t.children) or any(
            isinstance(c, Tree) and self.type_mentions_result(c) for c in t.children
        )

    def fn_def(self, fn: Tree) -> str:
        name = next(c.value for c in fn.children if isinstance(c, Token) and c.type == "NAME")
        if name in RESERVED:
            raise TranspileError(f"identifier {name!r} is reserved")
        params = next((c for c in fn.children if isinstance(c, Tree) and c.data == "params"), None)
        ret_type = next(c for c in fn.children if isinstance(c, Tree) and c.data == "type")
        contracts = [c for c in fn.children if isinstance(c, Tree) and c.data == "contract"]
        block = next(c for c in fn.children if isinstance(c, Tree) and c.data == "block")

        param_list = [] if params is None else params.children
        params_d = [
            (p.children[0].value, self.type_to_dafny(p.children[2]))
            for p in param_list
            if isinstance(p, Tree)
        ]
        ret = self.type_to_dafny(ret_type)

        requires = [self.expr_to_dafny(c.children[1]) for c in contracts if c.children[0].type == "REQUIRES"]
        ensures = [self.expr_to_dafny(c.children[1]) for c in contracts if c.children[0].type == "ENSURES"]

        param_names = {n for n, _ in params_d}
        assigned = self.assigned_names(block)
        shadows = [n for n, _ in params_d if n in assigned]

        lines = []
        for n, t in params_d:
            lines.append(f"{n}: {t}")
        sig = f"method {name}({', '.join(lines)}) returns (result: {ret})"
        for r in requires:
            sig += f"\n  requires {r}"
        for e in ensures:
            sig += f"\n  ensures {e}"
        body = [f"  var {n} := {n};" for n in shadows]
        body += self.block_to_dafny(block)
        return sig + "\n{\n" + "\n".join(body) + "\n}"

    def extern_def(self, fn: Tree) -> str:
        """Emit a body-less `{:extern}` method; its contract is assumed by the
        verifier (the trust boundary). Anything untranslatable -> TranspileError."""
        name = next(c.value for c in fn.children if isinstance(c, Token) and c.type == "NAME")
        if name in RESERVED:
            raise TranspileError(f"identifier {name!r} is reserved")
        params = next((c for c in fn.children if isinstance(c, Tree) and c.data == "params"), None)
        ret_type = next(c for c in fn.children if isinstance(c, Tree) and c.data == "type")
        contracts = [c for c in fn.children if isinstance(c, Tree) and c.data == "contract"]

        param_list = [] if params is None else params.children
        params_d = [
            (p.children[0].value, self.type_to_dafny(p.children[2]))
            for p in param_list
            if isinstance(p, Tree)
        ]
        ret = self.type_to_dafny(ret_type)
        requires = [self.expr_to_dafny(c.children[1]) for c in contracts if c.children[0].type == "REQUIRES"]
        ensures = [self.expr_to_dafny(c.children[1]) for c in contracts if c.children[0].type == "ENSURES"]

        sig = f"method {{:extern}}{{:axiom}} {name}({', '.join(f'{n}: {t}' for n, t in params_d)}) returns (result: {ret})"
        for r in requires:
            sig += f"\n  requires {r}"
        for e in ensures:
            sig += f"\n  ensures {e}"
        return sig

    def assigned_names(self, block: Tree) -> set[str]:
        names: set[str] = set()

        def walk(t):
            if isinstance(t, Tree):
                if t.data == "stmt":
                    walk(t.children[0])
                elif t.data == "assign" and isinstance(t.children[0], Tree) and t.children[0].data == "lvalue":
                    lvalue = t.children[0]
                    if len(lvalue.children) == 1:
                        names.add(lvalue.children[0].value)
                else:
                    for c in t.children:
                        walk(c)

        walk(block)
        return names

    # ---------------------------------------------------------------- types

    def type_to_dafny(self, t: Tree) -> str:
        if not isinstance(t, Tree):
            raise TranspileError(f"expected type node, got {t!r}")
        kids = t.children
        first = kids[0]
        if isinstance(first, Token):
            if first.type == "RESULT_TYPE":
                return f"Result<{self.type_to_dafny(kids[2])}, {self.type_to_dafny(kids[4])}>"
            if first.type == "LIST_TYPE":
                return f"seq<{self.type_to_dafny(kids[2])}>"
            return TYPE_MAP[first.value]
        raise TranspileError(f"unsupported type node {first!r}")

    # ----------------------------------------------------------- expressions

    def expr_to_dafny(self, node) -> str:
        if isinstance(node, Token):
            return self.token_value(node)
        data = node.data
        if data == "call":
            name = node.children[0].value
            args = self.args_to_dafny(node)
            if name in BUILTINS:
                arity, fn = BUILTINS[name]
                if len(args) != arity:
                    raise TranspileError(f"{name}() takes exactly {arity} argument(s)")
                return fn(args)
            if name in self._externs and self.enforce_boundary:
                return f"{name}_checked({', '.join(args)})"
            return f"{name}({', '.join(args)})"
        if data == "indexed":
            base = node.children[0].value
            idx = self.expr_to_dafny(node.children[2])
            return f"{base}[{idx}]"
        if data == "unit_lit":
            return "()"
        if data == "quantifier":
            # children: [quant_kw, NAME, "in", "range", "(", lo, ",", hi, ")", "::", body]
            kw = node.children[0].children[0].value
            var = node.children[1].value
            lo = self.expr_to_dafny(node.children[5])
            hi = self.expr_to_dafny(node.children[7])
            body = self.expr_to_dafny(node.children[10])
            bound = f"{lo} <= {var} < {hi}"
            if kw == "forall":
                return f"forall {var} :: {bound} ==> ({body})"
            return f"exists {var} :: {bound} && ({body})"
        if data in ("or_expr", "and_expr", "sum", "product"):
            return self.fold_chain(node.children)
        if data == "not_expr":
            if len(node.children) != 2:
                return self.expr_to_dafny(node.children[0])
            return f"!({self.expr_to_dafny(node.children[1])})"
        if data == "comparison":
            return self.fold_chain(node.children)
        if data == "comp_op":
            return node.children[0].value
        if data == "factor":
            if len(node.children) == 1:
                return self.expr_to_dafny(node.children[0])
            if node.children[0].type == "MINUS":
                return f"-{self.expr_to_dafny(node.children[1])}"
            return f"({self.expr_to_dafny(node.children[1])})"
        if data == "expr":
            return self.expr_to_dafny(node.children[0])
        raise TranspileError(f"unsupported expression node {data!r}")

    def args_to_dafny(self, call: Tree) -> list[str]:
        args = next((c for c in call.children if isinstance(c, Tree) and c.data == "args"), None)
        if args is None:
            return []
        return [self.expr_to_dafny(e) for e in args.children if isinstance(e, Tree) or isinstance(e, Token) and e.type not in ("COMMA",)]

    def fold_chain(self, items) -> str:
        out = self.expr_to_dafny(items[0])
        i = 1
        while i < len(items):
            op = items[i]
            opval = op.children[0].value if isinstance(op, Tree) else op.value
            opval = {"and": "&&", "or": "||"}.get(opval, opval)
            out = f"{out} {opval} {self.expr_to_dafny(items[i + 1])}"
            i += 2
        return out

    def token_value(self, tok: Token) -> str:
        if tok.type == "TRUE":
            return "true"
        if tok.type == "FALSE":
            return "false"
        if tok.type == "NAME" and tok.value in self._subst:
            return self._subst[tok.value]
        return tok.value

    # ------------------------------------------------------------ statements

    def block_to_dafny(self, block: Tree, indent: int = 1) -> list[str]:
        lines = []
        for s in block.children:
            if not isinstance(s, Tree):
                continue
            if s.data == "stmt":
                s = s.children[0]
            lines.extend(self.hoisted_calls(s, indent))
            lines.extend(self.stmt_to_dafny(s, indent))
        return lines

    def hoisted_calls(self, stmt: Tree, indent: int) -> list[str]:
        """Dafny forbids method calls in expression position. Hoist every
        non-builtin call (innermost first) into a `var __cN := call;` statement
        and replace the call node with a fresh name token, in place. Nested
        blocks are left to their own block_to_dafny pass."""
        lines = []
        if not isinstance(stmt, Tree) or stmt.data == "for_stmt":
            return lines
        pad = INDENT * indent
        for idx, child in enumerate(stmt.children):
            if not isinstance(child, Tree) or child.data == "block":
                continue
            lines.extend(self.hoisted_calls(child, indent))
            if child.data == "call" and isinstance(child.children[0], Token) and child.children[0].value not in BUILTINS and child.children[0].value not in CONSTRUCTORS:
                if stmt.data == "call_stmt" and idx == 0:
                    continue
                while f"w{self._call_counter}" in self._used_names:
                    self._call_counter += 1
                name = f"w{self._call_counter}"
                self._call_counter += 1
                lines.append(f"{pad}var {name} := {self.expr_to_dafny(child)};")
                stmt.children[idx] = Token("NAME", name)
        return lines

    def stmt_to_dafny(self, s: Tree, indent: int) -> list[str]:
        pad = INDENT * indent
        data = s.data
        if data == "var_decl":
            name = s.children[1].value
            expr = self.expr_to_dafny(s.children[5])
            return [f"{pad}var {name} := {expr};"]
        if data == "assign":
            lvalue, op, expr = s.children[0], s.children[1], s.children[2]
            if isinstance(lvalue, Tree) and len(lvalue.children) > 1:
                raise TranspileError("record field assignment not supported in ward0 v0.1")
            name = lvalue.children[0].value
            opval = op.children[0].value if isinstance(op, Tree) else op.value
            if opval == "=":
                return [f"{pad}{name} := {self.expr_to_dafny(expr)};"]
            return [f"{pad}{name} := {name} {opval[:-1]} {self.expr_to_dafny(expr)};"]
        if data == "if_stmt":
            cond = self.expr_to_dafny(s.children[1])
            then_b = s.children[2]
            out = [f"{pad}if {cond} {{"]
            out.extend(self.block_to_dafny(then_b, indent + 1))
            if len(s.children) > 4:
                out.append(f"{pad}}} else {{")
                out.extend(self.block_to_dafny(s.children[4], indent + 1))
            out.append(f"{pad}}}")
            return out
        if data == "for_stmt":
            var = s.children[1].value
            lo = self.expr_to_dafny(s.children[5])
            hi = self.expr_to_dafny(s.children[7])
            invs = [self.expr_to_dafny(c.children[1]) for c in s.children[9:-1] if isinstance(c, Tree) and c.data == "loop_invariant"]
            block = s.children[-1]
            out = [f"{pad}for {var} := {lo} to {hi}"]
            for inv in invs:
                out.append(f"{pad}  invariant {inv}")
            out.append(f"{pad}{{")
            out.extend(self.block_to_dafny(block, indent + 1))
            out.append(f"{pad}}}")
            return out
        if data == "return_stmt":
            if len(s.children) == 3 and s.children[1] is not None:
                return [f"{pad}return {self.expr_to_dafny(s.children[1])};"]
            return [f"{pad}return;"]
        if data == "call_stmt":
            call = next(c for c in s.children if isinstance(c, Tree) and c.data == "call")
            return [f"{pad}var discard := {self.expr_to_dafny(call)};"]
        raise TranspileError(f"unsupported statement node {data!r}")


def transpile(source: str) -> str:
    return Ward0Transpiler().transpile(source)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ward0 -> Dafny transpiler")
    ap.add_argument("input", help="path to a .ward0 file")
    ap.add_argument("-o", "--output", help="output .dfy path (default: stdout)")
    args = ap.parse_args(argv)
    source = Path(args.input).read_text(encoding="utf-8")
    try:
        out = transpile(source)
    except TranspileError as e:
        print(f"transpile error: {e}", file=sys.stderr)
        return 1
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
