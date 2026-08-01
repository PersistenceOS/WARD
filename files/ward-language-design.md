# Ward: A Frontier Verification Language for Full-Stack AI Development

**Status:** Design proposal / architecture sketch. Not implemented. Not benchmarked.
This document lays out a coherent design, the reasoning behind each choice, and what
would be required to actually build and test it. It is a starting point for a real
engineering effort, not a finished language.

**Positioning (updated):** Ward is not a bet that verification itself is a gap —
Dafny and Lean 4 already do real-time, production-grade, checker-driven AI code
verification (see §2a). Ward's target gap is different and, as of this draft,
unfilled by either: **verification-grade rigor applied to full-stack software that
depends on huge, unverified, real-world library ecosystems (web frameworks, ORMs,
UI libraries, cloud SDKs) — without forcing the model to write in a low-resource,
off-distribution formal syntax, and without demanding full proof for code where
proof isn't the bottleneck.**

---

## 1. Design Philosophy

Two false binaries drove earlier, weaker versions of this idea:

1. **"Familiar syntax vs. strict semantics"** — resolved by splitting the language
   into a **surface layer** (what the model writes, familiar) and a **core calculus**
   (what gets checked and executed, strict). See §3.
2. **"Make the model smarter vs. make errors easier to catch"** — resolved by
   attacking a third lever instead: **reduce how much novel code the model has to
   write at all**, via a global, proof-verified, content-addressed library that
   composition is drawn from by default. See §6.

A third false binary, surfaced by direct comparison to Dafny and Lean 4 (§2a):

3. **"Verify everything vs. verify nothing"** — resolved by **tiered verification**
   (§4c): most full-stack code (UI glue, routing, formatting) is contract-checked
   and tested, not fully proven; only consequential logic (auth, payments, data
   integrity, anything touching linear-typed resources) gets full proof. This is a
   direct response to Dafny's own documented pain point that manual verification
   effort is significant and hard to predict in non-trivial programs (§2a).

The governing principle: **treat every AI-authored program as a proof obligation
where proof is warranted, and as a contract-checked, tested obligation everywhere
else.** "Compiles" means "provably satisfies its stated contract, at whatever tier
that contract requires" — not "type-checks and looks plausible," and not
"everything must be a theorem."

---

## 2a. Positioning Against Dafny and Lean 4 (added after direct research)

Dafny and Lean 4 are real, currently-used, verification-first languages already
deployed in AI-driven code-generation loops — not research curiosities. Any honest
proposal for a new language has to be positioned against what they already do well.

**What they already do, confirmed:**

| Capability | Evidence |
|---|---|
| Self-healing repair loop using verifier feedback | DafnyPro reaches 86% correct proofs on DafnyBench using Claude Sonnet 3.5, a 16-point improvement over prior state of the art |
| High verify@k success with iterative repair | A self-healing Dafny pipeline got Gemma 4-31B to 90.91% verification success, and GPT-OSS 120B from 0% to 81.82% |
| Adversarial "cheating" detection beyond the deterministic checker | AxDafny already runs a reviewer LLM that rejects trivially-satisfied predicates and proof-bypass constructs — Ward's §7 adversarial critic, already implemented |
| Checker-as-reward-signal to reduce hallucination | Dafny + Z3 used explicitly as ground-truth reward in published pipelines |
| Anti-gaming / vacuous-verification defenses | Documented failure mode (models satisfying verifiers with trivial/empty postconditions) addressed via external functional-validation tooling (uDebug) |
| Production industrial use | Dafny used at AWS to verify their authorization engine while preserving exact behavior of the original implementation |
| Multi-target compilation to existing ecosystems | Dafny compiles to C#, Java, JavaScript, Go, and Python specifically to integrate with existing developer workflows |

**Where they fall short for full-stack development, confirmed:**

| Gap | Evidence |
|---|---|
| Lean 4 has effectively no web/frontend ecosystem | A developer attempting a Lean 4 backend found no HTTP framework; the official community pointed to a raw socket-bindings library, forcing a hand-rolled HTTP/1.1 implementation from the RFC |
| Lean 4 frontend usage is hobbyist, not production | The only documented Lean 4 frontend effort is a single-developer experimental SSR/reactivity library, explicitly not expected to see real adoption |
| Formal syntax is off-distribution for LLMs, capping accuracy | Directly targeting Dafny's formal syntax leaves models off-distribution relative to their pretraining, limiting throughput; pure Dafny pass@1 sits around ~77% even inside a verification-aware pipeline |
| Verification effort is significant and unpredictable in non-trivial programs | Dafny's own case-study literature: strengths are real, but manual verification work "may be significant and difficult to predict and master" |
| Neither has a first-class trust boundary for calling unverified third-party libraries | Not documented as a *native language feature* in either — see correction below: the underlying idea has real academic precedent, just not built into Dafny/Lean's core language |
| Neither treats async/network/UI-render as first-class, named effect kinds | Dafny's effect story is heap-focused (`reads`/`modifies`); no native concept of "this function performs an HTTP call" or "this component re-renders on state change" |

