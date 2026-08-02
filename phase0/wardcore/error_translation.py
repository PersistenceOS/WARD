"""ward-core error translation (Phase-2 week 7) — R8 as a core module.

Pre-registered scope (files/ward-phase2-scoping.md §2 R8, §3 scope item 2,
§6 E8; design doc §7):

- **R8 (error translation):** the elaborator's error surface keeps the
  Phase-0/1 status taxonomy (pass / transpile_error / verify_fail /
  test_fail / model_error) and adds the design doc §7 structured-error form:
  `(location, violated_obligation, counterexample)` triples translated back
  into **surface terms** for the repair loop — the Phase-1 harness showed raw
  Dafny errors; Phase 2 must translate.

- **Triple shape:**
  - location — (emitted line, col) mapped to the ward0 surface construct
    (fn name + clause kind + clause index) via a line map over the emitted
    Dafny. The map is built from the EMITTED text (what Dafny actually
    sees), never from emit internals — sound because E1 proves emission is
    byte-identical/deterministic, and zero-risk to E1 parity.
  - violated_obligation — a canonical kind rendered WITH the ward0 clause
    text, e.g. "postcondition of transfer — ensures is_ok(result) ==
    (amount <= 1000000) — could not be proved on this return path".
  - counterexample — the model values Dafny reports (via
    `--extract-counterexample`), parsed from `assume <value> == <name>;`
    lines into name -> value.

- **Kinds:** postcondition | precondition | assertion | termination |
  parse | timeout | other — plus the I1 advisory kind `tightness` (a
  standalone triple for a Proven fn whose spec is too weak to justify a
  proof, appended by `annotate_tightness`, never fabricated from Dafny
  output).

- **Boundary (scoping doc §7 E8 row):** if E8 fails (triples not legible)
  the repair loop keeps raw-Dafny errors as the fallback and the gap is
  logged as a Phase-2 finding. This module makes the translation; the gate
  (wardcore/e8_gate.py) measures legibility + a deterministic repair probe
  (the real-model repair probe runs with E7 in week 8).

- **Surface terms = the language the model is fluent in (design doc §3/§7).**
  A triple's `surface` one-liner names the ward0 function and the actual
  ward0 contract clause text, never a `task.dfy(3,12)` line — the model can
  act on it directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from wardcore.ir import (
    Binary,
    BoolLit,
    Call,
    Contract,
    ExternFn,
    Function,
    Indexed,
    IntLit,
    Module,
    Paren,
    Quant,
    StrLit,
    Unary,
    UnitLit,
    Var,
)
from wardcore.tightness_gate import TAU0

# ---------------------------------------------------------------------------
# structured-error schema (R8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceLocation:
    """A ward0 surface location: (fn, construct, clause index) + raw emitted (line, col)."""

    fn: str = ""  # ward0 function name (or extern name / "" for module-level)
    kind: str = "module"  # fn | extern | wrapper | module
    clause: str = "body"  # requires | ensures | body | sig | datatype
    clause_idx: int = -1  # index into the fn's requires/ensures list (-1 = n/a)
    emitted_line: int = 0
    emitted_col: int = 0

    def surface(self) -> str:
        if self.clause in ("requires", "ensures"):
            return f"{self.fn}:{self.clause}"
        if self.kind in ("fn", "extern"):
            return f"{self.fn}:{self.clause}"
        return f"{self.fn or '(module)'}:{self.clause}"


@dataclass(frozen=True)
class StructuredError:
    """The R8 `(location, violated_obligation, counterexample)` triple."""

    kind: str  # postcondition | precondition | assertion | termination | parse | timeout | other | tightness
    location: SurfaceLocation
    violated_obligation: str  # canonical surface text (already ward0 terms)
    counterexample: dict[str, str] = field(default_factory=dict)  # name -> value
    raw: str = ""  # the raw Dafny error line, for traceability only
    surface: str = ""  # one-line surface term for the repair loop
    tightness_advisory: str = ""  # I1: tau + weak-clause target appended for a Proven fn below TAU0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "location": {
                "fn": self.location.fn,
                "construct": self.location.kind,
                "clause": self.location.clause,
                "clause_idx": self.location.clause_idx,
                "emitted_line": self.location.emitted_line,
                "emitted_col": self.location.emitted_col,
                "surface": self.location.surface(),
            },
            "violated_obligation": self.violated_obligation,
            "counterexample": dict(self.counterexample),
            "surface": self.surface,
            "raw": self.raw,
            "tightness_advisory": self.tightness_advisory,
        }


# ---------------------------------------------------------------------------
# raw Dafny output -> issues
# ---------------------------------------------------------------------------

# Dafny 4.11 diagnostic lines:  <path>(<line>,<col>): <Severity>: <message>
_ISSUE_RE = re.compile(
    r"^(?P<path>.+)\((?P<line>\d+),(?P<col>\d+)\):\s*"
    r"(?P<sev>Error|Related location|Warning|Info):\s*(?P<msg>.+?)\s*$"
)

# counterexample value lines:  assume 0 == a;   /   assume 0 == a && 0 == r;
_ASSUME_RE = re.compile(r"assume\s+(.+?);\s*$")
_EQUALS_RE = re.compile(r"^(?P<lhs>\S+)\s*==\s*(?P<rhs>[A-Za-z_][A-Za-z0-9_]*)$")

# runner timeout detail (harness/wallclock): "verify wall-clock timeout after Ns (process tree killed): ..."
_TIMEOUT_RE = re.compile(r"verify wall-clock timeout after (?P<secs>\d+)s\b")


@dataclass
class _RawIssue:
    line: int
    col: int
    sev: str  # Error | Related location | Warning | Info
    msg: str
    cex: dict[str, str] = field(default_factory=dict)


def parse_dafny_output(raw: str) -> list[_RawIssue]:
    """Split raw `dafny verify` output into issues, attaching any model values
    that follow each issue (`Related counterexample:` blocks)."""
    issues: list[_RawIssue] = []
    current: _RawIssue | None = None
    in_cex = False
    for line in raw.splitlines():
        m = _ISSUE_RE.match(line)
        if m:
            current = _RawIssue(
                line=int(m.group("line")),
                col=int(m.group("col")),
                sev=m.group("sev"),
                msg=m.group("msg"),
            )
            issues.append(current)
            in_cex = False
            continue
        if "counterexample" in line.lower():
            in_cex = True
            continue
        if in_cex and current is not None:
            am = _ASSUME_RE.match(line.strip())
            if am:
                for part in am.group(1).split("&&"):
                    em = _EQUALS_RE.match(part.strip())
                    if em:
                        current.cex[em.group("rhs")] = em.group("lhs")
    return issues


def classify(message: str) -> str:
    """Map a raw Dafny error message to a canonical obligation kind (R8)."""
    m = message.lower()
    if "postcondition could not be proved" in m:
        return "postcondition"
    if "precondition for this call could not be proved" in m:
        return "precondition"
    if "precondition that could not be proved" in m:
        return "precondition"
    if "assertion might not hold" in m or "assertion could not be proved" in m:
        return "assertion"
    if "decreases" in m or "could not be proved to terminate" in m or "termination" in m:
        return "termination"
    if "expected" in m or "parse" in m or "token" in m:
        return "parse"
    return "other"


# ---------------------------------------------------------------------------
# emitted-Dafny -> ward0 surface line map
# ---------------------------------------------------------------------------

# method sigs: `method name(...)`, `method {:verify false} name(...)`,
# `method {:extern}{:axiom} name(...)`, `method name_checked(...)`
_METHOD_RE = re.compile(
    r"^\s*method\s+(?:(?:\{[^{}]*\}\s*)*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\(.*$"
)
_CONTRACT_RE = re.compile(r"^\s*(?P<kw>requires|ensures)\s+(?P<body>.+?)\s*$")


class EmittedLineMap:
    """Line map over emitted Dafny: emitted line -> ward0 surface construct.

    Built by scanning the emitted text (what Dafny sees), so it can never
    drift from the verified artifact — sound because E1 proves emission is
    byte-identical and deterministic.
    """

    def __init__(self, emitted: str, extern_names: set[str] = frozenset()):
        self._lines: list[dict] = []  # per line: {kind, fn, clause, clause_idx}
        cur: dict = {"kind": "module", "fn": "", "clause": "datatype"}
        req_idx = 0
        ens_idx = 0
        for line in emitted.splitlines():
            mm = _METHOD_RE.match(line)
            if mm:
                name = mm.group("name")
                if name.endswith("_checked"):
                    # wrapper contract lines map back to the extern's ward0
                    # name (strip the generated `_checked` suffix) so the
                    # triple names the extern clause, not the generated method
                    cur = {"kind": "extern", "fn": name[: -len("_checked")], "clause": "sig"}
                elif name in extern_names:
                    cur = {"kind": "extern", "fn": name, "clause": "sig"}
                else:
                    cur = {"kind": "fn", "fn": name, "clause": "sig"}
                req_idx = 0
                ens_idx = 0
                self._lines.append(dict(cur))
                continue
            if line.strip() == "{":
                # body opening brace ends the contract region — everything
                # after is body until the next method (don't inherit the
                # previous requires/ensures clause)
                cur = dict(cur, clause="body", clause_idx=-1)
                self._lines.append(dict(cur))
                continue
            cm = _CONTRACT_RE.match(line)
            if cm:
                kw = cm.group("kw")
                if kw == "requires":
                    cur = dict(cur, clause="requires", clause_idx=req_idx)
                    req_idx += 1
                else:
                    cur = dict(cur, clause="ensures", clause_idx=ens_idx)
                    ens_idx += 1
                self._lines.append(dict(cur))
                continue
            self._lines.append(dict(cur))

    def annotate(self, line: int) -> SurfaceLocation:
        if 1 <= line <= len(self._lines):
            meta = self._lines[line - 1]
            return SurfaceLocation(
                fn=meta.get("fn", ""),
                kind=meta.get("kind", "module"),
                clause=meta.get("clause", "body"),
                clause_idx=meta.get("clause_idx", -1),
                emitted_line=line,
            )
        return SurfaceLocation(emitted_line=line)


# ---------------------------------------------------------------------------
# clause rendering (IR -> ward0 surface text)
# ---------------------------------------------------------------------------


def render_expr(e) -> str:
    """Render an IR expression as ward0 surface text (design doc §3: the
    language the model is fluent in)."""
    if isinstance(e, IntLit):
        return str(e.value)
    if isinstance(e, BoolLit):
        return "true" if e.value else "false"
    if isinstance(e, StrLit):
        return f'"{e.value}"'
    if isinstance(e, UnitLit):
        return "()"
    if isinstance(e, Var):
        return e.name
    if isinstance(e, Unary):
        if e.op == "not":
            return f"!({render_expr(e.operand)})"
        return f"-{render_expr(e.operand)}"
    if isinstance(e, Binary):
        op = {"and": "&&", "or": "||"}.get(e.op, e.op)
        return f"{render_expr(e.left)} {op} {render_expr(e.right)}"
    if isinstance(e, Indexed):
        return f"{e.base}[{render_expr(e.index)}]"
    if isinstance(e, Paren):
        return f"({render_expr(e.inner)})"
    if isinstance(e, Quant):
        bound = f"{render_expr(e.lo)} <= {e.var} < {render_expr(e.hi)}"
        kw = "forall" if e.kw == "forall" else "exists"
        return f"{kw} {e.var} :: {bound} && ({render_expr(e.body)})"
    if isinstance(e, Call):
        return f"{e.callee}({', '.join(render_expr(a) for a in e.args)})"
    return "?"


def _clause_text(clauses: tuple[Contract, ...], idx: int) -> str:
    if 0 <= idx < len(clauses):
        return render_expr(clauses[idx].expr)
    if clauses:
        return render_expr(clauses[0].expr)
    return ""


def _fn_clause(module: Module, name: str, clause: str, idx: int) -> tuple[str, str]:
    """Look up (clause_kind, clause_text) for a ward0 fn or extern by name."""
    for fn in (*module.fns, *module.externs):
        if fn.name == name:
            if clause == "requires":
                return "requires", _clause_text(fn.requires, idx)
            if clause == "ensures":
                return "ensures", _clause_text(fn.ensures, idx)
            return clause, ""
    return clause, ""


# ---------------------------------------------------------------------------
# translation entry points
# ---------------------------------------------------------------------------


def translate_timeout(detail: str) -> StructuredError | None:
    """A runner timeout detail string -> structured timeout triple."""
    m = _TIMEOUT_RE.search(detail)
    if not m:
        return None
    secs = m.group("secs")
    loc = SurfaceLocation(kind="module", clause="verify")
    return StructuredError(
        kind="timeout",
        location=loc,
        violated_obligation=f"verification timed out after {secs}s (wall-clock budget exceeded)",
        raw=detail.strip().splitlines()[0] if detail.strip() else "",
        surface=f"verification timeout after {secs}s: the proof obligation exceeded its wall-clock budget",
    )


def translate_errors(
    detail: str,
    emitted: str = "",
    module: Module | None = None,
    extern_names: set[str] | None = None,
) -> list[StructuredError]:
    """Translate raw `dafny verify` output into structured (location,
    violated_obligation, counterexample) triples in ward0 surface terms.

    detail: stdout+stderr of `dafny verify` (or a runner timeout string).
    emitted: the emitted Dafny the verifier ran (for the surface line map).
    module: the ward-core IR module (for ward0 clause text).

    Dafny points an `Error:` at the failing *site* (for a postcondition, the
    return path; for a precondition, the call site) and a `Related location:`
    at the actual clause. For clause-bearing kinds we prefer the related
    location's line so the triple names the violated ward0 clause, while the
    primary location keeps the Error line/col.
    """
    to = translate_timeout(detail)
    if to is not None:
        return [to]
    if not emitted:
        return []
    ext_names = extern_names if extern_names is not None else (
        {e.name for e in module.externs} if module else frozenset()
    )
    line_map = EmittedLineMap(emitted, ext_names)
    issues = parse_dafny_output(detail)
    errors = [i for i in issues if i.sev == "Error"]
    related = [i for i in issues if i.sev == "Related location"]
    out: list[StructuredError] = []
    for issue in errors:
        kind = classify(issue.msg)
        loc = line_map.annotate(issue.line)
        # for clause-bearing kinds, prefer the first related location that
        # follows this error (it points at the actual requires/ensures line)
        clause_loc = loc
        if kind in ("postcondition", "precondition", "termination") and related:
            rl = related[0]
            candidate = line_map.annotate(rl.line)
            if candidate.clause in ("requires", "ensures"):
                clause_loc = candidate
                related.pop(0)
        loc = SurfaceLocation(
            fn=clause_loc.fn,
            kind=clause_loc.kind,
            clause=clause_loc.clause,
            clause_idx=clause_loc.clause_idx,
            emitted_line=issue.line,
            emitted_col=issue.col,
        )
        clause_kind, clause_text = (
            _fn_clause(module, loc.fn, loc.clause, loc.clause_idx) if module else (loc.clause, "")
        )
        obligation = _render_obligation(kind, loc, clause_kind, clause_text, issue)
        surface = _render_surface(kind, loc, clause_text, issue)
        out.append(
            StructuredError(
                kind=kind,
                location=loc,
                violated_obligation=obligation,
                counterexample=dict(issue.cex),
                raw=f"{issue.line},{issue.col}: {issue.msg}",
                surface=surface,
            )
        )
    return out


def _render_obligation(kind: str, loc: SurfaceLocation, clause_kind: str, clause_text: str, issue: _RawIssue) -> str:
    if kind == "postcondition":
        return (
            f"postcondition of {loc.fn or '(module)'} — {clause_kind} {clause_text} — "
            f"could not be proved on this return path"
        )
    if kind == "precondition":
        return (
            f"precondition of {loc.fn or '(callee)'} — {clause_kind} {clause_text} — "
            f"could not be proved at this call site"
        )
    if kind == "assertion":
        return f"assertion in {loc.fn or '(module)'} might not hold"
    if kind == "termination":
        return f"termination (decreases) of {loc.fn or '(module)'} could not be proved"
    if kind == "parse":
        return f"syntax error in emitted code: {issue.msg}"
    return f"{loc.fn or '(module)'}: {issue.msg}"


def _render_surface(kind: str, loc: SurfaceLocation, clause_text: str, issue: _RawIssue) -> str:
    cex = ""
    if issue.cex:
        pairs = ", ".join(f"{k}={v}" for k, v in sorted(issue.cex.items()))
        cex = f"; counterexample: {pairs}"
    if kind == "postcondition":
        return (
            f"{loc.fn or '(module)'}: the postcondition ({clause_text or issue.msg}) "
            f"could not be proved on this return path{cex}"
        )
    if kind == "precondition":
        return (
            f"{loc.fn or '(callee)'}: the precondition ({clause_text or issue.msg}) "
            f"could not be proved at this call site{cex}"
        )
    if kind == "assertion":
        return f"{loc.fn or '(module)'}: an assertion might not hold{cex}"
    if kind == "termination":
        return f"{loc.fn or '(module)'}: termination could not be proved{cex}"
    if kind == "parse":
        return f"syntax error: {issue.msg}{cex}"
    return f"{loc.fn or '(module)'}: {issue.msg}{cex}"


# ---------------------------------------------------------------------------
# I1: tightness advisory into the repair loop
# ---------------------------------------------------------------------------


def _weak_clause_targets(entry: dict) -> list[str]:
    """Render the specific weak contract clauses of a fn's tightness entry as
    surface terms the model can act on (I1). Only `ensures` clauses pin output
    entropy, so only those can be weak; unevaluable clauses are never named as
    the fix target (honest bounded-domain limit)."""
    out = []
    for c in entry.get("clauses", []):
        if c.get("kind") != "ensures":
            continue
        if c.get("weak"):
            out.append(f"ensures {c['text']} (tau={c['tau']})")
    return out


def annotate_tightness(
    triples: list[StructuredError],
    tightness: dict[str, dict] | None,
    tau0: float = TAU0,
) -> list[StructuredError]:
    """I1: append the advisory tightness result to the repair-loop triples.

    For every Proven fn whose measured tau < TAU0 (the gate's `demote`
    action), the triples that already name that fn get the tau and the
    specific weak contract clauses appended to their surface; a standalone
    kind="tightness" triple is ADDED for a weak Proven fn that has no error
    triple (e.g. its spec is vacuous so Dafny verified it — the exact case
    the anti-slop instrument exists for).

    Advisory-first: no tier is changed, no error is fabricated, and the
    existing triples keep their kind/location/obligation — the advisory is a
    `tightness_advisory` field plus a surface suffix the model reads.

    tau0 is the fallback threshold for entries that don't record one; the
    elaborator's entries carry the tau0 they were measured with (measure_source
    records it per fn), and that recorded value is read back so the message
    can never misstate the threshold that produced the demote — pipeline ==
    calibration by construction.

    CONTRACT NOTE (semantic change vs raw translate_errors): the returned
    list is NOT "Dafny's errors" — a vacuous Proven fn (ensures true, which
    Dafny verifies cleanly) produces a standalone kind="tightness" advisory
    even when `triples` is empty, because a clean proof of a vacuous spec is
    exactly the anti-slop case this instrument exists for. Callers must read
    `kind` (or `tightness_advisory`) to separate real failures from
    advisories. Posture note: in the elaborator pipeline a fn with no
    `tier:` annotation defaults to Proven (the t-task shape), so a weak
    t-task spec CAN receive an advisory here where the gate runner (JSON
    tiers '?') never demotes — this is the pipeline being stricter, and it
    is intentional: the advisory never changes any tier either way.
    """
    if not tightness:
        return list(triples)
    out: list[StructuredError] = []
    named_fns: set[str] = set()
    for t in triples:
        fn = t.location.fn
        entry = tightness.get(fn)
        named_fns.add(fn)
        if entry is None or entry.get("action") != "demote":
            out.append(t)
            continue
        entry_tau0 = entry.get("tau0", tau0)  # measured threshold, never misstated
        targets = _weak_clause_targets(entry)
        advice = (
            f"I1 tightness: Proven fn '{fn}' scores tau={entry.get('tau')} < TAU0={entry_tau0} — "
            f"the contract is too weak to justify a proof. "
            + (f"Weak clause(s): {', '.join(targets)}. " if targets else "")
            + "Strengthen the ensures clause(s) to pin the output value."
        )
        out.append(
            replace(
                t,
                surface=t.surface + " | " + advice,
                tightness_advisory=advice,
            )
        )
    # a weak Proven fn with NO error triple still gets an advisory triple so
    # the repair loop sees the anti-slop target (vacuous spec verified —
    # Dafny proved `ensures true`, which is exactly what tau=0 catches).
    for fn, entry in tightness.items():
        if fn in named_fns or entry.get("action") != "demote":
            continue
        entry_tau0 = entry.get("tau0", tau0)  # measured threshold, never misstated
        targets = _weak_clause_targets(entry)
        advice = (
            f"I1 tightness: Proven fn '{fn}' scores tau={entry.get('tau')} < TAU0={entry_tau0} — "
            f"the contract verified but is too weak to justify a proof. "
            + (f"Weak clause(s): {', '.join(targets)}. " if targets else "")
            + "Strengthen the ensures clause(s) to pin the output value."
        )
        out.append(
            StructuredError(
                kind="tightness",
                location=SurfaceLocation(fn=fn, kind="fn", clause="contract"),
                violated_obligation=advice,
                surface=advice,
                tightness_advisory=advice,
            )
        )
    return out
