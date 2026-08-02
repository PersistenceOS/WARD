"""ward-core tier routing (R6/T6) + effort metering (R7) — Phase-2 week 3.

Pre-registered scope (files/ward-phase2-scoping.md §2 R6/R7, §4 T6, §6 E5):

- **R6/T6 (routing):** each `Function.tier` (already on the IR node, week 1)
  drives a deterministic per-function verification plan — a semantics of the
  CORE, not harness metadata:

      Proven     -> VERIFY_FULL     (proof required; hard gate, no fallback)
      Contracted -> VERIFY_BOUNDED  (bounded proof search, 30 s) + test fallback
      Tested     -> NO_PROOF        (no proof obligation; runtime checks only)

  The plan is the single source of truth both the emitter (Tested functions
  emit `method {:verify false}` so they never block the module's proof — R10
  allows this because it is the tier's declared semantics, not a wrapper
  cheapening hack) and the runner (verify budget per function) consume.

- **R7 (effort):** solver-seconds per proof obligation. The core owns the
  schema — `EffortRecord` (fn, tier, route, budget_s, actual_s, outcome), with
  `verify_s`-compatible floats (Phase-0/1 harness field unchanged) — and an
  `EffortMeter` the runner fills with real `dafny verify --filter-symbol=<fn>`
  wall-clock per obligation. Tested obligations are never run -> actual_s 0.

- **T6 soundness (cross-tier):** a proof-carrying function (Proven/Contracted)
  must NOT call a Tested function — the caller's proof would rest on an
  unverified callee (the same principle as T4: no un-checked path into a
  proof-carrying function). Hard elaboration error; probe in the E5 gate.

E5 gate (scoping doc §6): a module with one Proven fn + one Tested fn routes
per function — Tested fn never blocks on proof, Proven fn verifies, effort
metered per function. Gate runner: wardcore/e5_gate.py.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from wardcore.ir import Module, Tier, stmt_calls

# Bounded proof search budget for the Contracted tier (T6; scoping doc §4 T6:
# "Contracted -> bounded (30 s) + test fallback"). Proven is a FULL proof
# obligation (the harness's 60 s is a wall-clock safety cap, not a semantic
# budget); Tested never reaches a verify call.
CONTRACTED_VERIFY_LIMIT_S = 30


class VerifyRoute(enum.Enum):
    """Per-function verification route (T6)."""

    VERIFY_FULL = "full"      # Proven: proof required, hard gate
    VERIFY_BOUNDED = "bounded"  # Contracted: bounded search + test fallback
    NO_PROOF = "no_proof"     # Tested: runtime checks only


@dataclass(frozen=True)
class Obligation:
    """One function's routing decision (R6/T6)."""

    fn: str
    tier: Tier
    route: VerifyRoute
    verify_limit_s: int | None  # None = full / never run
    fallback_allowed: bool


@dataclass(frozen=True)
class VerificationPlan:
    """Per-function routing for a whole module (deterministic, from the IR)."""

    obligations: tuple[Obligation, ...]

    def for_fn(self, name: str) -> Obligation:
        for o in self.obligations:
            if o.fn == name:
                return o
        raise KeyError(f"no obligation for function {name!r}")


class TierPass:
    """R6/T6: tier routing as a core pass. `plan` is total and deterministic;
    `validate` enforces the T6 structural obligations; `run` = validate + plan
    (hard error on problems)."""

    def plan(self, module: Module) -> VerificationPlan:
        obligations: list[Obligation] = []
        for fn in module.fns:
            if fn.tier is Tier.PROVEN:
                obligations.append(
                    Obligation(fn.name, fn.tier, VerifyRoute.VERIFY_FULL, None, False)
                )
            elif fn.tier is Tier.CONTRACTED:
                obligations.append(
                    Obligation(
                        fn.name,
                        fn.tier,
                        VerifyRoute.VERIFY_BOUNDED,
                        CONTRACTED_VERIFY_LIMIT_S,
                        True,
                    )
                )
            else:  # Tier.TESTED
                obligations.append(
                    Obligation(fn.name, fn.tier, VerifyRoute.NO_PROOF, None, False)
                )
        return VerificationPlan(tuple(obligations))

    def validate(self, module: Module) -> list[str]:
        """T6 structural obligations checkable at the core level."""
        problems: list[str] = []
        tier_by_fn = {f.name: f.tier for f in module.fns}
        for fn in module.fns:
            # ---- T6 cross-tier: proof-carrying fns must not call Tested ----
            if fn.tier in (Tier.PROVEN, Tier.CONTRACTED):
                for call in _fn_calls(fn):
                    callee_tier = tier_by_fn.get(call.callee)
                    if callee_tier is Tier.TESTED:
                        problems.append(
                            f"{fn.name} ({fn.tier.value}): calls Tested function "
                            f"{call.callee} — a proof-carrying function must not "
                            "depend on an unverified callee (T6)"
                        )
        return problems

    def run(self, module: Module) -> VerificationPlan:
        problems = self.validate(module)
        if problems:
            from wardcore.elaborator import ElaborationError

            raise ElaborationError(
                "; ".join(problems[:5]) + (" ..." if len(problems) > 5 else "")
            )
        return self.plan(module)


# ---------------------------------------------------------------------------
# R7: effort metering (schema + meter; runner fills actual solver seconds)
# ---------------------------------------------------------------------------


@dataclass
class EffortRecord:
    fn: str
    tier: str
    route: str
    budget_s: float | None  # None = full (Proven) or never run (Tested)
    actual_s: float = 0.0
    outcome: str = "not_run"  # not_run | verified | timed_out | failed

    def to_dict(self) -> dict:
        return {
            "fn": self.fn,
            "tier": self.tier,
            "route": self.route,
            "budget_s": self.budget_s,
            "verify_s": round(self.actual_s, 2),  # Phase-0/1 harness field name
            "outcome": self.outcome,
        }


class EffortMeter:
    """R7: records solver-seconds per proof obligation (one per function).

    The runner measures each VERIFY_FULL/VERIFY_BOUNDED obligation with
    `dafny verify --filter-symbol=<fn>` wall-clock and calls `record`. NO_PROOF
    obligations are never run (actual_s stays 0 — exactly the Phase-1 Tested
    behavior: verify_s stays 0).
    """

    def __init__(self, plan: VerificationPlan):
        self._records: dict[str, EffortRecord] = {}
        for o in plan.obligations:
            budget = o.verify_limit_s
            self._records[o.fn] = EffortRecord(
                fn=o.fn,
                tier=o.tier.value,
                route=o.route.value,
                budget_s=budget,
            )

    def record(self, fn: str, actual_s: float, outcome: str = "verified") -> None:
        if fn not in self._records:
            raise KeyError(f"no obligation for function {fn!r}")
        rec = self._records[fn]
        rec.actual_s = actual_s
        rec.outcome = outcome

    def for_fn(self, fn: str) -> EffortRecord:
        return self._records[fn]

    def report(self) -> list[dict]:
        return [r.to_dict() for r in self._records.values()]

    def total_s(self) -> float:
        return sum(r.actual_s for r in self._records.values())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fn_calls(fn) -> list:
    """Every Call node in a function body (used by the T6 cross-tier check)."""
    calls = []
    for st in fn.body.stmts:
        calls.extend(stmt_calls(st))
    return calls