**Adding Verus (Rust's verification dialect) — the closest existing precedent
to "verified, Rust-like, mainstream," directly relevant to Ward's stated goal
of broad adoption:**

| Capability | Verus |
|---|---|
| What it is | A verification-aware subset of Rust with formal pre/post-condition specs, SMT-backed (Z3) proof, compiling to real, runnable Rust binaries |
| AI proof automation | AutoVerus and Lemur both perform automated/LLM-assisted proof generation specifically for Verus code — the most direct existing precedent for "AI writes the proof, not the human" |
| Ecosystem | Inherits Rust's real crates.io ecosystem directly, since Verus code compiles to actual Rust — unlike Lean 4 or SPARK, this is not a gap |
| Relevance | The single closest real precedent found in this research for a broadly-adopted, verification-first, Rust-like language |

**Adding SPARK/Ada, since it's a third live precedent, not just Dafny/Lean:**

| Capability | Dafny | Lean 4 | SPARK/Ada |
|---|---|---|---|
| AI self-healing verification loop, published | 82–91% verify success (DafnyPro/self-healing) | Strong on proofs (autoformalization gains 4–6x) | 50.7% (Marmaragan, GPT-4o) — weaker than Dafny; but a separate, very recent (2 weeks old) paper has agents building fully verified crypto/IPsec/TLS stacks, discharging 49,280 proof obligations across 1,600 tests |
| Industrial production use | AWS authorization engine | Formal math (AlphaProof, DeepSeek-Prover, etc.) | Long-standing high-integrity/aerospace industry use; contracts are Ada-native (Ada 2012), not bolted on |
| Ecosystem for full-stack dev | Compiles to C#/Java/JS/Go/Python — real integration story | Effectively none (hand-rolled HTTP server) | Small — ~400 packages on Alire vs. ~1.3M on npm; SPARK also excludes pointers/aliasing/exceptions by design, narrowing general applicability further |

**Conclusion driving the rest of this document:** Ward should not compete with
Dafny, Lean 4, SPARK, or Verus on core verification mechanics — all four already
prove contract-based, SMT-backed, AI-repairable verification works, in
production or active research. It should match that (§4, §7) and differentiate
on: ecosystem integration (§4d), a verification boundary for unverified
libraries (§4e — see correction below), tiered verification (§4c), a full-stack
effect model (§4f), and reusable contract templates (§4g, new — see below).
**Given Verus's existence, any serious next step for this research should
include a direct build-vs-extend comparison: is it cheaper and lower-risk to
extend Verus toward full-stack support than to build Ward as a new language
from scratch?** This document does not currently answer that question and
should not proceed to real resourcing without doing so.

## 2b. What Is and Isn't Novel in Ward (added directly in response to a fair challenge)

An earlier draft of this document overstated one piece of novelty. Corrected inventory:

**Not novel — established, reused deliberately, not reinvented:**
- Contracts, SMT-backed proof, self-healing repair loop — Dafny and SPARK
  already do this in production, with published numbers (above).
- **Correction: the trusted FFI/verification boundary (§4e) is not a new idea.**
  It is the established research area of **Gradual Verification** (Bader et
  al., 2018), including work specifically titled *"Specifying the Boundary
  Between Unverified and Verified Code"* (with a corresponding Amazon Science
  paper on the same problem at industrial scale), and a live project —
  **verity** — that already implements near-identical machine-readable trust
  annotations for AI-agent-authored, formally verified code, explicitly
  betting that "agents will make full formal verification practical." §4e
  should be read as *applying* this established technique to LLM-authored
  full-stack code specifically, not inventing it. The prior claim that this
  was "the largest genuinely unfilled gap" is retracted.
- Effect tracking, linear types, content-addressing, refinement types,
  totality checking, CEGIS-style adversarial checking, property-based testing,
  type-constrained decoding — all previously verified as established precedent
  (§9a–§9c, §11).
- **Verified WASM/native multi-target compilation (§4d)** — F*/HACL* already
  compiles one verified source to both WebAssembly (production npm package)
  and C (shipping in Firefox and WireGuard). Ward applies the same pattern.
