"""ward-core effects pass (Phase-2 week 4) — T5 as a core pass.

Pre-registered scope (files/ward-phase2-scoping.md §2 R4/T5, §4 T5, §6 E4;
design doc §4f):

- **T5 (effects):** every function's *declared* effect set (net/db/fs/mut/
  partial; async/render deferred) is enforced at elaboration time. Two
  directions, both hard errors:
    1. **escape** — the inferred effect set must stay within the declared set
       (a fn calling a `net` extern without declaring `net` fails elaboration,
       the pre-registered E4 probe);
    2. **unused** — a declared effect must actually be exercised
       (declared-but-unused is an error, the Phase-1 w1 shape already carries
       `effects=frozenset({NET})` on its IR fixture).
- **Inference** is extern-driven (design doc §4f: effect kinds are the FFI
  vocabulary; a fn touches what it calls) and transitive through fn calls
  (module-level call graph, cycle-guarded — T8 already forbids recursion).
- **Week-4 boundary (scoping doc §7 risk row, pre-registered):** "declared-
  implies-inferred check first (simpler than full inference); defer
  bidirectional inference to Phase 2.5". So a function with NO `effects:`
  annotation is unconstrained this week — exactly what keeps E1 byte-parity
  (the 70-reference corpus declares no effects). The E4 gate exercises both
  error directions on DECLARED functions.

E4 gate (scoping doc §6): "A function calling a net extern without declaring
net fails elaboration; declared-but-unused effect fails; correct code passes.
Oracle scenario set covers all five kinds." Gate runner: wardcore/e4_gate.py.
"""

from __future__ import annotations

from wardcore.ir import EffectKind, Module, stmt_calls


class EffectsPass:
    """T5: effect inference + declared-set enforcement as a core pass.

    `infer` computes, per function, the effect set it actually touches
    (extern effects called directly or transitively through fn calls).
    `validate` reports T5 problems on functions that DECLARE effects (both
    directions). `run` = validate (hard error on problems) + return the
    inferred map for the caller (runner/plan exposure).
    """

    # ------------------------------------------------------------- inference

    def infer(self, module: Module) -> dict[str, frozenset[EffectKind]]:
        """Per-function inferred effect set (extern-driven, transitive)."""
        extern_effect = {e.name: e.effect for e in module.externs}
        fn_by_name = {f.name: f for f in module.fns}
        memo: dict[str, frozenset[EffectKind]] = {}
        visiting: set[str] = set()

        def infer_fn(name: str) -> frozenset[EffectKind]:
            if name in memo:
                return memo[name]
            if name in visiting:
                return frozenset()  # cycle guard (T8 forbids recursion anyway)
            visiting.add(name)
            acc: set[EffectKind] = set()
            for call in _fn_calls(fn_by_name[name]):
                if call.callee in extern_effect:
                    acc.add(extern_effect[call.callee])
                elif call.callee in fn_by_name:
                    acc |= infer_fn(call.callee)
            visiting.discard(name)
            memo[name] = frozenset(acc)
            return memo[name]

        return {f.name: infer_fn(f.name) for f in module.fns}

    # ------------------------------------------------------------- T5 checks

    def validate(self, module: Module) -> list[str]:
        """T5 problems: escape + unused, on functions that DECLARE effects."""
        problems: list[str] = []
        inferred = self.infer(module)
        for fn in module.fns:
            declared = fn.effects
            if not declared:
                # undeclared = unconstrained this week (scoping doc §7:
                # declared-implies-inferred check first; bidirectional
                # inference deferred to Phase 2.5). Keeps E1 parity.
                continue
            got = inferred[fn.name]
            escape = got - declared
            unused = declared - got
            # one problem PER KIND so the R8 repair loop gets a single
            # obligation per message (matches ir.py's per-kind T5 wording)
            for eff in sorted(escape, key=lambda e: e.value):
                decl = ", ".join(sorted(e.value for e in declared))
                problems.append(
                    f"{fn.name}: calls undeclared effect {eff.value} (T5) — "
                    f"inferred effects must stay within declared {{{decl}}}"
                )
            for eff in sorted(unused, key=lambda e: e.value):
                problems.append(f"{fn.name}: declared effect {eff.value} is unused (T5)")
        return problems

    def run(self, module: Module) -> dict[str, frozenset[EffectKind]]:
        problems = self.validate(module)
        if problems:
            from wardcore.elaborator import ElaborationError

            raise ElaborationError(
                "; ".join(problems[:5]) + (" ..." if len(problems) > 5 else "")
            )
        return self.infer(module)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

EFFECT_NAMES = {e.value for e in EffectKind}


def parse_effect_set(text: str) -> frozenset[EffectKind]:
    """Parse an `effects: net, db` annotation value into a frozenset.

    Unknown names are a hard ElaborationError (never a silent choice — design
    doc §5): the vocabulary is exactly net|db|fs|mut|partial in v0.1.
    """
    out: set[EffectKind] = set()
    for part in text.split(","):
        name = part.strip()
        if not name:
            continue
        if name not in EFFECT_NAMES:
            from wardcore.elaborator import ElaborationError

            raise ElaborationError(
                f"unknown effect kind {name!r} (expected net|db|fs|mut|partial)"
            )
        out.add(EffectKind(name))
    return frozenset(out)


def _fn_calls(fn) -> list:
    """Every Call node in a function body (extern + fn callees)."""
    calls = []
    for st in fn.body.stmts:
        calls.extend(stmt_calls(st))
    return calls
