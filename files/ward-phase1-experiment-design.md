# Ward Phase 1 — Experiment Design (post-pivot falsification round)

**Status:** benchmark v1 authored and oracle-validated (8/8 w-tasks solved in all
four arms, 0/20 boundary leaks, 57/57 harness suite green; Contracted fallback
gate proven by negative-oracle probe). **Real-model W-arm smoke: 3/3 solved
(w1 Proven, w3 Contracted, w6 Tested), 0/8 boundary leaks, 0/8 escapes, all on
attempt 1, 3 s total verification** (log: `phase0/experiments/runs/phase1_smoke_W.jsonl`).
Full four-arm + 8-task runs pending. Follows the Phase-0 go/no-go
(`../phase0/PHASE0_REPORT.md`): A (surface-syntax superiority) **falsified**;
B (trust boundary) and the Phase-1 generated-enforcement pivot **validated**.
**Decision from Phase 0:** *conditional GO for Phase 1 on the boundary /
tiered-verification thesis — the surface-syntax claim is dropped.*

This document designs the falsification round for the *surviving* thesis. It is
deliberately the same shape as Phase 0: cheap, relative, pre-registered gates,
run by one engineer on one machine, before any language construction begins.

**Date:** 2026-08-01

---

## 1. Thesis under test (revised, per Phase-0 verdict)

Ward's differentiation is **not** "surface syntax beats Dafny at pass rate"
(falsified: exact pass@1 tie, McNemar p = 1.0000). The surviving thesis is:

> **On mid-size, realistic, full-stack-shaped code, a toolchain that (a) tiered
> verification (Tested / Contracted / Proven, design doc §4c) to bound
> verification effort, and (b) enforces every extern boundary with a generated
> runtime contract-check wrapper (§4c.1), converges verified programs at least
> as reliably as raw Dafny while spending materially less verification effort —
> and never lets a contract-violating library call cross the boundary as `Ok`.**

Three falsifiable claims, each with its own gate (§5):

- **C1 — Cost containment.** Tiered verification makes verification effort
  *predictable and bounded*: Proven-tier code verifies fully; Contracted-tier
  proof search is bounded (timeout falls back to test confidence, not a hard
  failure); Tested-tier never blocks on a proof obligation at all.
- **C2 — Boundary scales.** The generated `_checked` wrapper holds **0 boundary
  leaks** on mid-size modules with multiple externs and multi-hop call chains —
  not just the single-call scenarios of Phase 0 — across naive, oracle, and
  real-model callers.
- **C3 — Converged accuracy at lower cost.** On the same tasks, ward0 +
  enforcement + tiers converges ≥ as many tasks as raw Dafny (non-inferiority),
  while total verification effort is materially lower (the §4c cost-containment
  claim quantified).

**The null that falsifies the thesis:** C1 or C2 fails (tiers don't bound
effort / the wrapper leaks at scale), or C3 shows parity on accuracy *without*
an effort win (the machinery buys nothing measurable). Decision rules in §5.

## 2. Why mid-size modules (not more small functions)

Phase 0's 62 tasks were small pure functions — Dafny's home turf — where tiers
and boundaries have no purchase. The full-stack-shaped code Ward targets is
dominated by: multi-extern call chains (auth → rate-limit → db → charge),
invariant-bearing state transitions (transfer, order placement), and glue that
should be Tested-tier (routing, shaping). Phase 1 tests exactly that shape,
bounded by what the ward0 subset (v0.1) can express: pure functions, bounded
loops, `int/bool/str/Unit/Result<T,E>/List<T>`, `extern fn` + `enforce_boundary`.
No classes, no heap, no recursion — so "realistic" here means *multi-function
orchestration over extern boundaries*, the pattern B validated, at greater scale
and composition.

## 3. Benchmark: the w-task set (mid-size module scenarios)

Extend the existing `benchmarks/b_tasks/` format (JSON descriptor + `.ward0`
reference + Python stub impl + hidden tests with `violation: true` markers).
New w-tasks are **modules**: several functions, ≥ 2 externs, cross-function
contracts.

As-built 8 scenarios (subset of Phase-0 b-scenarios scaled up + new ones).
**Pre-registration note:** the as-built set exercises the *expressible subset*
contingency (risk row 1): each module is a single entry `fn` orchestrating ≥ 2
extern calls, with ONE tier per module carried by the descriptor — the
per-function helper-tier schema below was dropped (see schema note). The tier
gates are exercised by descriptor allocation: w3=Contracted, w6=Tested, the
rest Proven. This deviation from the original proposal table (which had
per-function tiers inside modules) is the C-adjacent finding the risk table
pre-registered: ward0 v0.1 expresses multi-extern orchestration, not
multi-function modules with per-function tier routing.