- **Reusable contract templates (§4g)** — the specification-patterns research
  area (NASA's FRET, smart-contract property catalogues) already establishes
  this as a real, working technique. Ward applies it to its own contract syntax.

**Genuinely novel or under-precedented, as far as this research process found:**
- The specific *combination*: surface/core elaboration + tiered verification +
  gradual-verification-style FFI boundary + a full-stack-specific effect
  vocabulary, applied together to one target problem (AI-authored full-stack
  apps). No single existing system combines all of these for this purpose.
- **Confidence-typed verification routing** (§7) — using a model's own
  per-unit generation confidence to decide verification tier — was not found
  precedented anywhere in this research process, in Dafny, Lean, SPARK, or the
  gradual-verification literature. This is the component of Ward with the
  weakest outside support, which cuts both ways: it may be a genuine
  contribution, or it may be under-precedented because it doesn't work well —
  only building and testing it (§8, Phase 0/1) can settle that.
- The motivating *reason* for the surface/core split — specifically fixing the
  documented LLM off-distribution penalty on formal syntax (Dafny's ~77%
  pass@1 ceiling) — is a distinct framing even though the mechanism itself
  (surface syntax elaborating to a checked core) is old (Lean 4, Idris 2, F*).

**Honest summary:** Ward is best described as a **new integration architecture
for an established toolkit**, purpose-built for a specific, underserved target
(AI-authored full-stack software) — not a fork of any single language, and not
a claim to have invented verification, contracts, effects, or repair loops. The
claims of outright novelty in this document are now narrowed to the two items
above, both explicitly flagged as unverified design choices, not established fact.

---

## 2. Architecture Overview

```
 ┌─────────────┐   elaborate   ┌──────────────┐   check    ┌─────────────┐
 │  Surface     │ ────────────▶│  Core         │──────────▶│  Verifier    │
 │  (model      │  (deterministic│  Calculus     │ (SMT/proof │  (accept/    │
 │  writes this)│   compiler pass)│  (strict,     │  search)   │  reject +    │
 │              │               │  dependently  │            │  structured  │
 │              │               │  typed)       │            │  error)      │
 └─────────────┘               └──────────────┘            └──────┬──────┘
        ▲                                                          │
        └───────────────── structured error, translated ───────────┘
                             back into surface terms (repair loop)
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │  Multi-target backend     │
                          │  (JS/TS, Go, Python, JVM)  │
                          │  + trusted FFI boundary    │
                          │  to existing ecosystems    │
                          └─────────────────────────┘
```

Components, each independently motivated by a specific benchmark failure
pattern or a specific documented Dafny/Lean gap:

| Component | Solves |
|---|---|
| Surface layer | Low fluency in unfamiliar syntax (Rust: 58-72% vs Python: 88%); Dafny's ~77% pass@1 off-distribution ceiling |
| Core calculus | Silent logic/version/effect errors (GitChameleon: 48-51%, SWE-bench Pro drop) |
| Elaboration | Forces the model to *state* contracts without hand-writing strict syntax |
| Repair loop | Converts single-shot generation into iterative, checker-guided convergence (matches Dafny/Lean's proven approach) |
| Multi-target compilation | Avoids Lean 4's dead-end of having no real ecosystem to deploy into |
| Trusted FFI boundary | Lets verified code call unverified real-world libraries safely — unaddressed by either Dafny or Lean |
| Tiered verification | Avoids Dafny's own documented problem of unpredictable, unbounded verification effort on non-trivial programs |

---

## 3. Surface Layer

Deliberately unoriginal. Python/TypeScript-shaped: familiar control flow, familiar
collection literals, familiar function definitions. The bet: model fluency tracks
training-data volume more than language elegance, so surface syntax should look
like what's already best-represented in training corpora — and, now confirmed
directly: this is precisely the axis on which Dafny's formal syntax measurably
costs accuracy (~77% pass@1 pinned partly to being off-distribution).

```ward-surface
fn transfer(from: Account, to: Account, amount: Money) -> Result<Unit, TransferError>
  requires amount > 0
  requires from.balance >= amount
  ensures from.balance == old(from.balance) - amount
  ensures to.balance == old(to.balance) + amount
{
    from.balance -= amount
    to.balance += amount
    Ok(())
}
```

- `requires` / `ensures` are surface-level contract syntax — familiar to anyone
  who's seen design-by-contract (Eiffel) or docstring-style pre/post-conditions.
  The model states them in plain terms; it does **not** need to hand-write
  dependent types, refinement predicates, or ownership annotations.
- Generation is **grammar-constrained**: the decoder only samples tokens that keep
  the partial program inside the valid surface grammar at every step. This is
  already production technology for structured output (JSON-schema-constrained
  decoding, tool-calling grammars; backends like XGrammar and Outlines ship in
  vLLM/SGLang/TensorRT-LLM today) — applying it to a whole language deletes the
  "syntactically malformed program" failure class outright, before logic is even
  considered.
  **Correction from initial draft:** grammar-only constraints guarantee syntactic
  validity but not semantic correctness (`x + 1` parses fine even if `x` isn't a
  number). The stronger, verified technique is **type-constrained decoding**,
  which also masks tokens incompatible with the current type context — published
  results (Mündler et al., PLDI 2025, primary source confirmed) show this cuts
  HumanEval compilation errors by 74.8% versus 9.0% for syntax-only constraints.
  **Second correction (from the full primary-source read):** that 74.8% figure is
  a reduction in *compilation errors*, not overall functional accuracy. The same
  paper's own pass@1 (functional correctness) results are far more modest: +3.5%
  for synthesis, +5.0% for translation, but a much larger +37.0% for repair tasks
  — because most compiling-but-wrong programs stay wrong; type constraints mainly
  convert non-compiling attempts into compiling ones, which is necessary but not
  sufficient for correctness. This matters directly for Ward: the decoder-level
  win compounds with, but does not replace, the contract/proof layer in §4 and §7
  — type-constrained decoding alone would not get Ward near a 90%+ target on its
  own, only reduce one specific failure category feeding into it.

---

## 4. Core Calculus

Not written by the model directly. Produced by elaboration (§5), checked by the
verifier (§7). Strict on every axis that benchmarks showed correlates with failure:

- **Dependent/refinement types** (Idris/Liquid Haskell lineage) — contracts like
  `requires amount > 0` become actual type-level predicates the checker must prove,
  not comments.
- **No implicit coercion, no `any`/untyped escape hatch** — every value has an
  exact, provable type at every point.
- **Effect tracking** (Koka-style) — every function's signature declares what it
  touches: network, filesystem, mutable state, specific package + version range.
  A change that violates a declared effect boundary is a compile error, not a
  production incident. This directly targets the GitChameleon failure mode
  (library-version incompatibility): a version assumption becomes a checkable
  elaboration-time constraint.
- **Linear/ownership types where consequential** (money, tokens, physical
  actuators, one-time capabilities) — inferred and inserted *by the elaborator*,
  not hand-annotated by the model. This is the direct fix for Rust's low scores:
  Rust forces the model to *author* borrow/lifetime annotations in its own output;
  here, the model never touches that layer at all.
- **Totality by default** — functions must be provably terminating unless
  explicitly marked partial, closing the "infinite loop / unbounded resource use"
  failure class before it starts. **Caveat confirmed by research:** real totality
  checkers (e.g., Idris's) are explicitly conservative and imperfect by
  necessity — the halting problem is undecidable, so a totality checker will
  reject some genuinely-terminating functions as "not proven total," and
  documented cases exist where checkers have been too permissive in the other
  direction. Ward's totality checking should be expected to add friction
  (false-positive rejections requiring rephrasing) rather than being a clean
  yes/no gate.

---

## 4c. Tiered Verification (new — direct response to Dafny's documented pain point)

Full-stack applications are mostly not algorithmically interesting. A typical app
is dominated by routing, request/response shaping, UI event handling, and
formatting — code where full formal proof is expensive overkill, not safety.
Dafny's own case-study literature confirms this is a real cost, not a hypothetical
one: manual verification effort is "significant and difficult to predict and
master" even for experienced users on non-trivial programs.

Ward addresses this with three explicit verification tiers, declared per-module
or inferred from effect/type signature:

| Tier | What's required | Typical code |
|---|---|---|
| **Tested** | Property-based tests generated from type signature; no proof | UI glue, formatting, simple CRUD handlers |
| **Contracted** | `requires`/`ensures` checked, but proof search is bounded (timeout falls back to test-based confidence, not a hard failure) | Business logic, validation, most backend services |
| **Proven** | Full SMT/proof-search verification, no timeout fallback, required for anything touching linear-typed resources (§4 — money, tokens, one-time capabilities) | Auth, payments, data-integrity-critical paths |

This is the direct fix for the risk documented in §2a: unpredictable verification
cost is *contained* rather than *eliminated*, by only paying the expensive cost
where the resource-type system (§4) says it's mandatory, and letting the rest of
the app fall back gracefully to tested-not-proven — which is still stronger than
what any current mainstream language offers by default (property-based tests
generated automatically, rather than hand-written by the model — see §7).

### 4c.1 Extern-call rule (validated in phase-0's Phase-1 pivot)

**The rule:** every call from Ward core code into an unverified library
(`extern fn`, §4e) is routed through a **generated runtime contract-check
wrapper**, never through the raw stub. The wrapper is a toolchain obligation,
not a model convention — the checker emits it and rewrites all call sites
(`stripe_charge(...)` → `stripe_charge_checked(...)`) regardless of what the
model wrote:

```ward-surface
extern fn stripe_charge(amount: Money, token: CardToken) -> Result<Charge, PaymentError>
  requires amount > 0
  ensures result.is_ok() implies result.unwrap().amount == amount
  trust: "stub — Stripe SDK v14.2, not independently verified"
```

```
// generated by the checker (enforce_boundary); shown in Ward surface notation —
// the phase-0 ward0 subset spells these `int`/`str`/`Result<Unit, str>`:
method stripe_charge_checked(amount: Money, token: CardToken) returns (result: Result<Charge, PaymentError>)
  requires amount > 0
  ensures result.is_ok() implies result.unwrap().amount == amount  // stub contract, proved via extern axiom
{
  var r := stripe_charge(amount, token);
  if !(r.is_ok() implies r.unwrap().amount == amount) {           // runtime check of actual stub output
    return Err("contract violation");
  }
  return r;
}
```

The wrapper's `ensures` is the stub's contract — so the verifier can reason
against the call exactly as if it were an axiom — and at runtime the wrapper
evaluates the contract expression against the stub's *actual* return value,
converting any contradiction into `Err("contract violation")`. An over-grant
(stub returns `Ok` where the contract says `Err`) can never cross the boundary
as `Ok`.

**Per-tier application:**

| Tier | Extern-call behavior |
|---|---|
| **Proven** | Caller discharges the stub's `requires`; result must pass through the wrapper untouched (see caller rule below) |
| **Contracted** | Same as Proven — bounded proof search may fall back to the runtime check for confidence |
| **Tested** | No proof obligation; the wrapper is still generated and enforces at runtime |

**Caller rule (residual model obligation):** discharge the stub's `requires`,
then **pass the wrapper's result through verbatim** — do not re-derive the
stub's outcome from the checked result, and do not re-label
`Err("contract violation")` as a genuine library error. Phase-1 data shows
model-written defense now *conflicts* with the wrapper: callers that
re-inspect the stub result convert the wrapper's violation marker into a
"real" error (`err(declined)` / `err(no such user)`), and short-circuiting
callers fabricate outcomes without ever calling the stub. The correct,
minimal caller is a pass-through (`return stripe_charge(amount, token);`).

**Boundary rule:** enforcement is only as strong as the contract. With no
contract (baseline arm) the wrapper is vacuous — bare pass-through — so
the power comes from the stated contract, not from the wrapper alone. This
reinforces §4e: an extern *must* carry its contract for the boundary to mean
anything.

**Evidence (phase-0 Phase-1):** deterministic naive callers (zero defense):
8/8 boundary leaks → 0/8 with enforcement, all tests pass. Real model
(trust+enforce, buggy scenarios): 3/3 solved, 0/8 leaks vs. 2/3 and 3/8
without enforcement; baseline+enforce control: 0/3, 5/8 leaks (vacuous
without a contract). Full trust+enforce set: 6/6 solved, 0/8 leaks, 0/6
rejections; oracle clean in both arms. Boundary escapes are 0 in every
enforce-on run — generated obligation, model-independent.

## 4d. Multi-Target Compilation (new — direct response to Lean 4's ecosystem gap)

Ward's core calculus compiles to existing host runtimes rather than inventing a
parallel ecosystem, following Dafny's proven approach rather than Lean 4's:

- **Frontend target:** compiles to TypeScript/JavaScript, so verified UI logic
  can still render through React, Vue, or any existing component framework — the
  verified code is the state/logic layer, not a replacement for the rendering
  ecosystem.
- **Backend targets:** compiles to whatever the host team already runs — Go,
  Python, JVM, Node — mirroring Dafny's own multi-target compiler design (C#,
  Java, JavaScript, Go, Python), which is explicitly built "to integrate with
  your existing workflow."
- **WASM and native binary targets (added — precedented, not speculative):**
  for performance-critical or systems-adjacent code (game logic, WebGPU
  compute, embedded/native deployment), Ward should also compile to WebAssembly
  and native binaries. This is not a novel ask: F* already does exactly this —
  its low-level subset (Low*) compiles to WebAssembly (formalized in a
  peer-reviewed IEEE S&P 2019 paper, shipping today as a production npm
  package, WHACL*) *and* to C from the same verified source, with that C output
  running inside Firefox and WireGuard. Ward's WASM/native path should follow
  this exact model: one verified core, multiple compilation backends, rather
  than a separate unverified fast-path language.
- **Why this matters more for Ward than for Dafny:** Dafny's multi-target
  compilation already solves the "integrate with a backend team's stack"
  problem. Neither Dafny nor Lean 4 targets WASM or native binaries for
  performance-sensitive/game-adjacent code — this is a genuine differentiator
  for reaching game and systems developers, not just enterprise backend teams,
  and is the single component most directly relevant to a "used broadly like
  Rust" adoption goal, since Rust's own path to ubiquity leaned heavily on
  exactly this profile (systems-level C interop, WASM, game-engine adoption).
- Lean 4's lack of an equivalent multi-target story is precisely why a
  developer attempting a Lean 4 backend had to hand-roll an HTTP server from
  raw sockets. Ward inherits Dafny's and F*'s solved problems here rather than
  repeating Lean's unsolved one.

## 4g. Domain Contract-Template Library (new — extends §6's composition-first idea)

Writing `requires`/`ensures` contracts from scratch is still real work, and
most real-world business logic reuses the same handful of correctness
patterns — conservation of value, no double-spend, monotonic counters,
idempotent writes, access-control invariants. Ward should ship a library of
**reusable, parameterized contract templates** for these, so a developer (or
the model) instantiates a known-correct shape rather than hand-deriving
invariants each time.

This is not a novel idea — it's applying an established research area,
**specification/requirement patterns** (a lineage going back to reusable,
logic-based property templates, with a NASA tool — FRET — built specifically
around this, and separate work applying the same idea to smart-contract
domain-specific properties) — to Ward's contract syntax specifically. The
content-addressed registry from §6 is the natural home for these: contract
*templates* become first-class, searchable, composable citizens alongside
verified *functions*, so "conservation of value for a ledger transfer" is a
lookup, not a from-scratch derivation, for every developer or model that needs
it after the first one proves it out.

## 4e. Verification Boundary for Unverified Libraries (revised — not novel, correctly attributed)

**Correction:** this section originally claimed to identify "the largest
genuinely unfilled gap." That was an overclaim, retracted in §2b. The correct
framing: full-stack development is structurally dependent on libraries that
will never be formally verified — React, Express, ORMs, payment SDKs, cloud
APIs — and Dafny/Lean/SPARK don't build a boundary mechanism for this into the
core language. But the *underlying technique* is a real, established research
area: **Gradual Verification** (Bader et al., 2018), including work
specifically on formalizing the boundary between verified and unverified code,
studied at industrial scale (an Amazon Science paper addresses the identical
problem), and already implemented for AI-agent-authored formally verified code
by an existing project (**verity**), which uses machine-readable trust
annotations and a `--deny-local-obligations` mode to fail closed on anything
left unverified — closely matching what's proposed below.

Ward's contribution here is applying this established technique specifically
to LLM-authored full-stack code, not inventing the mechanism:

```ward-surface
extern fn stripe_charge(amount: Money, token: CardToken) -> Result<Charge, PaymentError>
  requires amount > 0
  ensures result.is_ok() implies result.unwrap().amount == amount
  trust: "stub — Stripe SDK v14.2, not independently verified"
```

- Inside the boundary (Ward core code), the verifier reasons only about the
  *stated* contract — not the library's real implementation — the same
  optimistic-assumption approach gradual verification formalizes generally.
- The `trust:` annotation is mandatory and machine-readable, following
  verity's precedent of an auditable, queryable trust report rather than
  silent absorption into "it compiled."
- What remains untested, and is flagged honestly in §2b: whether this
  established technique holds up specifically for the kind of high-churn,
  loosely-specified third-party APIs common in web/full-stack development
  (vs. the more constrained domains — smart contracts, security primitives —
  where it's been demonstrated so far).

## 4f. Full-Stack Effect Model (extends §4's effect tracking)

Dafny's effect tracking (`reads`/`modifies`) is heap-focused — it can express
"this function mutates this field," but has no native vocabulary for the effects
that dominate full-stack code. Ward's effect kinds are extended explicitly:

- `net` — network/HTTP calls (subsumes and generalizes what §4e's FFI stubs declare)
- `async` — non-blocking/awaited operations, with the type system tracking
  whether a value is settled or pending
- `render` — UI state mutation that triggers a re-render (frontend-specific;
  lets the checker catch, e.g., a render-effect inside a pure computation path)
- `db` — persistence-layer access, parameterized by which store/table, enabling
  the same version/schema-drift protection §4 already gives package dependencies
- `fs`, `mut`, `partial` — filesystem, heap mutation, and non-totality, as before

A function's declared effect set is still enforced at elaboration time (§5) —
violating it is a compile-time error, not a runtime surprise — but now the
vocabulary actually covers what full-stack applications do, not just what
algorithmic/systems code does.

---

## 4g. Reusable Contract Templates (new — with a required safety discipline)

Prompted by a direct question about whether pre-built "business logic" contract
macros (e.g., a ledger-balancing template, a currency-conversion template) would
undermine verification rather than help it. The risk is real and specific:
**a template that gets trusted rather than re-checked recreates the vacuous
verification problem already documented in this research (models satisfying a
checker with a trivial or mismatched spec).**

The required discipline, to keep templates safe: a contract template is
**expansion sugar, never a pre-proven shortcut.**

- A template expands, deterministically, into a full, specific proof obligation
  written in terms of the actual function it's attached to.
- The SMT solver checks that expanded obligation fresh, every time, against the
  real code — exactly as if it had been hand-written. Nothing about using a
  named template skips or weakens this step.
- This mirrors, deliberately, the distinction already present in §6: reusing a
  *proven function* from the content-addressed library is safe because the
  hash guarantees the implementation is fixed and unchanged. Reusing a
  *contract template* is different — the code it's attached to is new each
  time, so the proof obligation must be re-derived and re-checked each time
  too. Conflating the two would be the exact failure mode this section exists
  to prevent.

Templates are therefore a legitimate ergonomic improvement over hand-deriving
predicates from scratch (§3), and do not represent a weakening of what gets
verified — provided this expand-then-recheck rule is enforced structurally,
not left as a convention someone could skip under time pressure.

## 4h. Compilation-Target Trust Gap (new — a real, load-bearing limitation)

Prompted by the same challenge: does multi-target compilation (§4d) undermine
verification's purpose? Partially, and this needs to be stated plainly rather
than glossed over.

**The core problem, well-established in verification research:** a proof
covers source-level semantics. Once source is compiled by an *unverified*
compiler to a target runtime, the guarantee only holds if that compiler is
correct — an assumption, not a proven fact. This is precisely why CompCert (a
formally verified C compiler) exists as its own multi-decade research project:
ordinary compilers can and do miscompile code in ways that silently invalidate
a source-level proof. Dafny already accepts a version of this trade-off
(verified logic, unverified backend compiler to C#/Java/JS/Go/Python) and it
remains useful in practice — but it is a real, acknowledged dilution of "fully
verified," not a detail to gloss over.

**This is materially worse for one specific target named in this document's
motivating discussion: WebGPU.** Translating WASM to WGSL (WebGPU's shading
language) is not a solved, deterministic compilation step — current published
work does this via small language models specifically because the semantic gap
between WASM's stack-based execution model and WGSL's structured, GPU-centric
model resists conventional compilation. Routing Proven-tier code through an
SLM-based translation step would be a strictly worse version of the
already-imperfect Dafny trade-off: the "compiler" doing the final translation
would be a probabilistic model, not a deterministic (if unverified) program.

**Required rule, connecting this directly to §4c's tiered verification:**
Proven-tier code must never target WebGPU (or any target requiring a
non-deterministic/model-based translation step). This isn't a limitation
imposed on top of the tiering system — it's a natural consequence of it.
Game graphics, animation, and shader code were never intended to be
Proven-tier in the first place (§4c's own example: "blazing-fast, unverified
code for graphics" is explicitly Tested-tier). Proven-tier code stays on
well-understood, deterministic backends (native compilation, or WASM without
a further GPU-translation hop) — the same category of trust Dafny already
accepts, not a new or larger one. Ward's marketing or documentation should
never claim "verified, runs on GPU" as a single statement — those are, and
should remain, two separate claims about two different tiers of code.

---

## 5. Elaboration

The deterministic bridge between surface and core. Not AI — a conventional
compiler pass, auditable and reproducible.

Responsibilities:
1. Expand `requires`/`ensures` into core-level refinement predicates.
2. Resolve every implicit (types, coercions, numeric widening) into an explicit
   core term.
3. Infer and insert effect annotations by static analysis of what the function body
   actually touches.
4. Infer ownership/linearity annotations for any value flowing through a
   linear-typed capability.
5. Resolve every dependency reference against a declared, pinned version range —
   failure here is a compile-time elaboration error, not a runtime surprise.

If elaboration cannot resolve something unambiguously (e.g., an implicit that
could go two ways), it is a **hard elaboration error**, surfaced to the model
immediately — the program never reaches the verifier in an ambiguous state.

---

## 6. Composition-First Standard Library

The single largest lever, and the one most different from a "stricter type
system" framing.

- A **global, content-addressed, proof-verified function registry** (Unison's
  model, extended). Every function is identified by a hash of its content *and*
  its proven contract — not by name, not by file path.
- Once a function's proof is checked, it is **permanently trusted** — it never
  needs to be re-verified or regenerated by any future model.
- The model's default behavior is **search-and-compose**: given a required
  contract, search the registry for an existing function that already satisfies
  it (exact match or provably-subsuming match), and compose from proven parts.
  Writing genuinely new logic is the fallback, not the default.
- Effect: the space of "novel, unverified code" — exactly where GitChameleon and
  SWE-bench Pro show errors concentrate — shrinks every time the ecosystem is
  used. The language gets *safer over time* by construction, not just faster.

This reframes the target metric. Instead of "how often does the model write
correct new code," the operative question becomes "how much new code does the
model even need to write" — and the answer, over time, trends toward "less."

---

## 7. Verification & Repair Loop

- **Granular proof obligations.** The core calculus forces every function to
  decompose into small, independently provable sub-obligations. A failure
  localizes to one unit, not "somewhere in these 200 lines" — turning debugging
  into pointing rather than searching.
- **Structured, machine-parseable errors.** The verifier never emits prose for
  the model to guess at. It emits `(location, violated-obligation, counterexample)`
  triples. These are translated back into surface-language terms before being
  shown to the model, keeping the repair loop legible in the language the model
  is actually fluent in (§3), even though checking happens in the strict core.
- **Adversarial critic role, built into the toolchain, not bolted on after.**
  A second pass — same or different model — is required to actively try to
  construct a counterexample to the stated contract, not just run the tests the
  generator itself wrote. This closes the "model grading its own homework" gap
  more forcefully than test generation alone.
- **Confidence-typed units.** The surface language includes a first-class
  annotation for per-unit model confidence (derived from token probabilities /
  self-consistency across samples, not a comment). Low-confidence units are
  automatically routed to heavier verification (more test generation, more
  adversarial rounds, multiple independent regenerations diffed against each
  other); high-confidence, simple units skip that cost. This targets verification
  compute precisely where benchmarks show errors cluster, instead of spending it
  uniformly.
- **Property-based tests generated mechanically from the contract**, never
  written by the model — removing the failure mode where a model writes code and
  tests that agree with each other but not with reality.

---

## 8. What This Would Take to Actually Build

Being direct about scope, since the temptation is to treat a design doc as a
finished thing:

| Phase | Work | Rough scale |
|---|---|---|
| 1. Core calculus + checker | Formalize the type/effect/linearity system, build a checker (likely SMT-backed, à la Liquid Haskell/F*) | Multi-person-year, PL-research-grade |
| 2. Elaborator | Deterministic compiler from surface to core | Substantial but conventional compiler engineering |
| 3. Grammar-constrained surface decoder | Adapt existing constrained-decoding infrastructure to the surface grammar | Weeks-months, mostly integration work |
| 4. Content-addressed registry | Build the storage/lookup/proof-caching system (Unison is the closest existing precedent) | Multi-month, less research-heavy |
| 5. Bootstrapping model fluency | **The largest unknown.** No model is natively fluent in a brand-new language on day one — fluency comes from training-data volume, not design quality. This requires generating large synthetic corpora (via translation from Python/TS, or self-play against the checker) and fine-tuning/RL against the verifier as reward signal — closely analogous to how Lean's recent AI-proof-generation gains were achieved | Months-years, compute-intensive |

Phase 5 is the honest bottleneck. Everything in §2-§7 is design work that follows
fairly directly from known, proven techniques recombined. Phase 5 is the part with
real research risk: **there is no existing evidence that checker-driven RL
generalizes from a narrow, clean domain (Lean's math proofs) to messy, stateful,
real-world software with side effects, concurrency, and shifting dependencies** —
which is exactly the harder territory SWE-bench Pro and GitChameleon are built to
probe.

**Phase 1 confidence update, from research:** SMT-backed verification of real,
consequential software has a stronger production track record than the original
draft implied. F* — the exact tool cited for Phase 1's checker — was used in
Project Everest (2016-2021) to build verified cryptographic and protocol code
now deployed in the Windows kernel, Linux, Firefox, and Python, with roughly
600,000 lines of verified code and proofs maintained under continuous
integration. This is real evidence that Phase 1 (the checker) is the more
tractable half of this proposal; Phase 5 (bootstrapping model fluency) remains
the comparatively unproven half.

---

## 9. Explicit Non-Claims

- This design has **not** been implemented or tested. No accuracy number here is
  a measured result — every number in this conversation came from *existing*
  languages and models, not from Ward.
- Nothing here guarantees 90%+ first-pass generation. The realistic claim is
  narrower: this architecture should push **converged, checker-verified**
  accuracy higher than any current combination, by (a) deleting the syntax-error
  failure class, (b) shrinking the novel-code surface area over time, and
  (c) targeting verification effort where models are least confident — not by
  making single-shot generation itself dramatically smarter.
- The single largest open risk is Phase 5 (§8): whether checker-driven training
  generalizes beyond narrow formal domains to general-purpose, stateful software.
  That is an empirical question a real research program would need to answer.

---

## 9a. Fact-Check Log (full pass, every section)

Every precedent cited in this document has now been checked against current
sources, section by section.

**§3 Surface Layer**
| Claim | Status | Note |
|---|---|---|
| Design-by-contract (`requires`/`ensures`/`old`) mirrors Eiffel | **Confirmed** | Eiffel's actual keywords are `require`/`ensure`/`old` — near-identical to Ward's surface syntax. |
| Contracts need native language integration, not bolt-on annotations | **Confirmed, strengthens the design** | Direct sourced argument: "Design by Contract can only work if tightly integrated with the fabric of the language" — evidence against treating contracts as an add-on, and a direct vindication of making them mandatory surface syntax rather than optional comments. |
| Grammar-constrained decoding deletes syntax errors | **Confirmed but incomplete — already corrected** | See prior correction: type-constrained decoding (74.8% error reduction) is the stronger, separately-verified technique; grammar-only gets you valid syntax, not valid semantics. |

**§4 Core Calculus**
| Claim | Status | Note |
|---|---|---|
| Idris/Liquid Haskell-style refinement/dependent types | **Confirmed** | LiquidHaskell verified 10,000+ lines of real Haskell libraries (containers, bytestring, text, xmonad) via SMT-backed refinement types — a real production track record, not just a research idea. |
| Full dependent type systems (Coq/Idris/Agda) are undecidable for validity checking | **Confirmed** | Directly sourced: "their expressiveness comes at the cost of making logical validity checking undecidable, thus rendering verification cumbersome." This validates the §8 risk flag about proof-search intractability when stacking dependent types with other strict features. |
| Koka: effects tracked in every function's type | **Confirmed** | Matches Koka's own documentation. |
| Move: linear/resource types prevent asset duplication/loss | **Confirmed** | Move resources "can never be copied or implicitly discarded, only moved" — enforced statically by the type system, in production on Aptos/Sui at real transaction scale. |

**§5 Elaboration** — no independently-checkable external claim (this is original architecture, not attributed to a precedent). No correction needed; flagged as design, not established fact.

**§6 Composition-First Standard Library**
| Claim | Status | Note |
|---|---|---|
| Unison: functions identified by content hash | **Confirmed** | Accurate. Additional finding: Unison hit v1.0 in Nov 2025, but its own team says "runtime performance isn't great" and it isn't aimed at replacing Go/Rust — a real precedent, not yet proven at general-purpose production scale beyond its own creators' usage. |

**§7 Verification & Repair Loop**
| Claim | Status | Note |
|---|---|---|
| Checker-driven RL improves formal-proof accuracy | **Confirmed, originally understated** | One framework improved autoformalization pass@1 4-6x (ProofNet 4.04%→26.15%); a synthetic-data-trained prover reached 46-52% on miniF2F vs. GPT-4's 23%. Mechanism (compiler-as-reward-signal) matches exactly. |
| Adversarial critic / property-based test generation as distinct from generator-written tests | **Confirmed, better-precedented than originally stated** | CEGIS (Counterexample-Guided Inductive Synthesis, established since PLDI 2007, Solar-Lezama et al.) is exactly the learner/verifier-with-counterexamples loop Ward's adversarial critic describes — not a loose analogy but a direct, decades-established match. Property-based test generation from specifications (QuickCheck, 1990s-present; QuickChick for Coq) is likewise a real, mature technique, not a speculative one. Both entries upgraded from "design choice" to "established precedent." |

## 9c. Final Closing Pass

Every remaining unverified claim in the document has now been checked:

| Claim | Status |
|---|---|
| CEGIS as precedent for the adversarial critic | **Confirmed** — real, established since 2007, matches the design closely |
| Property-based testing generated from specs (QuickCheck-style) | **Confirmed** — mature, decades-old technique |
| F*/Project Everest production track record | **Confirmed, strengthens Phase 1 confidence** |
| Idris totality checking, incl. its limits | **Confirmed, including the honest caveat that it's conservative/imperfect** |
| Type-constrained decoding numbers | **Confirmed at the primary source, but corrected to distinguish compile-error reduction (74.8%) from functional accuracy gain (+3.5-8%)** |

**State of the document after three verification passes:** every named precedent,
technique, and specific number now traces to a primary or clearly credible
source, and one meaningful correction was made (the type-constrained decoding
figure). No claim was found to be fabricated. Two claims (CEGIS, property-based
testing) turned out to be more directly precedented than the original draft
credited. The only claim in this entire document that remains and will always
remain unverified by research — because it is not a fact about the world but a
prediction about an unbuilt system — is the central one: that assembling these
verified pieces together as Ward proposes would achieve 90%+ accuracy across
broad, realistic coding tasks. That is the one claim no amount of further
searching can close.

**§8 What This Would Take**
No independently fact-checkable claims — this is a scoping/timeline estimate,
explicitly presented as reasoning rather than measured fact. No correction
needed, but worth restating: these are engineering-judgment estimates, not
sourced projections.

**§10 Precedents table** — every entry cross-checked above; all accurate as
listed.

**Overall result:** no fabricated or contradicted claims found across the full
document. One correction was made in §3 (grammar- vs. type-constrained
decoding). Several claims turned out to have *more* supporting evidence than
originally presented (Lean/RL gains, LiquidHaskell's production track record,
Move's live deployment at Aptos/Sui scale, Eiffel's own literature backing the
"contracts must be native" design choice). This fact-check verifies that the
building blocks are real and accurately described — it still cannot and does
not verify the document's central hypothesis, since the specific combination
proposed here has not been built or tested by anyone.

## 9b. Second Pass: Primary-Source Verification (numbers previously taken on trust)

A further round checked the two most load-bearing *specific numbers* directly
against primary sources rather than secondary summaries.

| Claim | Status | Note |
|---|---|---|
| Type-constrained decoding: 74.8% / 9.0% figures | **Confirmed at the primary source (paper's own Table 2), but the framing needed correction** | The 74.8% figure is real and precisely stated in the PLDI 2025 paper itself. But it measures *compilation-error* reduction, not overall functional accuracy. The same paper's pass@1 (actually-correct-code) improvement is much smaller: +3.5% synthesis, +5.0% translation, +37.0% repair. §3 now states both numbers so the 74.8% isn't read as a proxy for overall accuracy gain — it corrects a genuine risk of the original citation implying more than the paper supports. |
| F*/Project Everest: real production track record for SMT-backed verification | **Confirmed, previously understated** | F* (the tool named as Ward's likely checker technology) verified real cryptographic and protocol code now shipping in the Windows kernel, Linux, Firefox, and Python — roughly 600,000 lines of verified code and proofs. This is stronger evidence for Phase 1's feasibility than the original draft implied, and has been added to §8. |
| Idris totality checking is real but imperfect | **Confirmed** | Idris's own documentation states the totality checker is "necessarily conservative" (the halting problem is undecidable) and that "the totality checker is not perfect" — with at least one documented case of it being too permissive. Added as a caveat to §4; this reinforces, rather than undermines, the earlier-confirmed point about undecidability being the central research risk in §8. |

This second pass changed the document's confidence calibration in two
directions at once: **more confidence** in Phase 1 (the checker) being
practically achievable, given F*'s real deployment history, and **more
precision, not more confidence**, on the type-constrained decoding number,
since its most impressive figure (74.8%) applies to a narrower thing
(compile-clean code) than the document originally implied (working code).

## 10. Nearest Existing Precedents (for anyone prototyping this)

- **Surface/core split, elaboration:** Lean 4, Idris 2, F*, TypeScript→JS
- **Refinement/dependent types:** Liquid Haskell, Idris, F*
- **Effect tracking:** Koka, Eff; heap-focused precedent: Dafny's `reads`/`modifies`
- **Linear/ownership types (inferred, not hand-written):** Rust's model, but
  inference-driven rather than annotation-driven — closer to how borrow checking
  is *inferred* internally in some research systems (e.g., region inference work)
- **Content-addressed, proof-cached code:** Unison
- **Design-by-contract surface syntax:** Eiffel
- **Grammar-constrained decoding:** existing JSON-schema/tool-calling constrained
  generation, extended to a full language grammar
- **Checker-driven RL on generation:** the training approach behind recent
  Lean 4 proof-generation gains — the closest real precedent for Phase 5, though
  in a narrower domain than general software
- **Verified WASM/native multi-target compilation from one source:** F*/HACL*
  (Low* compiles to both WebAssembly, formalized in IEEE S&P 2019, and C,
  currently shipping in Firefox and WireGuard) — the model for Ward's §4d
  WASM/native addition
- **Reusable contract/specification templates:** the specification-patterns
  research lineage (NASA's FRET tool; domain-specific property catalogues for
  smart contracts and other domains) — the model for Ward's §4g
- **Self-healing verifier-feedback repair loop, in production, for AI code
  generation specifically:** Dafny (DafnyPro, AxDafny) — the single closest
  overall precedent to Ward's §7 verification loop, already achieving 82-91%
  verify success rates with existing open models
- **Multi-target compilation to real developer ecosystems:** Dafny (compiles to
  C#, Java, JavaScript, Go, Python) — the model for Ward's §4d, and the exact
  capability Lean 4 lacks
- **Industrial-scale formal verification precedent:** Dafny at AWS (authorization
  engine); F*/Project Everest (~600K lines verified, shipping in Windows kernel,
  Linux, Firefox, Python)
- **Gap Ward fills that neither Dafny nor Lean 4 currently addresses as a named
  language feature:** a trusted FFI boundary for unverified third-party
  libraries (§4e) and a full-stack-specific effect vocabulary (§4f)

## 11. Fact-Check Addendum: Dafny/Lean Full-Stack Comparison Round

| Claim | Status | Note |
|---|---|---|
| DafnyPro: 86% on DafnyBench with Claude Sonnet 3.5, +16pts over prior SOTA | **Confirmed**, POPL 2026 accepted paper | Real, current, peer-reviewed |
| Self-healing Dafny pipeline: 90.91% (Gemma 4-31B), 0%→81.82% (GPT-OSS 120B) | **Confirmed**, arXiv 2604.22601 | Uses verify@k metric with uDebug functional-validation safeguard against vacuous verification |
| AxDafny reviewer-LLM anti-cheating check | **Confirmed**, arXiv 2606.32007 | Directly precedented Ward's adversarial critic (§7) more closely than originally credited |
| Dafny used at AWS for authorization engine verification | **Confirmed**, cited in Dafny compiler-verification literature | Real industrial deployment, not just research |
| Dafny multi-target compilation (C#/Java/JS/Go/Python) | **Confirmed**, Dafny's own site and 4.0 release notes | Explicitly framed by Dafny's own team as workflow-integration, matching §4d's rationale |
| Lean 4 lacks web/backend ecosystem; no HTTP framework available | **Confirmed**, first-person developer account | Directly motivates §4d's multi-target design choice over Lean's approach |
| Lean 4 frontend usage is experimental/hobbyist only | **Confirmed**, single-developer blog account, explicitly not expecting adoption | Same conclusion, independent source |
| Dafny verification effort "significant and difficult to predict" in practice | **Confirmed**, published case-study paper (arXiv 2301.03224) | Directly motivates §4c's tiered verification design |
| Dafny effect tracking limited to heap (`reads`/`modifies`), no async/net/UI vocabulary | **Inferred from Dafny's documented feature set, not a claim any source explicitly denies** | Reasonable characterization given no evidence found of Dafny supporting these effect kinds; flagged as inference rather than a directly sourced negative claim, since absence of evidence is weaker than a positive citation |

**One item flagged for honesty:** the last row is an inference from what Dafny's
documentation *does* claim, not a sourced statement that Dafny explicitly lacks
these features. It's a reasonable read of the evidence gathered, but weaker in
kind than the other rows in this table, and should be verified directly against
Dafny's language reference before being treated as settled if this document is
used to make resourcing decisions.

## 12. Fact-Check Addendum: Novelty Correction and SPARK Comparison

Prompted by a direct challenge to whether Ward is a genuine redesign or a
relabeled copy, this round specifically checked whether Ward's claimed novel
components have prior art, and added SPARK/Ada as a third comparison baseline.

| Claim | Status | Note |
|---|---|---|
| Trusted FFI boundary (§4e) is a novel Ward contribution | **Overclaim — retracted** | Gradual Verification (Bader et al., 2018) is an established research area addressing exactly this problem, including work titled "Specifying the Boundary Between Unverified and Verified Code" and an Amazon Science paper on the same problem at industrial scale. A live project, verity, already implements near-identical machine-readable trust annotations for AI-agent-authored verified code. §4e rewritten to attribute correctly. |
| SPARK/Ada: contract-based, SMT-backed (GNATprove/Why3/CVC5/Z3), used in high-integrity industry | **Confirmed** | Matches Dafny's general approach; independently established, not derived from Dafny |
| SPARK + LLM: Marmaragan generates correct SPARK annotations for 50.7% of benchmark cases (GPT-4o) | **Confirmed**, published paper (arXiv 2502.07728) | Notably weaker than Dafny's 82-91%, a real data point for cross-language comparison |
| Very recent AI-agent + SPARK: fully verified crypto/IPsec/TLS/PQ-CA stack, 49,280 proof obligations discharged | **Confirmed**, arXiv 2607.14340, dated ~2 weeks before this check | Strong, current counter-evidence to treating SPARK as a weak AI-verification target; success appears to depend heavily on domain (security-critical, well-specified) rather than the language alone |
| SPARK's package ecosystem is small (~400 vs. ~1.3M npm) | **Confirmed** | Same structural gap as Lean 4, different cause (small community vs. no framework at all); reinforces §2/§4d's ecosystem-integration rationale generally, now across three languages rather than one |

**Net effect of this round:** one real correction (§4e's novelty claim,
retracted and reattributed), one new comparison baseline added (SPARK) that
reinforces rather than undermines the document's core positioning claim (all
three existing verification-first languages share the same full-stack/ecosystem
gap), and one honest acknowledgment that Ward's remaining novel claims are
narrower than originally presented (§2b). This is a stronger, more accurately
scoped document than the previous draft, not a weaker one — the corrections
remove overclaiming without removing the underlying case for the research.

## 13. Fact-Check Addendum: WASM/Native Compilation and Contract Templates

Prompted by comparison against an independently-generated design sketch, this
round checked two additional proposed components: multi-target compilation to
WASM/native binaries (§4d addition), and reusable contract templates (§4g, new).

| Claim | Status | Note |
|---|---|---|
| F* compiles its Low* subset to WebAssembly | **Confirmed**, IEEE S&P 2019 (Protzenko et al.) | Produces WHACL*, a WebAssembly build of the verified HACL* crypto library, distributed as a production npm package (hacl-wasm) |
| The same verified F* source also compiles to C, shipping in real products | **Confirmed** | HACL*'s C output is used in the Firefox browser and the WireGuard VPN — real, current production deployment, not a research demo |
| Specification/requirement patterns (reusable contract templates) are an established research area | **Confirmed** | A documented lineage of reusable, logic-based property templates, including a NASA-built tool (FRET) and separate work applying the same idea to domain-specific smart-contract properties |

**Consequence for §2b's novelty inventory:** both additions in this round are,
like the FFI boundary before them, applications of established techniques
rather than new inventions — added to the "not novel, reused deliberately"
list, not the "genuinely novel" one. This keeps the document's novelty claims
consistently narrow rather than letting new sections quietly reintroduce
overclaiming. The convergence between this independently-generated blueprint
and Ward's existing design (both landing on gradual verification and
AI-in-the-loop proof automation independently) is worth noting as a mild
positive signal that these are the right levers — not as evidence the specific
combination has been validated, which still requires building it.
