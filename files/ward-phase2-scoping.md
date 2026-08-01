# Ward Phase 2 — Core Calculus + Elaborator Scoping (design doc §8, phases 1–2)

**Status:** Scoping draft, GO-**conditional** per `files/PHASE1_REPORT.md` (full
four-arm dataset now measured: W 8/8 vs D 6/8 accuracy, 0/20 boundary leaks,
but effort ratio 0.77 live / 1.15 controlled re-measurement — C3b fails the
pre-registered 0.7× gate → conditional GO per the mechanical decision rule,
with the effort claim scoped down; C3b follow-up (Finding 7) shows the gap is
100% wrapper overhead (~15% fixed per-method), W− == D at parity, and the
wrapper cannot be cheapened within Dafny's guard rails). This
document converts the Phase-1 experimental findings into *requirements* for the
first real language construction milestone, and pre-registers gates for it in
the same style as Phase 0 and Phase 1 (cheap, relative, solo-runnable).
**"Smaller Phase-2" = smaller *effort claim*, unchanged scope** — the milestone
list (weeks 1–8, gates E1–E8) is not reduced; only the effort-ratio claim is
restricted to tasks with real proof obligations (see E7 row).
**Date:** 2026-08-01
**Week-0 (R5) status: COMPLETE (2026-08-01).** Bounded-everything landed: new
`phase0/harness/wallclock.py` (`run_capped` — subprocess-tree wall-clock kill
via `taskkill /F /T` on Windows / `killpg(SIGKILL)` on POSIX, with a capped
post-kill drain so it can never hang), wired into `OpenCodeModel.generate` and
all six `subprocess.run` sites in `dafny_runner.py` (verify ×1, verify_dafny
×1, _run_compiled translate+run, _run_compiled_b translate+run); a verify
timeout returns `(False, detail)` so the Contracted-tier test fallback still
runs; soak test
`phase0/harness/test_wallclock.py` (7 tests) proves the cap fires and kills the
whole tree including a grandchild holding the stdout pipe — the exact 14,418 s
hang scenario — plus an end-to-end `generate()` regression test (the
`capture_output=True` requirement was caught by review and locked in by test).
Suites green: transpiler 24/24, harness 20/20, grammar 20/20. Effective bound
per call is cap + grace (e.g. 180 + 10 s).Next: ward-core IR v0.1 data model (week 1).
**Week-1 (ward-core IR v0.1) status: COMPLETE (2026-08-01).** New
`phase0/wardcore/` package: `ir.py` (the typed core IR — Tier/EffectKind enums,
Type + TInt/TBool/TStr/TUnit/TResult/TList, Expr hierarchy with Call.checked,
Block/Stmt (bounded Loop only, no while), Contract as an annotation node never
a statement, Param.linear, per-fn Function tier+effects, ExternFn with
mandatory contract+trust, Module with deps) and `validate_module()` encoding
the checkable T1–T8 structural obligations (contract purity, extern
contract/trust mandatory, checked routing, declared-but-unused effects,
linear-param-used, no recursion, duplicates). `test_ir.py` (15 tests) grounded
in the real w1_payment_chain shape (3 externs, Proven entry fn, net effects) +
a linear-money transfer. Suites green: wardcore 15/15, harness 20/20,
transpiler 24/24, grammar 20/20. **Full-8 W arm COMPLETE on the hardened
harness** (log: `phase0/experiments/runs/phase1_W_full8.jsonl`): **8/8 solved,
0/20 boundary leaks, 0 escapes, 0 fallbacks** — closes the last pre-registered
Phase-1 cell; effort ratio 0.77 total / 0.94 median (C3b fails ≤ 0.7 → GO
conditional, see `PHASE1_REPORT.md` §6). Next: typed elaborator front-end
(week 2, gates E1/E2).
**Companion docs:** `../files/ward-language-design.md` (§4 core calculus, §4c
tiers, §4c.1 extern-call rule, §5 elaboration, §8 what-it-takes),
`../files/PHASE1_REPORT.md` (decision + findings),
`../files/ward-phase1-experiment-design.md` (pre-registration this builds on).

---

## 1. Decision being operationalized

Phase 1 verdict: **GO (conditional) for Phase-2 scoping** — C1/C2/C3a pass on
the complete four-arm dataset (W 8/8 vs D 6/8, 0/20 boundary leaks on
enforce-on arms), C3b fails (effort ratio 0.77 total / 0.94 median vs the
pre-registered ≤ 0.7 gate) → the pre-registered rule fires: *"C3a pass + C3b
fail → conditional GO with a smaller Phase-2, or a second-model rerun before
committing."* No cell remains unmeasured. **Chosen branch: the smaller
Phase-2** — the effort claim is scoped down (tier savings claimed only where
proofs are non-trivial; E7 keeps ≤ 0.7 but measured on the harder
multi-function oracle scenarios, not the w-task set that already failed it),
not re-gated. The second-model rerun stays the available hedge, not the
chosen path.

Phase 2 is the first *construction* phase: **design doc §8 phases 1–2 — the
core calculus (with checker) and the deterministic elaborator from surface to
core.** Everything before this was measurement of a toolchain against the
"build-vs-extend" question. This document turns those measurements into
requirements and pre-registers the gates the construction must pass.

**Build-vs-extend position (carried from Phase 0):** Phase-0's pre-registered
Verus-spike trigger was *not* met (A1 negative → no positive signal), so the
decision stands to **extend the Dafny-backed toolchain**, not to build a new
checker from scratch. The "core calculus" below is therefore the formalization
of the ward0 subset (its type system, tier semantics, and extern boundary as a
typed core IR checked through the Dafny backend), not a new SMT solver. The
from-scratch-checker option stays a named contingency (§8), triggered only by
evidence that the Dafny backend is the bottleneck.

---

## 2. Phase-1 findings → requirements (the load-bearing section)

Each Phase-1 finding becomes one or more concrete, testable requirements for the
calculus / elaborator / harness. Mapping:

| # | Phase-1 finding | Requirement this imposes | Where it lands |
|---|---|---|---|
| R1 | Contracts take **no trailing semicolon** (`(contract)* block`); the model over-applied the statement rule, and externs cannot be called inside `ensures` | The core IR must make contract lines **structurally distinct** from statements (a contract is an annotation node, never a statement node); the surface grammar must keep `(contract)* block` and the elaborator must hard-error on `;` after a contract and on method calls inside contract expressions | ward-core grammar + elaborator |
| R2 | Raw-Dafny arm broke on a leading `dafny` echo line (`clean_dafny` was dropped in the Phase-1 port) | **One canonical candidate-extraction step** (strip fences, `dafny` echo, explanation noise) applied uniformly to every arm *before* parsing — never per-arm patches; the elaborator takes "model output" not "code" as its input | Elaborator front-end |
| R3 | C2 delta observed with the real model: W−enforce leaked exactly once (w3 `OKLEAK`); W solved the same task 0 leaks | The extern-call rule (§4c.1) — generated `_checked` wrapper as a **toolchain obligation** — is a first-class core construct with its own core-level pass, not a transpiler post-process. **Forward decision, pre-registered here:** a contract-less extern is a **hard elaboration error** — §4c.1's rule is that a contract-less wrapper is *vacuous* (the Phase-1 control cell ran one deliberately and leaked 5/8), so this milestone makes "extern must carry its contract" structural rather than advisory | ward-core extern module |
| R4 | W−enforce solved **7/8 without enforcement** — the tiered ward0 toolchain carries accuracy on its own; tiers are real, not decoration | **Tiers are part of the core**, not harness metadata: the IR carries a per-entry-point tier, and verification routing (full / bounded+fallback / none) is a semantics of the core, not of the runner | ward-core tier module |
| R5 | The 180 s fail-fast timeout did **not** fire on w2 a1 (ran 14,418 s) | **Bounded-everything is a build requirement**: every model call and every verify call has a hard wall-clock cap enforced *outside* the model's own timeout (subprocess-level kill), and this is tested by a harness soak test before any real-model run | Harness hardening (pre-construction) |
| R6 | Phase-1 expressible-subset contingency: modules are single-entry-fn with **one tier per module**; per-function tiers inside modules were dropped | The calculus must support **per-function tier routing inside a module** — this was Phase-1's pre-registered C-adjacent limitation and is the first thing the core type system must add (a module = several functions, each with its own tier/effect) | ward-core type system |
| R7 | Effort metering worked (verify_s per tier: Proven 1.4–2.8 s, Contracted 1.3 s bounded, Tested 0.0 s) | Effort metering stays a **core-level, per-function, per-tier attribute** (solver seconds recorded per proof obligation), unchanged schema so the Phase-0/1 analysis tooling reuses | ward-core + harness |
| R8 | Structured status taxonomy works (pass / transpile_error / verify_fail / test_fail / model_error, plus test_run_error / verify_run_error; OKLEAK/ERRFAIL markers) | The elaborator's error surface keeps this taxonomy and adds the §7 structured-error form: `(location, violated_obligation, counterexample)` triples translated back into **surface terms** for the repair loop — the current harness shows raw Dafny errors; Phase 2 must translate | Elaborator error module |
| R9 | Model timeout/hang flakiness (endpoint) repeatedly cost wall time across Phase-1 | Real-model runs are gated on R5 + resume-from-log (caching already in JSONL); no Phase-2 gate measurement depends on a single model run being lucky | Harness + protocol |
| R10 | C3b follow-up (Finding 7): the effort gap is 100% wrapper, 0% surface language — W− == D to the measured precision (16.2 s vs 16.2 s), the `_checked` wrapper adds ~15% fixed per-method overhead, its ensures is axiom-discharged (nothing to skip), and both skip mechanisms are unavailable (`{:verify false}` is dev-only, `{:axiom}`-with-body is a no-op) | The extern pass (week 3, R3/T3/T4) keeps wrappers **verified** (cheap per-proof, carries C2), accepts the ~15% fixed cost as the price of boundary enforcement, and does **not** chase `--allow-warnings`/`{:verify false}` hacks; the effort claim is scoped to tasks with real proof obligations (E7 ≥ 5 s oracle-verify floor), where ~15% is noise and tier savings dominate | ward-core extern module |

**Phase-0 finding that still constrains the calculus:** surface-syntax
superiority is falsified (pass@1 tie, p = 1.0000) — Phase-2 must **not** market
or gate on "ward0 syntax makes models verify better than Dafny." The calculus
wins on *effort + boundary + tier routing*, never on syntax pass-rate alone.

---

## 3. Scope

**In scope (design doc §8 phases 1–2):**

1. **ward-core v0.1** — the typed core calculus:
   - Existing types formalized: `int`, `bool`, `str`, `Unit`, `Result<T,E>`, `List<T>`.
   - **New (R6):** records/structs and **multi-function modules** with
     per-function tiers and per-function effect sets.
   - **New (design doc §5 resp. 5):** dependency references resolved against a
     declared, pinned version range — a version/schema assumption is a
     checkable elaboration-time constraint (design doc §4's package+version
     effect vocabulary), hard error on unresolved/ambiguous resolution
     (gate E4b).
   - **New (R3):** `extern fn` is a core construct with mandatory contract +
     mandatory `trust:` annotation; the `_checked` wrapper is generated by a
     core-level pass, never model convention.
   - **New:** effect tracking (design doc §4f subset to start: `net`, `db`,
     `fs`, `mut`, `partial`; `async`/`render` deferred — see §7).
   - **New (scoped):** linearity/ownership for consequential values (money,
     tokens, capabilities) — *inferred by the elaborator, never annotated by
     the model* (design doc §4). Minimal v0.1 semantics: linear values may not
     be copied or dropped implicitly.
   - **Carried:** totality by construction — bounded `for` over `range` only,
     no recursion (already the grammar's shape; now a typing rule).
   - **Carried (R7):** tier semantics Proven / Contracted / Tested with the
     exact Phase-1 behavior (bounded proof search + test fallback for
     Contracted; no proof obligation for Tested).

2. **Elaborator (ward0 surface → ward-core → Dafny)** — replacing the current
   syntax-directed `transpile()` with a typed pipeline:
   - **Front-end (R2):** candidate extraction (fences / echo noise) → parse.
   - **Desugar (R1):** contracts to core annotation nodes; hard errors on the
     Phase-1 failure classes (`;` after contract, method call in contract expr).
   - **Type-check:** resolve every implicit to an explicit core term (design
     doc §5.2); hard elaboration error on ambiguity — never a silent choice.
   - **Effect inference & check (R6/R3):** infer what a function touches; the
     declared effect set must match (design doc §5.3).
   - **Tier routing (R6/R7):** per-function tier from the IR → per-proof-obligation
     verify budget; effort metered per obligation.
   - **Extern pass (R3):** emit `_checked` wrappers + rewrite all call sites.
   - **Linearity pass (scoped):** move/copy discipline for linear values.
   - **Error translation (R8):** Dafny/checker errors → `(location, obligation,
     counterexample)` triples → surface terms, for the repair loop.

**Out of scope (explicitly deferred, design doc §8 phases 3–5):**
grammar/type-constrained decoder (phase 3), content-addressed registry (phase
4), model bootstrapping / corpus training (phase 5 — including the FABLE-5
trace corpus, which remains Phase-5 seed material per the phase-0 plan §10:
*mine trace tasks → rewrite → verify → own corpus*). None of these block the
calculus + elaborator; the calculus is their substrate.

---

## 4. ward-core v0.1 — calculus sketch

Surface `ward` (what the model writes) → elaborator → core `ward-core` (what is
checked) → Dafny backend (what executes / verifies). The core is an internal IR
with explicit nodes for everything the surface leaves implicit.

```
Module
  ├─ Externs : { name → (params, ret, contract, trust, effect) }
  ├─ Tiers    : { fn → Proven | Contracted | Tested }
  └─ Fns      : { name → (params, ret, requires[], ensures[], effects[],
                          body: Block, tier) }
```

Typing rules the elaborator must enforce (each is a testable obligation):

| Rule | Statement | Phase-1 evidence it encodes |
|---|---|---|
| T1 | Every `requires`/`ensures` is a predicate term over params + `old` + `result`; no method calls in contract terms | R1 (extern-in-ensures failed Dafny: "expression is not allowed to invoke a method") |
| T2 | A contract line is an annotation; a `;` after it is a parse error, not a statement | R1 (trailing-`;` transpile_error) |
| T3 | `extern fn` requires a contract and a `trust:` string; missing either = hard error | **Forward decision** (pre-registered §2 R3): §4c.1 states a contract-less wrapper is *vacuous* (Phase-1 control cell: 5/8 leaks); this milestone makes contract-less externs structurally impossible so the boundary can never be vacuous |
| T4 | Every extern call site is routed through the `_checked` wrapper generated by the core; no direct stub call survives elaboration | R3 (0 leaks enforce-on, 1 leak enforce-off on w3) |
| T5 | Effect set: inferred effects ⊆ declared effects, and declared-but-unused is an error (design doc §5.3); `mut`/`net`/`db`/`fs`/`partial` only | R4 (tiers/effects carry the toolchain's value) |
| T6 | Tier routing: Proven → full verify; Contracted → bounded (30 s) + test fallback; Tested → no proof obligation, runtime checks only | R7 (verify_s Proven 1.4–2.8 / Contracted 1.3 / Tested 0.0) |
| T7 | Linearity: a `linear`-typed value (money, token, capability) is consumed exactly once on every path | Design doc §4; no Phase-1 data yet — explicitly the highest-uncertainty rule (§7) |
| T8 | Totality: loops are bounded `for` over `range`; no recursion; every function terminates by construction | Carried from grammar; now a typing rule with a test |

The Dafny backend stays the checker (Z3 via Dafny 4.11); ward-core is the
formal target that makes the *toolchain's* guarantees (tiers, boundary,
effects) part of the type system rather than runner behavior.

---

## 5. Elaborator — concrete pipeline

Current state (what exists): `Ward0Transpiler` (~450 lines, syntax-directed
AST walk, `enforce_boundary` wrapper generation, `_strip_trusts`, hard
`TranspileError` on untranslatable constructs), ward0 PEG grammar (lark, LALR),
harness with the Phase-1 status taxonomy. Phase 2 replaces the single-pass
transpile with the typed pipeline below — reusing the existing grammar and
wrapper generator as components.

```
model output
  → [extract] strip fences / echo / prose        (R2: one canonical step)
  → [parse]   ward0 grammar (existing)           (R1: contract/statement distinction)
  → [desugar] contracts → core annotation nodes  (R1, T1/T2)
  → [deps]    resolve dependency references against pinned versions  (design doc §5 resp. 5; hard error on unresolved/ambiguous)
  → [infer]   types + effects + linearity        (T5, T7; hard error on ambiguity)
  → [route]   tier per function                  (R6, T6)
  → [extern]  generate _checked wrappers, rewrite call sites  (R3, T3/T4)
  → [emit]    Dafny (existing backend)           (R7: per-obligation effort metering)
  → [check]   dafny verify / --no-verify per tier (T6)
  → [errors]  Dafny diagnostics → (loc, obligation, cex) → surface terms  (R8)
```

Design rules (from design doc §5, made concrete):
- **Deterministic, auditable, not AI.** The elaborator is a conventional
  compiler pass; reproducibility is a test (same input → same core).
- **Hard-error on ambiguity.** If an implicit can resolve two ways, elaborate
  fails and reports the ambiguity in surface terms — never a silent choice.
- **Surface-legible errors.** Every core/verifier error is translatable back to
  a surface location and surface-language wording (R8) so the repair loop (§7
  of the design doc) can feed it back to the model in the language the model
  is fluent in.

---

## 6. Pre-registered gates for Phase 2

Measured on: oracle references (62 Phase-0 tasks + 8 w-tasks, extended with new
multi-function/effect scenarios) and real-model runs (same
`opencode/deepseek-v4-flash-free` model for comparability). Style: relative,
cheap, solo-runnable — same posture as Phase 0/1.

| Gate | Claim | Threshold | Measured on |
|---|---|---|---|
| **E1** | Core soundness | 100% of existing oracle references type-check + emit through the new pipeline (no regression: the 62 + 8 sets still pass; suite stays green) | Oracle, all tiers |
| **E2** | Contract rule enforced | 0 `;`-after-contract and 0 method-call-in-contract reach the backend; negative oracle probes (deliberately malformed contracts) produce surface-legible elaboration errors | Negative oracle + unit tests |
| **E3** | Extern boundary is core-level | 0 `boundary_okleak` on enforce-on runs; contract-less extern = hard error (probe); direct-stub-call = hard error (probe) | Oracle + probes, buggy scenarios |
| **E4** | Effects tracked | A function calling a `net` extern without declaring `net` fails elaboration; declared-but-unused effect fails; correct code passes. Oracle scenario set covers all five kinds | New effect scenarios |
| **E4b** | Dependency pinning | A dependency reference outside its declared version range (or unresolvable) fails elaboration; in-range reference passes. Oracle scenario covers a version-drift probe | New dependency scenario |
| **E5** | Per-function tiers inside modules | A module with one Proven fn + one Tested fn routes per function: Tested fn never blocks on proof, Proven fn verifies, effort metered per function | New multi-function w-task oracle |
| **E6** | Linearity (scoped) | Linear values are consumed exactly once on every path; probes for copy / drop / double-use all fail; money-transfer oracle passes | Oracle linearity scenarios |
| **E7** | No model regression | Real-model W arm on the 8 w-tasks through the new pipeline ≥ Phase-1 W−enforce rate (7/8); **effort ratio vs D ≤ 0.7 measured on the harder multi-function oracle scenarios (oracle verify ≥ 5 s floor) — NOT the Phase-1 w-task set, where the ratio already measured 0.77/0.94 and failed C3b** | W vs D, real model |
| **E8** | Repair-loop legibility | Structured error triples are emitted and surface-translated; measured by a repair probe: given the structured error, the model converges on the fixed attempt ≥ baseline retry rate | Repair probe |

**Decision rules (pre-registered):**
- **E1 fail** → stop; the pipeline regressed the validated core — fix before any
  feature work (soundness gate, mirrors Phase-1's oracle-first lesson).
- **E2/E3 fail** → the boundary/contract mechanism broke during refactor →
  halt-and-redesign the elaborator around the extern pass before proceeding.
- **E4/E5 fail (incl. E4b, dependency pinning)** → effects/tier routing /
  dependency resolution don't survive the typed pipeline → **halt or redesign**
  before adding more calculus features (same rule family as Phase-1 C1/C2).
  (Exception: an *isolated* E4b failure — pinning works for the oracle but not
  for real package metadata — is a scope cut, not a halt: defer pinning to
  Phase 2.5 and log it as a finding, since a dependency-gap doesn't threaten
  the core thesis.)
- **E6 fail** → linearity is the one rule with **no Phase-1 data** — failure
  here is a **scope cut, not a thesis kill**: drop linearity to a
  `linear`-typed wrapper-annotation (design doc §4's "where consequential"),
  document the limitation, and proceed. Pre-registered so a partial E6 can't
  block E7/E8.
- **E7 fail** → model regression through the new toolchain → fix before
  proceeding (the whole point of the toolchain is convergence).
- **E8 fail** → structured errors not legible → keep raw-Dafny errors as the
  fallback for the repair loop, log the translation gap as a Phase-2 finding.

---

## 7. Risks (solo, one machine — same posture as Phase 0/1)

| Risk | Severity | Mitigation |
|---|---|---|
| Building a typed IR + type-checker is real PL work; solo timeline slips | High | Scope the IR to exactly the validated subset + the R6/R3 additions; no speculative features; buffer week |
| Effects inference false-positives/negatives (Koka-style inference is the hard part) | Medium | Declared-implies-inferred check first (simpler than full inference); defer bidirectional inference to Phase 2.5 |
| Linearity is the highest-uncertainty rule (no Phase-1 data) | Medium | T7 scoped to consume-exactly-once only; E6 failure is pre-registered as a scope cut (§6), not a halt |
| Dafny backend constraints (e.g., encoding effects/linearity in Dafny types is contorted) | Medium | ward-core is the source of truth; the Dafny encoding is an implementation detail — if contorted, the *core* rules still hold and the encoding is documented; contingency: keep effect/linearity checks as elaboration-time static checks (not Dafny-encoded) |
| Multi-function modules with per-function tiers need new benchmark scenarios | Medium | Author new oracle scenarios first (gate E5), same discipline as Phase-1 w-tasks (oracle sanity before model runs) |
| Endpoint flakiness corrupts real-model gate measurements | Medium | R5 (bounded-everything + subprocess kill) lands *before* any Phase-2 real-model run; resume-from-log; gates are relative (W vs D same session) |

---

## 8. Timeline (solo, ~6–8 weeks + 1 week buffer)

| Week | Work | Deliverable |
|---|---|---|
| 0 | **Harness hardening (R5):** subprocess-level wall-clock kill for model + verify calls; soak test proving no call can exceed its cap; resume-from-log | Bounded-everything harness + soak test — **DONE (2026-08-01)**: `harness/wallclock.py` + 5 soak tests, suites green; resume-from-log already in JSONL schema |
| 1 | ward-core IR v0.1 data model + grammar updates (multi-fn modules, per-fn tiers/effects; contract-as-annotation) | IR + grammar + unit tests |
| 2 | Typed elaborator: front-end extraction (R2), desugar (R1), type-check with hard-error-on-ambiguity; port existing transpile behavior through it | Elaborator core pass; oracle 62+8 still green (E1) |
| 3 | Extern pass (R3/T3/T4) + tier routing (R6/T6) + effort metering (R7) as core passes | E2/E3 gates + probes |
| 4 | Effects (T5) + effect scenarios; **decide Verus-spike contingency if effect encoding hits a wall** | E4 gate |
| 5 | Dependency pinning (E4b); multi-function w-task scenarios; per-function tier routing end-to-end | E4b + E5 gates |
| 6 | Linearity v0.1 (T7) + probes | E6 gate (scope-cut if fails) |
| 7 | Error translation (R8): structured triples → surface terms; repair probe | E8 gate |
| 8 | Real-model W vs D rerun through the new pipeline; analysis; write-up | E7 gate + `PHASE2_REPORT.md` |
| 9 | Buffer | — |

---

## 9. Explicit non-claims

- **Not** the full Ward architecture: no decoder (phase 3), no content-addressed
  registry (phase 4), no model bootstrapping / corpus training (phase 5), no
  `async`/`render` effect kinds, no full ownership/borrowing — linearity is
  consume-exactly-once only.
- **Not** a new checker: the Dafny/Z3 backend is the checker; ward-core is the
  formal IR checked *through* it. A from-scratch checker is a named contingency
  only if Dafny becomes the measured bottleneck.
- **Not** a claim that surface syntax improves pass-rate — Phase 0 falsified
  that (p = 1.0000); Phase-2 gates on effort/boundary/tier-routing, never on
  syntax pass-rate.
- **Not** an answer to whether Phase-2.5+ (registry, bootstrapping) will work —
  those are gated on this milestone's outcome, exactly as design doc §8 phases
  3–5 are.