| id | Shape (entry fn → extern calls) | Externs | Tier (descriptor) |
|---|---|---|---|
| w1 | Payment: `pay` → `auth_check` → `rate_limit` → `stripe_charge` chain | 3 | Proven |
| w2 | Two-account ledger: `transfer` → `ledger_debit` → `ledger_credit` | 2 | Proven |
| w3 | Session/OTP: `login` → `session_valid` → `otp_check` | 2 | Contracted |
| w4 | Order placement: `place_order` → `stock_check` → `inventory_reserve` | 2 | Proven |
| w5 | Currency round-trip: `round_trip` → `fx_convert` (pair 1 USD→EUR, pair 2 EUR→USD) | 1 (two calls) | Proven |
| w6 | CRUD handler: `crud_op` → `db_get` / `db_put` dispatch | 2 | Tested |
| w7 | Idempotency: `charge_idempotent` → `dedup_lookup` (value-encoded status) → `gateway_charge` | 2 | Proven |
| w8 | Multi-currency ledger: `convert_transfer` → `fx_rate` → `ledger_debit` | 2 | Proven |

**Descriptor schema change (pre-registered, as built):** the Phase-0 `b_task`
format has a single `extern` object and a single `fn` entry point. w-tasks grow
explicitly: `extern` → **array** (each stub with its own
`contract`/`impl`/`contract_py`/`violation_probes`), plus `tiers: {fn: tier}`
carrying ONE tier per module (single entry `fn`; the originally proposed
per-function helper-tier list was dropped to the expressible subset — see §3
note). This is a benchmark-format extension, not a ward0 grammar change; both
are frozen before model runs.

Each scenario:
- **Buggy stubs in ~50% of externs** (over-grant on a designed input region),
  flagged hidden tests as `violation: true` — identical to Phase-0 B design.
- **Tier annotations** fixed in the descriptor (per-function `tier` field), NOT
  chosen by the model — this isolates the toolchain claim from model judgment.
- **Cross-function contracts** (e.g., `pay` ensures `is_ok == (amount <= limit)`
  composed through `auth`), so multi-hop reasoning is required — the step up
  from Phase-0 B's single call site.
- Hidden-test markers `PASS | OKLEAK | ERRFAIL` carried over (Phase 1's
  refinement), `boundary_okleak` as the B1-equivalent metric.

## 4. Arms and protocol (relative comparison, one model)

Pre-registered arms — the *only* difference between arms is the toolchain
feature set, exactly as Phase 0 B did:

| Arm | ward0 transpile | enforce | tiers | What it isolates |
|---|---|---|---|---|
| **W** | yes | **on** | **on** | The treatment: full Phase-1 toolchain |
| **D** | no (raw Dafny) | n/a | n/a | Competitor baseline (Direct-Dafny arm of Phase-0 A, scaled to w-tasks) |
| **W−enforce** | yes | off | on | Isolates the boundary contribution at scale (C2) |
| **W−tiers** | yes | on | off (all Proven) | Isolates the cost-containment contribution (C1) |

