"""Specification Tightness (tau) — canonical engine, theory instrument I1
(pre-registered in files/ward-phase2-scoping.md section 10).

Definition (bounded-domain):
    tau(x) = 1 - log2(|Y_perm(x)|) / log2(|Y|)        for admissible x (P(x) true)
    tau    = E_{x ~ P}[tau(x)]

    Y_perm(x) = { y in Y : Q(x, y) }   -- outputs the contract permits at x
    Y         = bounded output space (grid derived from the contract's own
                literals + fixed anchors)

tau = 1: the contract pins the output completely (maximally anti-slop).
tau = 0: `ensures true` -- zero output entropy constrained (vacuous).

Honest limits (stated, not hidden):
  * Bounded domains only: the input grid is built from the contract's own
    literals + fixed anchors; the output grid from the ensures literals.
    Exact, reproducible, and reported per task.
  * Result<T, str>: the Err class collapses all error strings into one
    outcome (contracts only ever talk about is_ok), so |Y| = |Ok-grid| + 1.
  * count==0 (spec unsatisfiable on the grid) is reported separately and
    counted as tight (tau=1) with a flag, never silently.
  * Unbounded/quantifier expressions are not evaluated: status = unevaluable.

This module is the engine; experiments/tightness_probe.py is the probe CLI and
wardcore/tightness_gate.py is the gate (I1 gate: Proven requires tau >= tau0).
"""

import math
import random
import re

UNIT = "<unit>"

