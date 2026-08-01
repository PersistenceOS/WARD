# Ward Phase 0 — Consolidated Results & Go/No-Go

**Date:** 2026-08-01
**Source of truth:** `phase0/README.md` (mechanisms, per-cell tables), run logs in
`phase0/experiments/runs/*.jsonl`, pre-registered gates in
`../files/ward-phase0-solo-execution-plan.md` §8.
**Headline:** **Conditional GO for Phase 1 — with the thesis revised.** The
surface-syntax claim (A) is **falsified on phase-0 tasks** (exact tie at pass@1,
McNemar p = 1.0000). The FFI-boundary claim (B) and the Phase-1 generated
enforcement pivot are **validated**. Phase 1 must build on the boundary/tiered
verification story, not on "ward0 beats Dafny at pass rate."

---

## 1. What was tested

Three claims, per the adapted solo plan (`../files/ward-phase0-solo-execution-plan.md`):

- **A (surface-syntax hypothesis):** a Python/TS-shaped surface syntax (`ward0`)
  that deterministically elaborates into Dafny outperforms generating Dafny directly.
- **B (FFI boundary hypothesis):** a contract-stub trust boundary catches more
  caller errors at library call sites than unverified direct calls.
- **Phase-1 pivot (generated enforcement):** runtime contract checking is a
  *generated obligation* of the toolchain (a `_checked` wrapper around every
  `extern` call), independent of what the model writes.

Toolchain: Dafny 4.11.0 + Z3 4.12.1, ward0 grammar v0.1 (lark PEG), ward0→Dafny
transpiler, harness (generate → transpile → `dafny verify` → hidden tests),
62-task benchmark (24/24/14 across tiers 1–3), 6 B-scenarios. Model:
`opencode/deepseek-v4-flash-free` (OpenCodeModel).

## 2. Toolchain status (all suites green, re-run 2026-08-01)

| Suite | Result |
|---|---|
| grammar | 20/20 |
| transpiler (incl. live Dafny verification, enforcement wrappers) | 24/24 |
| harness (incl. live end-to-end eval 4/4, pass@1 = 1.000) | 13/13 |
| **Total** | **57/57** |

Oracle (reference solutions): **62/62 pass@1** — benchmark and hidden tests are sound.

## 3. Gate-by-gate evaluation (pre-registered §8 criteria)

| Gate | Threshold (adapted) | Result | Verdict |
|---|---|---|---|
| **A1** | Positive direction on pass@5 across ≥ 2 of 3 tiers; CI reported honestly | pass@1 exactly tied (54/62 both arms); pass@2 **ward0 60/62 vs Dafny 61/62**; McNemar p = 1.0000 both metrics | **FAIL** (direction neutral-to-negative) |
| **A2** | Ward-Surface syntax-error rate < 2% (transpiler-level) | attempt-1 `transpile_error` 4/62 = **6.5%** (3.25× the threshold); all recovered by retry; note: constrained decoding not implemented — fallback post-hoc validation + 1 retry (plan §7 allowed fallback) | **FAIL** |
| **B1** | ≥ 30% relative reduction in contract-violation escape rate | **75%** (trust 2/8 vs baseline 8/8) | **PASS** |
| **B2** | ≤ 15% false positives (valid callers rejected) | **0/6** both arms | **PASS** |
| **Phase-1 enforcement** | (new, post-B pivot) 0 boundary leaks, oracle clean | 0/8 leaks naive + model callers; 6/6 solved; 0/6 rejections; vacuous-control confirmed | **PASS** |

**Decision rule invoked (§8):** *"A1 fails → core hypothesis (surface syntax
helps) falsified → halt or pivot."* → **Pivot.** Combined with B1/B2 and the
enforcement pivot passing, this is a GO for Phase 1 on a revised thesis, not a halt.

## 4. Experiment A — surface syntax vs raw Dafny (62 tasks, attempts = 2)

| metric | ward0 arm | raw-Dafny arm |
|---|---|---|
| pass@1 | 54/62 (0.871) | 54/62 (0.871) |
| pass@2 | 60/62 (0.968) | 61/62 (0.984) |
| avg seconds/task | ~30 | ~50 |

Paired analysis (McNemar on discordant pairs, computed 2026-08-01 from logs):

- **pass@1:** concordant 47+1; discordant ward0-only 7, dafny-only 7; **p = 1.0000**.
- **pass@2:** concordant 59+0; discordant ward0-only 1, dafny-only 2; **p = 1.0000**.

Per-tier: t1 24/24 both; t2 ward0 22/24 vs Dafny 23/24 (ward0 lost t2_gcd to a
model timeout; t2_product_list weak invariant); t3 14/14 both. ward0's only
advantages: faster per-task (~30s vs ~50s) and cleaner syntax-error recovery.

**Finding: on phase-0's small pure-function tasks, ward0 shows no pass-rate
advantage over direct Dafny — a null result.** These tasks are Dafny's home
turf (small math/contract functions), so the surface-syntax hypothesis as
originally stated is not supported by this evidence. The A null result must be
reported honestly (it is — this section, and the plan's §3.1 re-interpretation
of A1 as directional evidence makes the goalpost fixed in advance).

