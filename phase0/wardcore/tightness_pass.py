"""TightnessPass — advisory Specification Tightness (I1) pass, wired into the
phase-2 elaborator pipeline (theory instrument I1, pre-registered in
files/ward-phase2-scoping.md section 10).

ADVISORY-FIRST by design: the pass measures tau for every generated ward0 fn
but NEVER blocks and NEVER changes a declared tier. A Proven-tier fn scoring
below TAU0 gets a *recommended* demote (recorded on the elaborator's
`tightness` map and in the certificate's per-fn tau fields) — the repair
loop or human decides. unevaluable specs (quantifiers/len/unsupported ret)
are flagged, never treated as failures (the honest bounded-domain limit).

Consistency guarantee: the pass measures through parse_ward0_fns on the
SURFACE source — the exact parser the calibration probe and gate runner use
— so pipeline tau == calibrated tau for the same fn by construction. There is
no IR->surface round-trip that could drift (e.g. `is_ok(result)` must stay
`is_ok(result)`, never `result.Ok?`).
"""

from __future__ import annotations

from wardcore.ir import Module
from wardcore.tightness import clause_tightness, parse_ward0_fns
from wardcore.tightness_gate import TAU0, TightnessGate


def measure_source(
    source: str,
    tiers: dict[str, str] | None = None,
    tau0: float = TAU0,
) -> dict[str, dict]:
    """Advisory tau for every program fn in a ward0 source.

    tiers: {fn_name: tier_str} — only these fns are measured (the elaborator
    passes the module's fns; the certificate passes the task JSON tiers).
    Fns parsed from the source that are not listed are skipped — the surface
    parser's `_FN_RE` also matches the `fn` inside `extern fn` stubs, and
    externs are not program fns. tiers=None measures every parsed fn as
    Proven (gate-runner posture).

    Returns {fn_name: GateResult.to_dict() plus a per-clause `clauses` list
    (the I1 repair-loop target: which specific contract clauses pin the
    output) } — never raises, never blocks.
    """
    gate = TightnessGate(tau0=tau0)
    out: dict[str, dict] = {}
    for sf in parse_ward0_fns(source):
        if tiers is not None and sf["name"] not in tiers:
            continue  # extern stub matched by the surface parser — not a fn
        tier = tiers.get(sf["name"], "Proven") if tiers else "Proven"
        d = gate.check(sf, tier).to_dict()
        # I1: the specific weak clauses the repair loop should strengthen —
        # per-clause tau measured in isolation (requires kept), so diagnose()
        # can name the exact contract line to fix, not just the fn.
        d["clauses"] = clause_tightness(
            sf["params"], sf["ret"], sf["requires"], sf["ensures"], tau0
        )
        # record the tau0 this dict was measured with so the advisory message
        # can never misstate the threshold that produced a demote decision
        d["tau0"] = tau0
        out[sf["name"]] = d
    return out


class TightnessPass:
    """I1 advisory pass — the pipeline entry point.

    run(module, source) measures every fn in the module: tiers come from the
    IR (the declared tier is read, never written), the surface contract comes
    from the source parse. The elaborator exposes the result as
    `elab.tightness`; the certificate records it per fn (tau / tau_advisory /
    tau_unevaluable). Advisory means advisory: no tier is ever changed and no
    elaboration is ever blocked by this pass.
    """

    def __init__(self, tau0: float = TAU0):
        self.tau0 = tau0
        self.gate = TightnessGate(tau0=tau0)

    def run(self, module: Module, source: str) -> dict[str, dict]:
        tiers = {f.name: f.tier.value for f in module.fns}
        return measure_source(source, tiers, tau0=self.tau0)
