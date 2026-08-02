"""cert_rederive — Phase-3 standalone ward0 -> Dafny re-transpiler (stdlib only).

The one declared Level-1 honesty gap in `cert_check` (E9 / `ward-certified-code.md`):
`emitted_dafny_sha256` is *declared, not independently re-derived* — the standalone
checker has no transpiler, so the source -> emitted binding is trusted at Level-1.

Phase 3 closes it: this module re-transpiles the bound ward0 source itself and
compares the resulting Dafny hash, inside the checker, with no Dafny, no Z3, no
lark, no wardcore, no model — pure stdlib (re + json + hashlib only).

Feasibility contract: the re-transpiled output must be BYTE-IDENTICAL to
`Ward0Transpiler.transpile()` (E1 proves the real emission is deterministic —
byte-identical hashes are what make the certificate sound at all). This module is
therefore a faithful, dependency-free re-implementation of the ward0 emission
rules: a hand-rolled lexer + recursive-descent parser (mirroring the tree shapes
the emit logic indexes) + the emit logic ported verbatim from
`phase0/transpiler/transpiler.py`. No code is shared — a bug in the real
transpiler can't silently propagate into the checker (that is the point of
*independent* re-derivation). Corpus byte-identity is enforced by
`harness/test_cert_rederive.py` over every composed benchmark source.

Usage (probe):  python -m harness.cert_rederive --probe
  recomposes the 5 committed cert_probe artifacts, re-transpiles each, and
  reports whether the re-derived emitted_dafny_sha256 matches the recorded one.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# constants (must mirror transpiler.transpiler)
# --------------------------------------------------------------------------- #

RESERVED = {"result", "len", "is_ok", "is_err", "unwrap_ok", "unwrap_err"}
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

# toolchain annotations stripped pre-parse (not ward0 syntax) — same set as the
# transpiler's _strip_annotations so annotated references work on both paths.
_TRUST_RE = r'(?m)^[ \t]*trust[ \t]*:[ \t]*"([^"]*)"[ \t]*$'
_TIER_RE = r'(?m)^[ \t]*tier[ \t]*:[ \t]*(Proven|Contracted|Tested)[ \t]*$'
_EFFECT_RE = r'(?m)^[ \t]*effect[ \t]*:[ \t]*(net|db|fs|mut|partial)[ \t]*$'
_EFFECTS_RE = r'(?m)^[ \t]*effects[ \t]*:[ \t]*([^\r\n]+?)[ \t]*$'
_DEP_RE = r'(?m)^[ \t]*dep[ \t]*:[ \t]*([A-Za-z_][A-Za-z0-9_-]*)@([^\s\r\n]+?)[ \t]*$'
_LINEAR_RE = r'(?m)^[ \t]*linear[ \t]*:[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*$'

_ANNOTATION_RES = (_TRUST_RE, _TIER_RE, _EFFECT_RE, _EFFECTS_RE, _DEP_RE, _LINEAR_RE)


class RederiveError(Exception):
    """Raised when the bound source cannot be re-transpiled. The checker maps
    this to a clean INVALID (never a traceback)."""


# --------------------------------------------------------------------------- #
# minimal lark-shaped tree (same interface the emit logic indexes)
# --------------------------------------------------------------------------- #

class Token:
    __slots__ = ("type", "value")

    def __init__(self, type_: str, value: str):
        self.type = type_
        self.value = value

    def __repr__(self):  # pragma: no cover - debug aid
        return f"Token({self.type}={self.value!r})"


class Tree:
    __slots__ = ("data", "children")

    def __init__(self, data: str, children: list):
        self.data = data
        self.children = children

    def scan_values(self, pred) -> list:
        """All tokens in the subtree matching pred (lark-compatible)."""
        out: list = []

        def walk(n):
            if isinstance(n, Token):
                if pred(n):
                    out.append(n)
            else:
                for c in n.children:
                    walk(c)

        walk(self)
        return out

    def __repr__(self):  # pragma: no cover - debug aid
        return f"Tree({self.data}, n={len(self.children)})"


# --------------------------------------------------------------------------- #
# lexer — hand-rolled, mirrors the ward0.lark terminal set
# --------------------------------------------------------------------------- #

# keyword / type-word token types (must match the branches the emit logic checks)
# NOTE: lark lexes these contextually (e.g. `range` is only special inside
# for/quantifier headers); this hand lexer treats them as global keywords. That
# is corpus-invisible (byte-identical on the E1-validated composed corpus) and
# matches the emit branches, but the re-transpiler's soundness is scoped to the
# corpus the certificate's emission was verified against.
_KEYWORD_TYPES = {
    "fn": "FN", "extern": "EXTERN", "requires": "REQUIRES", "ensures": "ENSURES",
    "var": "VAR", "if": "IF", "else": "ELSE", "for": "FOR", "in": "IN",
    "range": "RANGE", "invariant": "INVARIANT", "return": "RETURN",
    "not": "NOT", "and": "AND", "or": "OR",
    "forall": "FORALL", "exists": "EXISTS",
    "true": "TRUE", "false": "FALSE",
    "int": "INT_TYPE", "bool": "BOOL_TYPE", "str": "STR_TYPE", "Unit": "UNIT_TYPE",
    "Result": "RESULT_TYPE", "List": "LIST_TYPE",
}

_OP2_TYPES = {
    "<=": "LE", ">=": "GE", "==": "EQ", "!=": "NE",
    "+=": "ASSIGN_OP", "-=": "ASSIGN_OP", "*=": "ASSIGN_OP",
    "/=": "ASSIGN_OP", "%=": "ASSIGN_OP",
    "::": "COLONCOLON", "->": "ARROW",
}

_OP1_TYPES = {
    "(": "LPAR", ")": "RPAR", "{": "LBRACE", "}": "RBRACE",
    "[": "LBRACKET", "]": "RBRACKET", ",": "COMMA", ":": "COLON",
    ";": "SEMICOLON", "<": "LESSTHAN", ">": "MORETHAN",
    "+": "PLUS", "-": "MINUS", "*": "STAR", "/": "SLASH", "%": "PERCENT",
    "=": "EQUALS", ".": "DOT",
}

_TOKEN_RE = re.compile(
    r"""
      (?P<WS>\s+)
    | (?P<COMMENT>//[^\n]*)
    | (?P<STRING>"[^"]*")
    | (?P<NUM>\d+)
    | (?P<NAME>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<OP2><=|>=|==|!=|\+=|-=|\*=|/=|%=|::|->)
    | (?P<OP1>[(){}[\] ,:;<>=+\-*/.%])
    """,
    re.VERBOSE,
)


def _tokenize(source: str) -> list:
    tokens: list = []
    pos = 0
    while pos < len(source):
        m = _TOKEN_RE.match(source, pos)
        if m is None:
            raise RederiveError(f"lex error at offset {pos}: {source[pos:pos+20]!r}")
        pos = m.end()
        kind = m.lastgroup
        text = m.group(kind)
        if kind in ("WS", "COMMENT"):
            continue
        if kind == "STRING":
            tokens.append(Token("STRING", text))
        elif kind == "NUM":
            tokens.append(Token("NUM", text))
        elif kind == "NAME":
            ttype = _KEYWORD_TYPES.get(text, "NAME")
            tokens.append(Token(ttype, text))
        elif kind == "OP2":
            tokens.append(Token(_OP2_TYPES[text], text))
        else:  # OP1
            tokens.append(Token(_OP1_TYPES[text], text))
    return tokens


# --------------------------------------------------------------------------- #
# recursive-descent parser — produces lark-shaped trees for the emit logic
# --------------------------------------------------------------------------- #

class _Parser:
    def __init__(self, tokens: list):
        self.toks = tokens
        self.i = 0

    # -- token cursor helpers ------------------------------------------------ #
    def peek(self) -> Token | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def peek2(self) -> Token | None:
        return self.toks[self.i + 1] if self.i + 1 < len(self.toks) else None

    def next(self) -> Token:
        t = self.peek()
        if t is None:
            raise RederiveError("unexpected end of source")
        self.i += 1
        return t

    def at(self, type_: str) -> bool:
        t = self.peek()
        return t is not None and t.type == type_

    def expect(self, type_: str, what: str) -> Token:
        t = self.next()
        if t.type != type_:
            raise RederiveError(f"expected {what}, got {t.value!r} at token {self.i}")
        return t

    # -- grammar ------------------------------------------------------------ #
    def parse_start(self) -> Tree:
        defs = []
        while self.peek() is not None:
            defs.append(Tree("definition", [self.definition()]))
        return Tree("file", defs)

    def definition(self) -> Tree:
        if self.at("EXTERN"):
            return self.extern_def()
        if self.at("FN"):
            return self.fn_def()
        raise RederiveError(f"expected fn or extern fn, got {self.peek().value!r}")

    def fn_def(self) -> Tree:
        children = [self.expect("FN", "'fn'"), self.expect("NAME", "function name")]
        children.append(self.expect("LPAR", "'('"))
        if not self.at("RPAR"):
            children.append(self.params())
        children.append(self.expect("RPAR", "')'"))
        children.append(self.expect("ARROW", "'->'"))
        children.append(self.type_node())
        while self.at("REQUIRES") or self.at("ENSURES"):
            children.append(self.contract())
        children.append(self.block())
        return Tree("fn_def", children)

    def extern_def(self) -> Tree:
        children = [self.expect("EXTERN", "'extern'"), self.expect("FN", "'fn'")]
        children.append(self.expect("NAME", "extern name"))
        children.append(self.expect("LPAR", "'('"))
        if not self.at("RPAR"):
            children.append(self.params())
        children.append(self.expect("RPAR", "')'"))
        children.append(self.expect("ARROW", "'->'"))
        children.append(self.type_node())
        while self.at("REQUIRES") or self.at("ENSURES"):
            children.append(self.contract())
        children.append(self.expect("SEMICOLON", "';'"))
        return Tree("extern_def", children)

    def params(self) -> Tree:
        children = [self.param()]
        while self.at("COMMA"):
            children.append(self.next())
            children.append(self.param())
        return Tree("params", children)

    def param(self) -> Tree:
        return Tree("param", [
            self.expect("NAME", "parameter name"),
            self.expect("COLON", "':'"),
            self.type_node(),
        ])

    def type_node(self) -> Tree:
        t = self.next()
        if t.type in ("INT_TYPE", "BOOL_TYPE", "STR_TYPE", "UNIT_TYPE"):
            return Tree("type", [t])
        if t.type == "RESULT_TYPE":
            lt = self.expect("LESSTHAN", "'<'")
            a = self.type_node()
            comma = self.expect("COMMA", "','")
            b = self.type_node()
            gt = self.expect("MORETHAN", "'>'")
            return Tree("type", [t, lt, a, comma, b, gt])
        if t.type == "LIST_TYPE":
            lt = self.expect("LESSTHAN", "'<'")
            a = self.type_node()
            gt = self.expect("MORETHAN", "'>'")
            return Tree("type", [t, lt, a, gt])
        raise RederiveError(f"expected a type, got {t.value!r}")

    def contract(self) -> Tree:
        kw = self.next()
        return Tree("contract", [kw, self.expr()])

    def block(self) -> Tree:
        children = [self.expect("LBRACE", "'{'")]
        while not self.at("RBRACE"):
            children.append(self.stmt())
        children.append(self.next())
        return Tree("block", children)

    def stmt(self) -> Tree:
        if self.at("VAR"):
            return Tree("stmt", [self.var_decl()])
        if self.at("IF"):
            return Tree("stmt", [self.if_stmt()])
        if self.at("FOR"):
            return Tree("stmt", [self.for_stmt()])
        if self.at("RETURN"):
            return Tree("stmt", [self.return_stmt()])
        if self.at("NAME"):
            # assign (lvalue assign_op expr ;) vs call_stmt (call ;)
            if self.peek2() is not None and self.peek2().type == "LPAR":
                return Tree("stmt", [self.call_stmt()])
            return Tree("stmt", [self.assign()])
        raise RederiveError(f"unexpected token {self.peek().value!r} in block")

    def var_decl(self) -> Tree:
        return Tree("var_decl", [
            self.expect("VAR", "'var'"),
            self.expect("NAME", "variable name"),
            self.expect("COLON", "':'"),
            self.type_node(),
            self.expect("EQUALS", "'='"),
            self.expr(),
            self.expect("SEMICOLON", "';'"),
        ])

    def assign(self) -> Tree:
        return Tree("assign", [
            self.lvalue(),
            Tree("assign_op", [self.next()]),  # "=" | "+=" | ...
            self.expr(),
            self.expect("SEMICOLON", "';'"),
        ])

    def lvalue(self) -> Tree:
        children = [self.expect("NAME", "lvalue name")]
        while self.at("DOT"):
            children.append(self.next())
            children.append(self.expect("NAME", "field name"))
        return Tree("lvalue", children)

    def if_stmt(self) -> Tree:
        children = [self.expect("IF", "'if'"), self.expr(), self.block()]
        if self.at("ELSE"):
            children.append(self.next())
            children.append(self.block())
        return Tree("if_stmt", children)

    def for_stmt(self) -> Tree:
        children = [
            self.expect("FOR", "'for'"),
            self.expect("NAME", "loop variable"),
            self.expect("IN", "'in'"),
            self.expect("RANGE", "'range'"),
            self.expect("LPAR", "'('"),
            self.expr(),
            self.expect("COMMA", "','"),
            self.expr(),
            self.expect("RPAR", "')'"),
        ]
        while self.at("INVARIANT"):
            children.append(Tree("loop_invariant", [self.next(), self.expr()]))
        children.append(self.block())
        return Tree("for_stmt", children)

    def return_stmt(self) -> Tree:
        children = [self.expect("RETURN", "'return'")]
        if not self.at("SEMICOLON"):
            children.append(self.expr())
        children.append(self.expect("SEMICOLON", "';'"))
        return Tree("return_stmt", children)

    def call_stmt(self) -> Tree:
        return Tree("call_stmt", [self.call(), self.expect("SEMICOLON", "';'")])

    # -- expressions (with lark ?-inlining: single-child rules collapse) ---- #
    def expr(self):
        return self.or_expr()

    def or_expr(self):
        items = [self.and_expr()]
        while self.at("OR"):
            items.append(self.next())
            items.append(self.and_expr())
        return items[0] if len(items) == 1 else Tree("or_expr", items)

    def and_expr(self):
        items = [self.not_expr()]
        while self.at("AND"):
            items.append(self.next())
            items.append(self.not_expr())
        return items[0] if len(items) == 1 else Tree("and_expr", items)

    def not_expr(self):
        if self.at("NOT"):
            return Tree("not_expr", [self.next(), self.not_expr()])
        return self.comparison()

    def comparison(self):
        # comp ops are lexed as EQ/NE/LE/GE and — for the raw < > chars —
        # LESSTHAN/MORETHAN (the same terminal tokens the type grammar uses;
        # lark names them once, so the comparison branch must accept both).
        items = [self.sum_expr()]
        while self.peek() is not None and self.peek().type in ("EQ", "NE", "LE", "GE", "LESSTHAN", "MORETHAN"):
            items.append(Tree("comp_op", [self.next()]))
            items.append(self.sum_expr())
        return items[0] if len(items) == 1 else Tree("comparison", items)

    def sum_expr(self):
        items = [self.product()]
        while self.at("PLUS") or self.at("MINUS"):
            items.append(self.next())
            items.append(self.product())
        return items[0] if len(items) == 1 else Tree("sum", items)

    def product(self):
        items = [self.factor()]
        while self.at("STAR") or self.at("SLASH") or self.at("PERCENT"):
            items.append(self.next())
            items.append(self.factor())
        return items[0] if len(items) == 1 else Tree("product", items)

    def factor(self):
        if self.at("MINUS"):
            return Tree("factor", [self.next(), self.factor()])
        if self.at("LPAR"):
            if self.peek2() is not None and self.peek2().type == "RPAR":
                return Tree("unit_lit", [self.next(), self.next()])
            lp = self.next()
            inner = self.expr()
            rp = self.expect("RPAR", "')'")
            return Tree("factor", [lp, inner, rp])
        return self.atom()

    def atom(self):
        t = self.peek()
        if t.type == "NAME":
            if self.peek2() is not None and self.peek2().type == "LPAR":
                return self.call()
            if self.peek2() is not None and self.peek2().type == "LBRACKET":
                return self.indexed()
            name = self.next()
            if self.at("DOT"):
                children = [name]
                while self.at("DOT"):
                    children.append(self.next())
                    children.append(self.expect("NAME", "field name"))
                return Tree("atom", children)
            return name  # single NAME collapses (lark ?atom)
        if t.type in ("NUM", "STRING", "TRUE", "FALSE"):
            return self.next()
        if t.type in ("FORALL", "EXISTS"):
            return self.quantifier()
        raise RederiveError(f"unexpected token {t.value!r} in expression")

    def call(self) -> Tree:
        children = [self.expect("NAME", "call name"), self.expect("LPAR", "'('")]
        if not self.at("RPAR"):
            children.append(self.args())
        children.append(self.expect("RPAR", "')'"))
        return Tree("call", children)

    def args(self) -> Tree:
        children = [self.expr()]
        while self.at("COMMA"):
            children.append(self.next())
            children.append(self.expr())
        return Tree("args", children)

    def indexed(self) -> Tree:
        return Tree("indexed", [
            self.expect("NAME", "indexed base"),
            self.expect("LBRACKET", "'['"),
            self.expr(),
            self.expect("RBRACKET", "']'"),
        ])

    def quantifier(self) -> Tree:
        kw = self.next()  # FORALL | EXISTS
        children = [Tree("quant_kw", [kw])]
        children.append(self.expect("NAME", "bound variable"))
        children.append(self.expect("IN", "'in'"))
        children.append(self.expect("RANGE", "'range'"))
        children.append(self.expect("LPAR", "'('"))
        children.append(self.expr())
        children.append(self.expect("COMMA", "','"))
        children.append(self.expr())
        children.append(self.expect("RPAR", "')'"))
        children.append(self.expect("COLONCOLON", "'::'"))
        children.append(self.expr())
        return Tree("quantifier", children)


# --------------------------------------------------------------------------- #
# emit — ported verbatim from transpiler/transpiler.py (no shared code)
# --------------------------------------------------------------------------- #

class Rederiver:
    def __init__(self, enforce_boundary: bool = False):
        self.enforce_boundary = enforce_boundary
        self._externs: dict[str, dict] = {}
        self._subst: dict[str, str] = {}
        self._call_counter = 0
        self._used_names: set[str] = set()

    # ---------------------------------------------------------------- public

    def transpile(self, source: str) -> str:
        cleaned, _trusts = self._strip_annotations(source)
        tree = _Parser(_tokenize(cleaned)).parse_start()
        file_node = tree
        defs = [
            d.children[0]
            for d in file_node.children
            if isinstance(d, Tree) and d.data == "definition"
        ]
        if not defs:
            raise RederiveError("no fn definitions found")
        self._externs = {}
        for d in defs:
            if d.data == "extern_def":
                info = self._extern_info(d)
                self._externs[info["name"]] = info
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

    def _strip_annotations(self, source: str) -> tuple[str, list[str]]:
        pairs = re.findall(_TRUST_RE, source)
        cleaned = source
        for r in _ANNOTATION_RES:
            cleaned = re.sub(r, "", cleaned)
        return cleaned, pairs

    # ------------------------------------------------------------- extern

    def _extern_info(self, fn: Tree) -> dict:
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
            raise RederiveError(f"identifier {name!r} is reserved")
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
        name = next(c.value for c in fn.children if isinstance(c, Token) and c.type == "NAME")
        if name in RESERVED:
            raise RederiveError(f"identifier {name!r} is reserved")
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

    def assigned_names(self, block: Tree) -> set:
        names: set = set()

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
            raise RederiveError(f"expected type node, got {t!r}")
        kids = t.children
        first = kids[0]
        if isinstance(first, Token):
            if first.type == "RESULT_TYPE":
                return f"Result<{self.type_to_dafny(kids[2])}, {self.type_to_dafny(kids[4])}>"
            if first.type == "LIST_TYPE":
                return f"seq<{self.type_to_dafny(kids[2])}>"
            return TYPE_MAP[first.value]
        raise RederiveError(f"unsupported type node {first!r}")

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
                    raise RederiveError(f"{name}() takes exactly {arity} argument(s)")
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
        raise RederiveError(f"unsupported expression node {data!r}")

    def args_to_dafny(self, call: Tree) -> list:
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

    def block_to_dafny(self, block: Tree, indent: int = 1) -> list:
        lines = []
        for s in block.children:
            if not isinstance(s, Tree):
                continue
            if s.data == "stmt":
                s = s.children[0]
            lines.extend(self.hoisted_calls(s, indent))
            lines.extend(self.stmt_to_dafny(s, indent))
        return lines

    def hoisted_calls(self, stmt: Tree, indent: int) -> list:
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

    def stmt_to_dafny(self, s: Tree, indent: int) -> list:
        pad = INDENT * indent
        data = s.data
        if data == "var_decl":
            name = s.children[1].value
            expr = self.expr_to_dafny(s.children[5])
            return [f"{pad}var {name} := {expr};"]
        if data == "assign":
            lvalue, op, expr = s.children[0], s.children[1], s.children[2]
            if isinstance(lvalue, Tree) and len(lvalue.children) > 1:
                raise RederiveError("record field assignment not supported in ward0 v0.1")
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
        raise RederiveError(f"unsupported statement node {data!r}")


def transpile(source: str, enforce: bool = False) -> str:
    """Re-transpile ward0 source to Dafny (stdlib-only, byte-identical to the
    real transpiler — enforced by the corpus identity test)."""
    return Rederiver(enforce_boundary=enforce).transpile(source)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# feasibility probe: re-derive emitted_dafny_sha256 for the 5 committed artifacts
# --------------------------------------------------------------------------- #

BENCH_DIR = Path(__file__).resolve().parent.parent / "benchmarks" / "w_tasks"
CERT_DIR = Path(__file__).resolve().parent.parent / "experiments" / "runs" / "cert_probe"

PROBE_TASKS = [
    "w1_payment_chain",
    "w4_order_placement",
    "w5_currency_roundtrip",
    "w6_crud_handler",
    "w7_idempotency",
]


def _extern_ward0_of(task: dict) -> str:
    """Recompose the extern stub declaration(s) in ward0 (trust arm) — mirrors
    DafnyRunner.extern_ward0_of so the probe binds the exact verified source."""
    parts = []
    for stub in task.get("externs", []):
        params_sig = ", ".join(f"{n}: {t}" for n, t in stub["params"])
        sig = f"extern fn {stub['name']}({params_sig}) -> {stub['ret']}"
        if stub.get("contract"):
            sig += "\n  " + stub["contract"]
        parts.append(sig + ";")
    return "\n\n".join(parts)


def probe_artifacts(tasks: list | None = None) -> list[dict]:
    """For each committed .proof: recompose the bound source, re-transpile,
    compare the re-derived emitted_dafny_sha256 against the recorded one."""
    rows = []
    for tid in tasks or PROBE_TASKS:
        proof = json.loads((CERT_DIR / f"{tid}.proof").read_text(encoding="utf-8"))
        task = json.loads((BENCH_DIR / f"{tid}.json").read_text(encoding="utf-8"))
        ward0 = (BENCH_DIR / f"{tid}.ward0").read_text(encoding="utf-8")
        full_src = _extern_ward0_of(task) + "\n\n" + ward0
        # the certificate's source hash binds exactly this text — check it first
        src_ok = sha256_text(full_src) == proof["source_sha256"]
        enforce = bool(proof.get("toolchain", {}).get("enforce_boundary", False))
        try:
            dafny = transpile(full_src, enforce=enforce)
            derived = sha256_text(dafny)
        except RederiveError as exc:
            derived = f"REDERIVE ERROR: {exc}"
        match = derived == proof["emitted_dafny_sha256"]
        rows.append({
            "task": tid,
            "source_rebind": src_ok,
            "enforce": enforce,
            "match": match,
            "recorded": proof["emitted_dafny_sha256"][:16],
            "re_derived": (derived[:16] if isinstance(derived, str) else derived),
            "detail": "" if match else (
                derived if derived.startswith("REDERIVE ERROR")
                else f"re-derived {derived[:16]}… != recorded {proof['emitted_dafny_sha256'][:16]}…"
            ),
        })
    return rows


def main(argv: list | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Phase-3 re-derivation feasibility probe")
    ap.add_argument("--probe", action="store_true", help="(default) run the feasibility probe on the committed artifacts")
    ap.add_argument("--tasks", nargs="*", default=PROBE_TASKS)
    args = ap.parse_args(argv)
    rows = probe_artifacts(args.tasks)
    print(f"{'task':<20}{'source_ok':<11}{'enforce':<8}{'hash_match':<11}detail")
    all_ok = True
    for r in rows:
        print(f"{r['task']:<20}{str(r['source_rebind']):<11}{str(r['enforce']):<8}{str(r['match']):<11}{r['detail']}")
        all_ok &= bool(r["source_rebind"]) and bool(r["match"])
    print(f"\nre-derived emitted_dafny_sha256 matches on {sum(1 for r in rows if r['match'])}/{len(rows)} artifacts")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