Plus **oracle sanity** (reference solutions, both arms' toolchains): every
w-task reference must pass in both W and D pipelines, and stubs must violate
exactly the flagged cases — same pre-run validation as Phase 0.

Protocol (mirrors Phase 0):
- Model: `opencode/deepseek-v4-flash-free` (same as Phase 0 for comparability);
  second model only if budget/time allows (Phase-0 plan §6 cross-check role).
- attempts = 2 per task (Phase-0 protocol; pass@1 and pass@2 reported).
- Per-task record: status (pass / transpile_error / verify_fail / test_fail /
  model_error), verify seconds, tests passed/total, `boundary_okleak`,
  per-function tier, and — new — **verify effort per tier** (solver seconds).
- Logs: `phase0/experiments/runs/phase1_w*.jsonl` etc., same schema family as
  Phase 0 so the analysis tooling reuses.

## 5. Pre-registered gates (adapted, same spirit as Phase-0 §8)

| Gate | Claim | Threshold | Measured on |
|---|---|---|---|
| **C1a** | Proven tier verifies | 100% of Proven-tier oracle references verify; model-written Proven functions verify at ≥ Phase-0 rate | w-tasks, W arm |
| **C1b** | Contracted tier bounds effort | verify-time p90 ≤ hard timeout (60 s) with fallback-to-tests rather than hard failure; the 60 s timeout never blocks a pass in the oracle (oracle must pass in Contracted mode before model runs) | W vs W−tiers |
| **C1c** | Tested tier never blocks | Tested-tier functions reach hidden tests with zero proof obligation; 0 hard failures from Tested-tier | W arm |
| **C2** | Boundary holds at scale | **0 `boundary_okleak`** across all enforce-on runs (W, W−tiers) on buggy scenarios; oracle clean in both enforce arms. (The W−enforce delta is reported as context, not a separate threshold — 0-leak is the binding gate.) | W vs W−enforce, buggy scenarios |
| **C3a** | Non-inferior accuracy | W pass@2 ≥ D pass@2 − 1 task (non-inferiority margin of one task on 8); report McNemar + CI honestly | W vs D |
| **C3b** | Effort advantage | Total verification effort (solver seconds) W ≤ 0.7 × D, or per-task median time W ≤ 0.7 × D | W vs D |

**Decision rules (pre-registered, from Phase-0 §8 style):**
- C1a/b/c any fail → tiered verification does not contain cost as designed →
  **halt or redesign tiers** before any Phase-2 commitment.
- C2 fail → generated enforcement insufficient at module scale → **halt or
  redesign the extern-call rule** (§4c.1).
- C3a pass + C3b pass → surviving thesis validated → **GO for Phase-2 scoping**
  (core calculus + elaborator per design doc §8, phases 1–2).
- C3a pass + C3b fail → machinery correct but no measured advantage on this
  model/task set → **conditional GO with a smaller Phase-2, or a second-model
  rerun before committing**.
- C3a fail (D beats W on accuracy) → the combination loses to Dafny even with
  enforcement → **halt** (consistent with Phase-0 A null: the surface/elaboration
  path shows no edge on this evidence).

## 6. Toolchain work required (small, incremental — most already exists)

1. **Multi-extern modules in the transpiler** — verify `enforce_boundary`
   generates per-extern wrappers when a single function calls ≥ 2 externs
   (Phase 0 only tested one extern per scenario; the mechanism is per-extern so
   this is expected to be near-zero work — a test, not a feature).
2. **Tier annotation plumbing** — carry a per-function `tier` from the JSON
   descriptor into the harness: Proven = current behavior; Contracted = bounded
   proof search with timeout → fall back to test-based confidence; Tested =
   skip verification, run hidden tests directly.
   **Mechanism — CONFIRMED by prototype (2026-08-01, `phase0/experiments/forcecompile/`):**
   Dafny only compiles after successful verification, but `dafny translate py
   --no-verify` (and `dafny run --no-verify`) **skip verification entirely and
   produce runnable Python even when verification would fail** (verified
   empirically: an unprovable `ensures` program translates with exit 0 under
   `--no-verify` vs exit 4 without). The `{:extern}{:axiom}` stub + `_checked`
   wrapper shape verifies clean, translates under `--no-verify`, and — with the
   stub injected the way `_stub_injection` does — **the runtime enforcement
   firing is demonstrated end-to-end**: a buggy stub over-granting `Ok` at
   amount=110 (contract says `Err` for > 100) is converted by the wrapper to
   `Err("contract violation")` in the compiled, running program.
   **Residual harness work:** tiers must flow descriptor → driver → runner
   (branch verify / `--no-verify` at the two translate sites, per-tier
   `--verification-time-limit`), plus oracle-validating Tested/Contracted
   hidden-test runs — small and localized, but a coordinated change across
   `dafny_runner.py` and the eval drivers, not a new compile path.
3. **Effort metering** — record solver seconds per tier (small harness addition,
   mirrors Phase-0 timing fields).
4. **w-task authoring** — 8 descriptors + references + stubs + hidden tests;
   oracle sanity before model runs (Phase-0 lesson: cross-check references first).
5. **Finding (2026-08-01): contracts take no trailing semicolon — guide fix with
   before/after evidence.** ward0's grammar is `(contract)* block`: a contract
   line is followed directly by the body `{`, never a `;`. The first real-model
   W-arm smoke exposed this: **w3 (Contracted) attempt 1** = `transpile_error`
   because the model wrote `ensures is_ok(result) == (...);` with a trailing
   semicolon — over-applying the guide's "semicolons after every statement"
   rule to contracts (attempt 2 failed separately by calling externs *inside*
   `ensures`, which Dafny rejects: "expression is not allowed to invoke a
   method" — extern calls are methods, not functions, and cannot appear in
   expressions). **Fix:** `WARD0_GUIDE` (phase0/harness/models.py) now states
   contracts are NOT statements and take NO trailing semicolon; the
   "semicolons after every statement" line scopes itself to body statements.
   **Before/after:** w3 went from 2/2 failed attempts to solved on attempt 1;
   the W arm went 2/3 → 3/3; boundary leaks were 0/8 in both runs (the first
   run's escapes 3/8 were w3's unmeasurable violation cases; the rerun had
   escapes 0/8). Same round, two harness fixes that unblocked real-model runs:
   the
   `opencode run` subprocess hung on an open stdin pipe (observed as 2 × 900 s
   timeouts on a single cell) — fixed with `stdin=subprocess.DEVNULL` — and
   the per-call model timeout default was lowered 900 s → 180 s so hangs fail
   fast (normal generations measure 10–22 s).

No changes to the grammar, the ward0 subset, or the Dafny backend are expected.
If multi-extern or tier plumbing turns out to need grammar changes, that is
itself a Phase-1 finding (surface too small for full-stack-shaped code) and
must be logged as such.

## 7. Risks (solo, one machine — same risk posture as Phase 0)

| Risk | Severity | Mitigation |
|---|---|---|
| ward0 subset can't express the intended w-shapes without contortions | High | Pilot 2 w-tasks by hand first (w1, w2); if contorted, drop to the expressible subset and log the limitation as a C-adjacent finding |
| Dafny won't compile unverified code → Tested/Contracted tiers unbuildable | Medium → **resolved** | Prototype confirmed `dafny translate py --no-verify` skips verification and emits runnable Python (exit 0) even for unprovable programs; the extern `{:extern}{:axiom}` + `_checked` wrapper shape verifies clean, and the runtime over-grant → `Err("contract violation")` conversion is demonstrated end-to-end under `--no-verify`. Remaining work: tier plumbing through the harness + oracle validation |
| 8 tasks too few for stable pass-rate deltas | Medium | Gates are non-inferiority margins + effort ratios, not significance; McNemar reported honestly; CI reported |
| Contracted-tier timeout → test-fallback could mask real failures | Medium | Oracle sanity must pass in Contracted mode before model runs; fallback only skips proof, never tests |
| Single model | Medium | Same model as Phase 0 for comparability; second-model rerun only if a gate lands within margin |
| Tier annotations in the descriptor leak the "right" tier to the model | Low | Tiers are toolchain metadata, not in the prompt; spec prose identical across arms |

## 8. Timeline (solo, ~3–4 weeks)

| Week | Work | Deliverable |
|---|---|---|
| 1 | Pilot w1/w2 by hand (both arms), oracle sanity; fix transpiler/harness gaps found | 2 verified w-tasks |
| 2 | Author all 8 w-tasks + stubs + hidden tests; oracle sanity full set | w-task set v1 |
| 3 | Tier plumbing + effort metering; run W and D arms | Phase-1 dataset (W, D) |
| 4 | Run W−enforce and W−tiers; analysis (gates C1–C3); write-up | `PHASE1_REPORT.md` + decision |

Buffer: one extra week if toolchain gaps surface (the honest Phase-0 lesson:
measurement refinement takes longer than expected).

## 9. What this does NOT test (explicit non-claims)

- Not the full Ward architecture (core calculus, effects, elaborator, content-
  addressed library, repair loop) — those are Phase-2+ scoping, gated on this
  round.
- Not "ward0 syntax is better for models" — that claim is falsified in Phase 0
  and deliberately excluded.
- Not real full-stack code with heap/async/UI — the ward0 subset cannot express
  it; this round tests the *orchestration-and-boundary* pattern at module scale,
  which is the validated-B mechanism extended, nothing more.
- Not an answer to the build-vs-extend question (new language vs Verus
  extension, design doc §2a) — that decision is explicitly deferred until this
  round's outcome.

---

*Companion docs: `../phase0/PHASE0_REPORT.md` (phase-0 verdict), `../phase0/README.md`
(phase-0 mechanics), `./ward-language-design.md` §4c/§4c.1/§8 (design context),
`./ward-phase0-solo-execution-plan.md` §8 (pre-registered phase-0 gates this
round extends).*
