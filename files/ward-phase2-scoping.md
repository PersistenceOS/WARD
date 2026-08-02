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
**Week-2 (typed elaborator front-end) status: COMPLETE (2026-08-01).** New
`phase0/wardcore/elaborator.py` (typed pipeline: [extract R2] → [desugar R1]
→ [typecheck, hard-error-on-ambiguity] → [emit], plus a convenience
`transpile()` mirroring `Ward0Transpiler`'s interface) and the committed gate
runner `phase0/wardcore/e1_gate.py`. The IR gained `Paren` + `UnitLit` expr
nodes (byte-exact emission) and `validate_module(check_t3_trust=)` — the
week-2 type-check defers the `trust:` half of T3 exactly as it defers T4 (the
Phase-0/1 reference corpus composes externs from JSON descriptors without
`trust:` lines; the week-3 extern pass R3/T3 attaches and validates it); the
contract-mandatory half of T3 stays enforced. Three real bugs found+fixed by
the E1 parity sweep: scope initialization (`set(fn.params)` vs param *names*
flagged every param undefined), loop-var not yet in scope while checking
bounds/invariants (`invariant n == i` in t1_all_positive), and hoisted-call
indent inside nested blocks (w6_crud_handler). **E1 gate: 70/70 plain +
70/70 enforce byte-identical vs `Ward0Transpiler`** (`python -m
wardcore.e1_gate`, incl. explicit `--verify` leg = dafny verify on every
emitted reference); new `test_elaborator.py` 24 tests (extract/desugar/
typecheck/parity/scope regressions). Suites green: wardcore 39 (15 ir + 24
elaborator), harness 20/20, transpiler 24/24, grammar 20/20. Next: extern
pass + tier routing as core passes (week 3, gates E2/E3).
**Week-3 (extern pass) status: COMPLETE (2026-08-01).** New
`phase0/wardcore/extern_pass.py` — the R3/T3/T4 extern-call rule as a
first-class core pass: `rewrite()` marks every extern call site
`Call.checked=True` (pure IR-to-IR transform via dataclasses.replace;
idempotent; covers var-decl/assign/if/loop/invariants/quantifiers/call-stmt
args), `validate()` runs `validate_module(check_t4=enforce,
check_t3_trust=True)` — T4 checked only under enforce (the W-enforce arm keeps
direct stub calls by design), T3-trust enforced in BOTH arms (week 2 deferred
it; week 3 activates it). `Elaborator.transpile`/`elaborate` now run the pass
between type-check and emit; `_expr_dafny` renders `_checked` on `(e.checked
or enforce)`, and hoist/call-stmt rebuilds preserve the checked flag. Gate
test compositions (e1_gate `w_task_source`, test_elaborator `w1_source`)
attach a canonical `trust: "oracle reference stub"` line per extern — placed
AFTER the terminating `;` so the transpiler's `_strip_trusts` regex strips it
(the `;`-before-trust ordering was a real bug caught by review: trust lines
ending in `;` are not stripped and break parsing) — so E1 byte-parity is
unchanged. Harness gained `run_emitted_dafny_b_marked` (runs an already-
emitted Dafny source — extern decls + wrappers + caller — against hidden
tests with stub injection) + `_parse_b_markers` dedup. **E3 gate: PASS**
(`python -m wardcore.e3_gate`): probe A contract-less extern = hard error
(`contract is mandatory (T3)`); probe B direct-stub-call = hard error (T4
flags it, pass rewrites it away, 0 direct stub calls in the emitted caller);
probe C 0 boundary_okleak on enforce-on through the ELABORATOR's emitted
Dafny across ALL SIX b-tasks (C2-style regression suite, log
`experiments/runs/e3_probe_c_all_b.jsonl`): the three buggy scenarios
(b1_payment/b3_db/b5_currency — stub violates its contract on
violation-flagged hidden tests) leak enforce-off and go to 0 enforce-on; the
three conforming controls (b2_auth/b4_rest/b6_transfer — stub honours its
contract, no violation-flagged cases) pass every hidden test in BOTH arms
proving the `_checked` wrapper never false-fires on a conforming stub:

```
id            buggy  viol  tests  leaks-off  leaks-on  pass-off  pass-on  verdict
b1_payment      buggy     3      7         3         0         4        7  PASS
b2_auth         ctrl      0      4         0         0         4        4  PASS
b3_db           buggy     3      7         3         0         4        7  PASS
b4_rest         ctrl      0      4         0         0         4        4  PASS
b5_currency     buggy     2      6         2         0         4        6  PASS
b6_transfer     ctrl      0      4         0         0         4        4  PASS
```

New `test_extern_pass.py` 10 tests (rewrite
coverage, idempotence, T4 flag, T3-trust enforce/defer, pipeline
integration). Suites green: wardcore 50 (15 ir + 25 elaborator + 10 extern
pass), harness 20/20, transpiler 24/24, grammar 20/20; E1 gate re-green
70/70 + 70/70. Next: tier routing (R6/T6) + effort metering (R7) as core
passes (week 3 remainder, gates E2/E5).
**Week-3 remainder (tier routing R6/T6 + effort metering R7) status: COMPLETE
(2026-08-01).** New `phase0/wardcore/tier_pass.py` — tier routing is now a
core pass, not harness metadata: `TierPass.plan(module)` derives a
deterministic per-function `VerificationPlan` from the IR's `Function.tier`
(Proven → VERIFY_FULL, no budget cap, no fallback; Contracted → VERIFY_BOUNDED,
30 s, test fallback; Tested → NO_PROOF, never reaches a verify call), and
`TierPass.validate` enforces the **T6 cross-tier rule**: a proof-carrying
(Proven/Contracted) function must not call a Tested function — the caller's
proof would rest on an unverified callee (same principle as T4). R7
`EffortMeter`/`EffortRecord` own the per-function solver-seconds schema
(`verify_s` floats, Phase-0/1-compatible); NO_PROOF obligations are never run →
actual_s 0 (the Phase-1 Tested invariant). The elaborator now strips a `tier:
Proven|Contracted|Tested` surface annotation (mirrors `trust:`), attaches tiers
to fns in declaration order, runs TierPass in `transpile`/`elaborate`
(exposing `elaborator.tier_plan`), and emits `method {:verify false}` for
Tested fns only. **E5 gate: PASS** (`python -m wardcore.e5_gate`, 4/4 probes)
on a multi-function module (Proven entry + Contracted helper + Tested helper
whose contract is DELIBERATELY unprovable): probe A routing table; probe B
Tested never blocks on proof — the module's `dafny verify` passes (3 verified,
0 errors) even though the Tested contract is unprovable, while a no-tier
control with the same fn FAILS on a real postcondition error (proving the tier
routing is what unblocks it); probe C effort metered per function via `dafny
verify --filter-symbol=<fn>` wall-clock into EffortMeter (entry 1.7 s
verified, tested 0.0 s never run); probe D cross-tier rule = hard
ElaborationError. One Dafny-4.11 finding: the Tested fn's `{:verify false}`
triggers Dafny's development-only advisory warning and rejects the compiled
module without `--allow-warnings` despite clean proof obligations — handled by
`verify_dafny(allow_warnings=)` scoped to Tested-bearing modules (NOT the R10
wrapper-cheapening case: `_checked` wrappers stay fully verified; the no-tier
control proves the flag can't mask a real proof failure). Suites green:
wardcore 63 (15 ir + 25 elaborator + 10 extern pass + 13 tier pass), harness
20/20, transpiler 24/24, grammar 20/20, wallclock 7/7; E1 gate re-green 70/70
+ 70/70; E3 gate re-green (all 6 b-tasks). E2 (contract rule) has been green
since week 2 (desugar hard errors + negative probes). Next: effects (T5) +
dependency pinning (E4b) + per-function tier routing end-to-end on new
multi-function w-task oracles (weeks 4–5, gates E4/E4b/E5-real).
**Week-4 (effects pass T5/E4) status: COMPLETE (2026-08-01).** New
`phase0/wardcore/effects_pass.py` — T5 as a core pass: `EffectsPass.infer`
computes, per function, the effect set it actually touches (extern-driven —
a fn touches what it calls — transitively through the module call graph,
with a cycle guard), and `validate` enforces the declared set in both
directions on functions that DECLARE effects: **escape** (inferred ⊄
declared → `calls undeclared effect X (T5)` — the pre-registered E4 probe)
and **unused** (declared ⊄ inferred → `declared effect X is unused (T5)`),
one problem per kind (R8 repair-loop friendly). Surface annotations
`effect: net|db|fs|mut|partial` (externs) and `effects: net, db, ...` (fns)
are stripped pre-parse like trust/tier and attached in declaration order;
`parse_effect_set` hard-errors on unknown kinds. The elaborator runs the
pass in transpile/elaborate (exposing `elaborator.effects_inferred`), and
the week-1 IR-level T5 pre-check was deferred via a new `check_t5` flag
(both `type_check` and `ExternPass.validate` pass `check_t5=False`) so
EffectsPass is the SINGLE authoritative T5 in the pipeline. **E4 gate: PASS**
(`python -m wardcore.e4_gate`, 5/5): probe A pure escape (declared db,
calls net+db both used → only the net-escape fires, nothing unused); probe
B unused; probe C correct multi-effect code elaborates AND `dafny verify`
clean (effects are elaboration-time, never change the backend); probe D all
five kinds net/db/fs/mut/partial (declared == inferred); probe E transitive
inference through fn calls with undeclared-db escape. **Week-4 boundary
(precise, per scoping doc §7 "declared-implies-inferred check first" and E1
parity):** a function with NO `effects:` annotation is unconstrained this
week — the E4 gate's "calling a net extern without declaring net fails" is
true when the fn declares *some* effect set that omits net (probe A's exact
case), not when it declares nothing. Bidirectional inference (requiring
every fn to declare) is deferred to Phase 2.5 exactly as pre-registered. New
`test_effects_pass.py` 16 tests (inference direct/transitive/cycle-guard,
escape + escape-through-pipeline, unused, undeclared-compat, all five kinds,
annotation attachment, extern default NET, parse errors, pipeline exposure).
Suites green: wardcore 79 (15 ir + 25 elaborator + 10 extern pass + 13 tier
pass + 16 effects pass), harness 20/20, transpiler 24/24, grammar 20/20,
wallclock 7/7; E1 gate re-green 70/70 + 70/70; E3 re-green (all 6 b-tasks);
E5 re-green. Next: dependency pinning (E4b) + multi-function w-task oracles
with per-function tiers/effects end-to-end (weeks 5, gates E4b/E5-real).
**Week-5 (dependency pinning E4b) status: COMPLETE (2026-08-01).** New
`phase0/wardcore/dep_pass.py` — E4b as a core pass (design doc §5 resp. 5:
"resolve every dependency reference against a declared, pinned version range").
Version grammar (v0.1, deterministic): `X.Y.Z` exact, `^X.Y.Z` caret
`[X.Y.Z,(X+1).0.0)`, `~X.Y.Z` tilde `[X.Y.Z,X.(Y+1).0)`, `X.Y.*`/`X.*`
wildcards; malformed specs/versions are hard elaboration errors (never a
silent choice — design doc §5). `DepPass.resolve` builds the per-extern
resolution plan against `Module.deps`; `validate` reports three E4b
conditions: **unresolved** (reference name not declared), **out_of_range**
(version drift — reference outside the pinned range), **ambiguous** (more
than one declared range for the name). Surface syntax: `dep: name@range`
lines before the first definition declare module ranges (→ `Module.deps`);
`dep: name@version` after an extern def is that extern's reference (→ new
`ExternFn.dep`, attached in declaration order like trust/effect; a leftover
reference with no extern left is a hard error). Week-5 boundary (E1 parity,
mirrors week-4 §7): externs WITHOUT a `dep:` reference are unconstrained —
the 70-reference corpus declares no deps, so byte-parity is untouched.
**E4b gate PASS** (`python -m wardcore.e4b_gate`, 5/5): A version-drift
(ledger@3.1.0 vs ^2.0.0 → hard error), B unresolved (payments@1.0.0 → hard
error), C ambiguous (^2.0.0 + ^1.0.0 → hard error), D in-range reference
(ledger@2.4.1, auth@1.2.0) elaborates AND the emitted Dafny `dafny verify`s
clean (resolution is elaboration-time; it never changes the backend), E
resolution plan exposed per extern. Validation: wardcore **101** tests (79 +
22 new dep tests: grammar, in-range, drift/unresolved/ambiguous hard errors
through the pipeline, malformed specs/versions, week-5 boundary, annotation
attachment) — all green; E1 70/70 + 70/70 (parity untouched), E3/E4/E5
re-green. Reviewer caught and I fixed: exact pins were unbounded (hi=None
meant unbounded; now `[v, next patch)`), ambiguity/unused scenarios must
insert extra ranges in the module header (post-first-def `dep:` lines read
as extern refs), extern contracts need the grammar's trailing `;` (unlike fn
contracts — Phase-1 R1 is fn-only), and the base SRC pinned `auth@~1.0.0`
which does NOT contain the referenced 1.2.0 (range now `~1.2.0`).
**Week-5/6 (multi-function w-task oracles + E5-real) status: COMPLETE
(2026-08-01).** Four new multi-function w-task oracles authored in the
established w_task schema — `phase0/benchmarks/w_tasks/` w9_inventory_order
(Proven entry + Proven helper + Contracted helper, deps inventory@^2.0.0 +
orders@^1.0.0), w10_session_ledger (Contracted entry + 2 Proven helpers, deps
session@^3.0.0 + ledger@^2.0.0), w11_idempotent_retry (**Tested** entry + 2
Proven helpers, deps dedup@^1.0.0 + pay@^4.0.0), w12_hold_release (Proven
entry + Contracted helper + Proven helper, dep escrow@^2.0.0) — each with
JSON descriptor (tiers/effects/deps, extern effect+dep, violation_probes,
hidden tests) + annotated `.ward0` reference (per-fn `tier:`/`effects:`
annotations, entry-first multi-fn) + `.dfy` raw-Dafny reference, so all three
tier gates get exercised (Proven ×4, Contracted ×3, Tested ×1, entry fn
covering all three roles). The Phase-0/1 transpiler gained annotation
tolerance (`_strip_annotations` strips tier:/effects:/effect:/dep: like
trust: — a no-op on the E1 corpus) so the same annotated references work on
both pipelines. **E5-real gate PASS** (`python -m wardcore.e5_real_gate`, 4/4
tasks; log `experiments/runs/e5_real_gate.jsonl`): per task — E4b deps all
resolved in-range, per-fn tier routing matches the declared tiers, effects
declared == inferred for every fn, the emitted Dafny `dafny verify`s clean,
hidden tests pass with **0 boundary_okleak** on violation cases, and effort is
metered per function (`--filter-symbol` into EffortMeter: Proven/Contracted
1.4–3.5 s verified; w11's Tested entry never run, verify_s 0.0). Three
integration fixes landed during validation: compose_module emits the
mandatory `trust:` per extern (T3), the harness translate path gained
`--allow-warnings` (run_emitted_dafny_b_marked/_run_compiled_b) for
Tested-bearing modules (w11's `{:verify false}` dev-only warning — same
finding as E5), and the E1 gate's `w_task_source` now delegates to
`compose_module` with Tested-bearing modules excluded from byte-parity (their
`{:verify false}` emission is tier semantics, verified by E5-real instead).
New `test_w_task_oracles.py` 17 tests (schema sanity incl. all-three-tiers,
elaboration, E4b resolution + drift-on-oracle probe, per-fn tier/effects
attachment + routing, effects escape on a real oracle, T6 cross-tier
negative probe, w12-fixed-shape regression). Validation: wardcore **118** tests
green (101 + 17), transpiler 24/24, harness 20/20, grammar 20/20, wallclock
7/7; E1 gate **73/73 plain + 73/73 enforce** byte-identical (62 + w1-w10 +
w12 parity; w11 Tested-bearing → E5-real); E3/E4/E4b/E5 all re-green;
fake-model harness W-arm smoke on the 4 new tasks 4/4 solved, 0/8 boundary
leaks, 0 fallbacks. **Next: week 6 (linearity T7/E6) or the first real-model
E5-real run (gates E4b/E5-real on real-model runs) per the week-8 timeline.**
**Companion docs:** `../files/ward-language-design.md` (§4 core calculus, §4c
tiers, §4c.1 extern-call rule, §5 elaboration, §8 what-it-takes),
`../files/PHASE1_REPORT.md` (decision + findings),
`../files/ward-phase1-experiment-design.md` (pre-registration this builds on),
`../files/ward-certified-code.md` (Phase-2.5 certificate vision, gate E9).

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
| R11 | Verification is a *process* that dies on the machine with Dafny+Z3 — nothing checkable ships with the code (the productization gap that motivates the certificate, gate E9). **Forward decision, pre-registered here:** proof-carrying code was settled theory (Necula 1997) that failed industrially because proofs were too expensive for human code; AI generation inverts the economics (the proof loop already runs during generation) | The pipeline must be able to **emit a machine-checkable `.proof` certificate** beside emitted code — per-function tier + proof outcome, `trust_boundary` manifest from the already-collected `trust_report`, source/emitted hashes — and a **dependency-free checker** must validate it with no Dafny/Z3. Sound only because E1 proves **byte-identical** emission (deterministic hashes bind code↔proof). **Phase 2.5 preview, pre-registered as gate E9; does not block E1–E8** | ward-core emit + `cert` module (Phase 2.5) |

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

3. **Certificate emission — Phase 2.5 preview (the productization step; vision
   `files/ward-certified-code.md`):**
   - **`.proof` artifact:** source hash; per-function tier + proof outcome
     (`verify_s`, obligations, `verified | timeout | failed`); `trust_boundary`
     manifest (each extern's `trust:` string + monitor flag); toolchain config
     (`enforce_boundary`, verification-time-limit); emitted-Dafny hash.
     `emitted_dafny_sha256` is *declared, not independently re-derived* at
     Level-1 (no transpiler inside the checker); re-derivation is the Phase-3
     standalone checker.
   - **Standalone checker** (`cert_check`, no Dafny/Z3): rebind source hash,
     validate structure + tier rules + trust manifest against the toolchain
     block, reject tampering; the richer `validate_module()`-based variant is
     CI-side only (two variants, per the vision note §4).
   - **Gates:** E9 (below). Explicitly **not** in the core-calculus scope — it
     is additive to, never load-bearing for, E1–E8.

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
| **E7** | No model regression | Real-model W arm on the 8 w-tasks through the new pipeline ≥ Phase-1 W−enforce rate (7/8); **effort ratio vs D ≤ 0.7 measured on the harder multi-function oracle scenarios (oracle verify ≥ 5 s floor) — NOT the Phase-1 w-task set, where the ratio already failed C3b under both measurements (0.77 live / 1.15 controlled, Finding 7)** | W vs D, real model |
| **E8** | Repair-loop legibility | Structured error triples are emitted and surface-translated; measured by a repair probe: given the structured error, the model converges on the fixed attempt ≥ baseline retry rate | Repair probe |
| **E9** (Phase 2.5 preview) | Certificate artifact checkable standalone | A `.proof` emitted for oracle tasks validates via the dependency-free checker with no Dafny/Z3 installed; tampering (source, trust string, verdict) invalidates it (exit 1); production cost ≤ 5% of measured verify + token cost | New cert probes + oracle tasks |

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
- **E9 fail** (Phase 2.5) → certificate not standalone-checkable or cost > 5% →
  **scope cut, not halt**: the productization step reverts to a documented
  vision note (`files/ward-certified-code.md`) and never blocks E1–E8 — it is
  additive by pre-registration.

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
| Certificate overclaims (checker validates the artifact, not the SMT proof) | Low | Split the claim in the vision note (§4/§8): Level-1 structural check now, independent re-proof deferred to the Phase-3 standalone checker; the non-claims section states it |

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
| 8b | **Phase 2.5 preview:** certificate emission (`.proof` for oracle tasks) + standalone `cert_check` + tamper probes; measure production cost vs verify + token | E9 gate (scope-cut if fails; does not block Phase-2 close) |
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
- **Not** a claim that a certificate independently re-proves SMT obligations at
  Level-1 — the standalone checker validates the artifact and the tier
  semantics; independent re-derivation is the Phase-3 standalone checker
  (`files/ward-certified-code.md` §4/§8).