# ---------------------------------------------------------------------------
# Minimal recursive-descent parser for the ward0 contract expression subset.
# Supports: ints, strs, bools, Unit (), params, `result`,
#   is_ok/is_err/unwrap_ok/unwrap_err calls,
#   == != < <= > >=, + - *, and/or/not, parens.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
    \s*(?:
        (?P<num>\d+)
      | (?P<str>"[^"]*")
      | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op><=|>=|==|!=|<|>|\+|-|\*)
      | (?P<lpar>\()
      | (?P<rpar>\))
    )""", re.VERBOSE)


def tokenize(s):
    toks = []
    pos = 0
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m:
            rest = s[pos:].strip()
            raise ValueError(f"unexpected token in contract expr: {rest[:20]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "num":
            toks.append(("num", int(m.group("num"))))
        elif kind == "str":
            toks.append(("str", m.group("str")[1:-1]))
        elif kind == "name":
            toks.append(("name", m.group("name")))
        elif kind == "op":
            toks.append(("op", m.group("op")))
        elif kind == "lpar":
            toks.append(("lpar", "("))
        elif kind == "rpar":
            toks.append(("rpar", ")"))
    toks.append(("eof", ""))
    return toks


class _Parser:
    def __init__(self, s):
        self.toks = tokenize(s)
        self.i = 0

    def peek(self):
        return self.toks[self.i]

    def next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind, val=None):
        t = self.next()
        if t[0] != kind or (val is not None and t[1] != val):
            raise ValueError(f"expected {kind} {val!r}, got {t!r}")
        return t

    def parse(self):
        e = self.or_expr()
        self.expect("eof")
        return e

    def or_expr(self):
        e = self.and_expr()
        while self.peek() == ("name", "or"):
            self.next()
            e = ("bin", "or", e, self.and_expr())
        return e

    def and_expr(self):
        e = self.not_expr()
        while self.peek() == ("name", "and"):
            self.next()
            e = ("bin", "and", e, self.not_expr())
        return e

    def not_expr(self):
        if self.peek() == ("name", "not"):
            self.next()
            return ("un", "not", self.not_expr())
        return self.cmp_expr()

    def cmp_expr(self):
        e = self.sum_expr()
        while self.peek()[0] == "op" and self.peek()[1] in ("==", "!=", "<", "<=", ">", ">="):
            op = self.next()[1]
            e = ("bin", op, e, self.sum_expr())
        return e

    def sum_expr(self):
        e = self.term()
        while self.peek() == ("op", "+") or self.peek() == ("op", "-"):
            op = self.next()[1]
            e = ("bin", op, e, self.term())
        return e

    def term(self):
        e = self.factor()
        while self.peek() == ("op", "*"):
            self.next()
            e = ("bin", "*", e, self.factor())
        return e

    def factor(self):
        if self.peek() == ("op", "-"):
            self.next()
            return ("un", "-", self.factor())
        if self.peek() == ("lpar", "("):
            # unit literal () or parenthesised expr
            if self.toks[self.i + 1] == ("rpar", ")"):
                self.next(); self.next()
                return ("lit", UNIT)
            self.next()
            e = self.or_expr()
            self.expect("rpar", ")")
            return e
        return self.atom()

    def atom(self):
        t = self.next()
        if t[0] == "num":
            return ("lit", t[1])
        if t[0] == "str":
            return ("lit", t[1])
        if t == ("name", "true"):
            return ("lit", True)
        if t == ("name", "false"):
            return ("lit", False)
        if t[0] == "name":
            name = t[1]
            if self.peek() == ("lpar", "("):
                self.next()
                args = []
                if self.peek() != ("rpar", ")"):
                    args.append(self.or_expr())
                # NOTE: single-arg calls only in the w-task contract subset;
                # multi-arg calls (e.g. stripe_charge(amount, token)) fail at
                # tokenize() because ',' is not a token -> unevaluable.
                self.expect("rpar", ")")
                return ("call", name, args)
            return ("name", name)
        raise ValueError(f"unexpected token in expr: {t!r}")


def parse_expr(s):
    return _Parser(s).parse()


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

def _eval(node, env):
    k = node[0]
    if k == "lit":
        return node[1]
    if k == "name":
        if node[1] not in env:
            raise KeyError(f"name not in scope: {node[1]}")
        return env[node[1]]
    if k == "un":
        v = _eval(node[2], env)
        if node[1] == "not":
            return not v
        if node[1] == "-":
            return -v
    if k == "bin":
        op = node[1]
        if op in ("and", "or"):
            l = _eval(node[2], env)
            if op == "and":
                return l and _eval(node[3], env)
            return l or _eval(node[3], env)
        l = _eval(node[2], env)
        r = _eval(node[3], env)
        if op == "==":
            return l == r
        if op == "!=":
            return l != r
        if op == "<":
            return l < r
        if op == "<=":
            return l <= r
        if op == ">":
            return l > r
        if op == ">=":
            return l >= r
        if op == "+":
            return l + r
        if op == "-":
            return l - r
        if op == "*":
            return l * r
    if k == "call":
        name, args = node[1], node[2]
        if name == "is_ok":
            r = _eval(args[0], env)
            return r[0] == "ok"
        if name == "is_err":
            r = _eval(args[0], env)
            return r[0] == "err"
        if name == "unwrap_ok":
            r = _eval(args[0], env)
            if r[0] != "ok":
                raise ValueError("unwrap_ok on Err")
            return r[1]
        if name == "unwrap_err":
            r = _eval(args[0], env)
            if r[0] != "err":
                raise ValueError("unwrap_err on Ok")
            return r[1]
        raise ValueError(f"unsupported call: {name}")
    raise ValueError(f"unsupported node: {node}")


# ---------------------------------------------------------------------------
# Bounded domains
# ---------------------------------------------------------------------------

ANCHOR_INTS = [0, 1, 2, 10, 100, 500, 1000]
# Output-space anchors for plain `int` returns: must span negatives so a
# constraint like `result >= 0` genuinely discriminates (an all-non-negative
# grid would score it 0 even though it rules out negative outputs).
INT_OUT_ANCHORS = [-1000, -100, -10, -1, 0, 1, 10, 100, 1000]


def int_literals(*texts):
    return sorted({int(t) for t in re.findall(r"\d+", " ".join(texts))})


def build_input_grid(params, requires, ensures):
    """Per-param bounded grids; product capped by sampling (seed 0)."""
    grids = []
    for name, typ in params:
        if typ == "int":
            lits = int_literals(*requires, *ensures)
            vals = sorted(set(ANCHOR_INTS) | set(lits))
            grids.append([(name, v) for v in vals[:12]])
        elif typ == "str":
            grids.append([(name, "tok")])
        elif typ == "bool":
            grids.append([(name, False), (name, True)])
        else:
            grids.append([(name, 0)])  # unsupported type: single dummy value
    combos = [dict(g) for g in _product(grids)]
    if len(combos) > 20000:
        rng = random.Random(0)
        combos = rng.sample(combos, 20000)
    return combos


def _product(grids):
    if not grids:
        return [()]
    out = [()]
    for g in grids:
        out = [c + (item,) for c in out for item in g]
    return out


def build_output_space(ret, ensures):
    """Bounded output space Y per return type:
      Result<Unit, str> -> [Ok, Err]             |Y| = 2
      Result<int, str>  -> Ok-grid + Err         |Y| = |grid| + 1
      int               -> int grid              |Y| = |grid|
      bool              -> [True, False]         |Y| = 2
    Returns None for unsupported types."""
    if ret.startswith("Result<Unit"):
        return [("ok", UNIT), ("err", "err")]
    if ret.startswith("Result<int"):
        lits = int_literals(*ensures)
        vals = sorted(set([0, 1]) | set(lits))[:8]
        return [("ok", v) for v in vals] + [("err", "err")]
    if ret.startswith("Result<bool"):
        return [("ok", True), ("ok", False), ("err", "err")]
    if ret.startswith("int"):
        lits = int_literals(*ensures)
        vals = sorted(set(INT_OUT_ANCHORS) | set(lits))[:12]
        return list(vals)
    if ret.startswith("bool"):
        return [True, False]
    return None


# ---------------------------------------------------------------------------
# Surface parsing (shared by the probe and the gate)
# ---------------------------------------------------------------------------

_FN_RE = re.compile(r"fn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)\s*->\s*([^\s{]+)")
_PARAM_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_]+)")
_CONTRACT_LINE_RE = re.compile(r"^\s*(requires|ensures)\s+(.*)$")


def parse_ward0_fns(src: str) -> list[dict]:
    """Parse all fn definitions in a ward0 source into surface fn dicts:
    {name, params: [(name, type)], ret, requires: [str], ensures: [str]}.
    Shared by experiments/tightness_probe.py and wardcore/tightness_gate.py
    so the mini-parser exists exactly once."""
    out = []
    for m in _FN_RE.finditer(src):
        name, params_s, ret = m.group(1), m.group(2), m.group(3)
        params = [(p.group(1), p.group(2)) for p in _PARAM_RE.finditer(params_s)]
        reqs, ens = [], []
        for line in src[m.end():].splitlines():
            cm = _CONTRACT_LINE_RE.match(line)
            if cm:
                (reqs if cm.group(1) == "requires" else ens).append(cm.group(2))
            elif line.strip().startswith("{"):
                break
        out.append({"name": name, "params": params, "ret": ret,
                    "requires": reqs, "ensures": ens})
    return out


# ---------------------------------------------------------------------------
# Tightness computation
# ---------------------------------------------------------------------------

def compute_tightness(params, ret, requires, ensures):
    """Return a result dict. status is 'ok' or 'unevaluable: <reason>'."""
    for clause in list(requires) + list(ensures):
        if re.search(r"\b(forall|exists|len)\b", clause):
            return {"status": "unevaluable: quantifier/len", "tau": None,
                    "|Y|": None, "admissible": 0, "zero_count": 0}
    try:
        P = [parse_expr(c) for c in requires]
        Q = [parse_expr(c) for c in ensures]
    except (ValueError, KeyError) as e:
        return {"status": f"unevaluable: parse ({e})", "tau": None,
                "|Y|": None, "admissible": 0, "zero_count": 0}

    Y = build_output_space(ret, ensures)
    if Y is None:
        return {"status": f"unevaluable: ret {ret}", "tau": None,
                "|Y|": None, "admissible": 0, "zero_count": 0}

    grid = build_input_grid(params, requires, ensures)

    admissible = 0
    zero_count = 0
    tau_sum = 0.0
    log2Y = math.log2(len(Y))
    for x in grid:
        env = dict(x)
        try:
            if not all(_eval(p, env) for p in P):
                continue
        except (KeyError, ValueError, TypeError):
            continue
        admissible += 1
        count = 0
        for y in Y:
            env["result"] = y
            try:
                if all(_eval(q, env) for q in Q):
                    count += 1
            except (KeyError, ValueError, TypeError):
                continue
        if count == 0:
            zero_count += 1
            tau_x = 1.0  # over-constrained on grid: tight, flagged
        else:
            tau_x = 1.0 - math.log2(count) / log2Y
        tau_sum += tau_x

    return {"status": "ok",
            "tau": round(tau_sum / admissible, 3) if admissible else None,
            "|Y|": len(Y), "admissible": admissible,
            "zero_count": zero_count}