## 5. Experiment B — contract-stub trust boundary (6 scenarios, 3/6 buggy stubs)

Pre-registered gates **both met**, oracle sanity clean in both arms (6/6, 0/8 escapes).

- **B1:** violation escapes **2/8 (25%) trust vs 8/8 (100%) baseline → 75%
  relative reduction** (gate ≥ 30%). Trust-arm escapes all from b5 (model
  short-circuited the contract guard without calling the stub).
- **B2:** **0/6 valid callers rejected** in both arms (gate ≤ 15%).
- Verification rate trust 6/6 vs baseline 5/6; hidden-test pass rate 5/6 vs 2/6.

Mechanism note: the trust arm's advantage is *not automatic* — models must still
write defensive boundary checks; the contract is what makes those checks
provable. This is exactly the residual-obligation problem Phase 1 removed.

## 6. Phase-1 pivot — generated boundary enforcement

- **Deterministic (naive pass-through callers, zero defense):** 8/8 boundary
  leaks → **0/8** with enforcement; all tests pass.
- **Real model 2×2 (buggy scenarios, 1 attempt):**

| cell | solved | boundary leaks /8 |
|---|---|---|
| trust, enforce off | 2/3 | 3/8 |
| trust, enforce on | **3/3** | **0/8** |
| baseline, enforce off (phase-0) | 0/3 | 8/8 |
| baseline, enforce on (control) | 0/3 | 5/8 |

(The enforce-off cells were re-measured with the refined OKLEAK marker, hence 3/8 vs the phase-0 escape count of 2/8 in §5 — same arm, different measurement epoch.)

- **Full trust+enforce 6-scenario set: 6/6 solved, 0/8 leaks, 0/6 rejections.**
- Control cell is the key negative: without a contract the wrapper is vacuous —
  enforcement's power comes from the contract, not the wrapper.
- Key finding: model-written defense *conflicts* with the wrapper (re-labeling
  `Err("contract violation")` as genuine library errors); the pass-through steer
  (`return stripe_charge(amount, token);`) resolves it — callers then emit
  minimal correct code and everything passes.

**Verdict:** enforcement as a generated obligation is validated; 0 boundary
leaks across naive and model callers; oracle clean; unit-tested wrapper
generation. The extern-call rule is now design-doc law (§4c.1, tiered
verification) — toolchain obligation, never model convention.

## 7. Revised thesis for Phase 1

Phase-0 evidence supports GO for Phase 1 **only** on the boundary/tier story:

1. **DROP** the claim "ward0's surface syntax makes AI code verify better than
   Dafny" — falsified on phase-0 tasks (p = 1.0, tied pass@1).
2. **KEEP/EXTEND** the validated core: verified contracts at FFI boundaries,
   generated runtime enforcement (model-independent), tiered verification
   (Tested/Contracted/Proven, §4c) for full-stack code where Dafny's documented
   pain point (unbounded, unpredictable verification effort) actually bites.
3. Phase-1 experiments must test the differentiators on **mid-size realistic
   code with tiers + extern boundaries** — not more small math functions.
4. Residual caller obligations (discharge `requires`, pass results through)
   stay in the prompt contract and are documented as model requirements.

## 8. Threats to validity (honest caveats)

- **Single weak model** (`deepseek-v4-flash-free`); no second-model cross-check
  (plan wanted both models — budget/availability cut it).
- **62 tasks, attempts=2** — under the plan's preferred pass@5/power target;
  small N ⇒ large CIs; we report the paired p-value as required (1.0).
- **A's task set doesn't exercise tiers/boundaries** — so A's null does not
  falsify the *full* Ward thesis, only the surface-syntax superiority sub-claim
  on that task distribution.
- **Constrained decoding not implemented** — A2 measured on the retry fallback
  (6.5% attempt-1 transpile errors, all recovered).
- **Enforcement "leaks = 0" is relative to the contract** — a wrong/weak
  contract enforces the wrong thing (vacuous-control cell demonstrates).
- FABLE-5 trace corpus: analyzed (`experiments/fable_report.json`) — usable only
  as optional Phase-5 seed material (see plan §10), zero ward0/Dafny content.

## 9. Decision

**GO — Phase 1 (pivoted).** Proceed to build the boundary/tiered-verification
layer (extern-call rule §4c.1 as generated obligation, tiered verification for
full-stack code), with experiments designed to test *that* claim. Do not
advertise a surface-syntax pass-rate advantage over Dafny — phase 0 ruled it out
on the evidence available. The Phase-1 falsification round is designed in
`../files/ward-phase1-experiment-design.md` (claims C1 cost-containment, C2
boundary-at-scale, C3 converged-accuracy-at-lower-cost; pre-registered gates;
8 w-task module scenarios). On the Phase-0.5 Verus spike: the plan's pre-registered
trigger was *"if A1 shows positive signal"* (§4, week 13–14) — A1 was negative, so
the trigger is not met; the spike happens only if a Rust/Verus target becomes the
priority by explicit decision, otherwise extend the Dafny toolchain.

**Deliverables closed by this report:** plan §5 items 4 (technical report incl.
negative results) and 5 (go/no-go decision document).
