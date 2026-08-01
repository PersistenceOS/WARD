<div align="center">

# 🛡️ WARD

### A Frontier Verification Language for Full-Stack AI Development

**Write. And Reason. Deductively.**

Ward is a Python/TypeScript-shaped surface language that **deterministically elaborates into Dafny** — giving LLM-authored programs machine-checked correctness without forcing models to write off-distribution formal syntax.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Dafny](https://img.shields.io/badge/Dafny-4.11.0-important.svg)](https://github.com/dafny-lang/dafny)
[![Z3](https://img.shields.io/badge/Z3-4.12.1-orange.svg)](https://github.com/Z3Prover/z3)
[![Tests](https://img.shields.io/badge/suites-57%2F57%20green-success.svg)](phase0/README.md)
[![Phase 0](https://img.shields.io/badge/phase-0%20complete-9cf.svg)](files/ward-phase0-solo-execution-plan.md)
[![Phase 1](https://img.shields.io/badge/phase-1%20conditional%20GO-ff69b4.svg)](files/PHASE1_REPORT.md)

---

</div>

## The 60-second version (no jargon)

> **TL;DR:** Ward = write code + write a promise about what the code must do — and the computer *proves* the code keeps the promise, before anything runs.

**The problem:** When an AI writes code — or a human writes code fast — the code can be wrong, even when it runs. "It works" can hide bugs that explode later: money disappears, passwords leak, databases get corrupted.

Most languages check *"does it run?"* WARD also checks *"is it right?"*

**The idea in one sentence:**

> You write code. You also write a promise about what the code must do. The computer **proves** the code keeps its promise — before anything runs.

Think of it like cooking with a math teacher standing over your shoulder. You write "add 2 cups of flour." The teacher doesn't just watch you pour — they *calculate* whether the result will be a cake before you start. If the recipe can't produce what it promised, they stop you.

```ward0
fn withdraw(balance: int, amount: int) -> int
  requires amount > 0                // the promise BEFORE: amount must be positive
  requires amount <= balance         // the promise BEFORE: can't take more than exists
  ensures result == balance - amount // the promise AFTER: what you get back is exactly right
{
    return balance - amount;
}
```

WARD reads the three promises and **mathematically proves** the code inside the `{ }` keeps them. Not "probably," not "trust me" — *proven*, the way a math theorem is proven.

**Where it gets clever — three speeds of checking:**

Not everything needs a full math proof — that would be slow and painful. So WARD has three levels:

1. **Tested** — for boring code (webpage buttons, formatting). It runs sample tests automatically. Good enough.
2. **Contracted** — for normal business logic. It checks the promises, with a time limit.
3. **Proven** — for code where being wrong is catastrophic (payments, logins, moving money). It proves everything, no shortcuts.

**One more trick — the bouncer:**

Your code calls other people's code (libraries). You can't prove *their* code is right. So WARD puts a bouncer at the door: when the library hands back a result, the bouncer checks it against the promise. If the library lies — returns "success" when it should have said "failed" — the bouncer stops it at the door. A wrong answer literally cannot sneak through labeled "OK."

**What it's designed for:**

WARD is built for a specific world: **AI writing full-stack apps** — websites, backends, payment flows — where a model writes a lot of code fast and nobody has time to hand-check every line. WARD's job is to make "the AI wrote it" and "it's proven correct" both true at once.

**Honest status:** WARD is a research project, not a finished language. Today's prototype (`ward0`) writes code in a Python-like style, translates it to another language ([Dafny](https://github.com/dafny-lang/dafny)) that does the proving, with [Z3](https://github.com/Z3Prover/z3) as the math engine. The end goal is WARD standing on its own — its own core, its own proof engine (see [Where we are vs. where we're going](#where-we-are-vs-where-were-going)). The ideas it tests are already validated by real, pre-registered experiments (see below).

## How WARD works with an AI model (no jargon)

WARD isn't a replacement for the AI — it's the AI's **strictest teacher**. The model still writes the code. WARD's job is to make sure the code actually does what it claims, and to keep guiding the model until it does.

**The loop:**

```
 model writes code + promises (in WARD's friendly syntax)
        │
        ▼
 WARD checks — proves the promises, mathematically
        │
   ┌────┴──────────┐
   │   passes      │  fails → "line X breaks promise Y in situation Z"
   └────┬──────────┘          → model fixes it → try again
        ▼
        ships
```

1. **The model writes in a language it's comfortable with.** WARD's surface syntax is Python/TypeScript-shaped, so the model stays in its comfort zone. It writes the code *and* the promises about what the code must do — no math, no proofs, just plain statements like "amount must be positive."

2. **WARD checks the promises the hard way.** The computer doesn't take "trust me." It *proves* the code keeps its promises, the way a math theorem is proven. No proof, no ship.

3. **If the proof fails, WARD says exactly why.** Not "something's wrong somewhere in these 200 lines" — but "this line breaks this promise in this situation." The model fixes it and tries again. This write → check → fix loop is the whole trick: the model converges on correct code because the teacher never blinks.

4. **WARD makes it hard to cheat.** A model could try to satisfy the checker with a promise that's technically true but meaningless. By design, WARD pushes back two ways: an adversarial pass actively tries to break the promise, and test cases are generated mechanically from the contract — so the model can't write tests that quietly agree with its own bugs.

5. **Over time, the model writes less and less.** WARD's design includes a library of already-proven functions. Instead of writing new code from scratch, the model looks up "a proven function that does this" and composes. Every verified function stays verified forever — so the amount of brand-new, unverified code shrinks as the library grows.

**What WARD does *not* do:** it doesn't make the model smarter or faster. It makes the model's mistakes *catchable*. An AI writing verified code is still an AI writing code — WARD is the unforgiving reviewer that catches what "it works" hides.

**What's true today vs. what's the plan:** the current prototype already runs the real write → verify → fix loop (generate → transpile → Dafny verify → hidden tests, with retries — and **0/20 boundary leaks** in the Phase-1 W arm). Today's hidden tests are fixed, written by the experiment designers, not the model. The adversarial critic, the proven-function library, and training models against the checker as a reward signal are the designed next steps (Phase 3+; training against the checker is the Phase-5 bottleneck).

## What is Ward?

Most AI code is checked by "did it compile" and "do the tests pass." Ward asks for more: **every AI-authored program is a proof obligation where proof is warranted, and a contract-checked, tested obligation everywhere else.**

> *"Compiles" means "provably satisfies its stated contract, at whatever tier that contract requires" — not "type-checks and looks plausible," and not "everything must be a theorem."*

Ward's surface layer looks like the Python/TypeScript the model already writes fluently. A deterministic elaborator compiles it to a strict core, and Dafny + Z3 prove the contracts. The model never hand-writes dependent types, ownership annotations, or proof scripts.

```ward0
// ward0 — surface syntax (Phase 0 reference solution, tier 3)
fn sum_between(xs: List<int>, lo: int, hi: int) -> int
  requires 0 <= lo and lo <= hi and hi <= len(xs)
  requires forall i in range(0, len(xs)) :: xs[i] >= 0
  ensures result >= 0
{
    var acc: int = 0;
    for i in range(lo, hi)
      invariant acc >= 0
    {
        acc += xs[i];
    }
    return acc;
}
```

## Why it exists

Dafny and Lean 4 already prove contract-based, SMT-backed, AI-repairable verification works — in production (AWS's authorization engine is Dafny-verified). But neither is built for **full-stack software that depends on huge, unverified, real-world ecosystems** (web frameworks, ORMs, cloud SDKs), and both force models off-distribution onto formal syntax (pinning pure-Dafny pass@1 around ~77%).

Ward attacks three levers instead:

| Lever | Ward's answer |
|---|---|
| **Familiar surface** | Python/TS-shaped `ward0` that elaborates deterministically to Dafny — no formal syntax for the model to write |
| **Tiered verification** | `Tested` → `Contracted` → `Proven`. Full proof only where it's consequential (auth, payments, data integrity); bounded contract checking and generated property tests everywhere else |
| **Trusted FFI boundary** | `extern fn` declarations with mandatory `trust:` annotations — the toolchain *generates* a runtime contract-check wrapper around every unverified-library call, so an over-grant can never cross the boundary as `Ok` |

## Architecture

```
┌──────────────┐   elaborate   ┌──────────────┐   check    ┌─────────────┐
│   Surface     │ ────────────▶│   Core        │──────────▶│   Verifier   │
│   ward0       │ (deterministic│   Calculus    │ (SMT /    │ Dafny + Z3  │
│   (model      │  compiler    │   (strict)    │  proof)   │ accept /     │
│   writes this)│  pass)       │               │           │ reject       │
└──────────────┘              └──────────────┘            └──────┬──────┘
       ▲                                                          │
       └────────── structured error, translated back ─────────────┘
                            into surface terms (repair loop)
```

| Component | Solves |
|---|---|
| Surface layer `ward0` | LLM off-distribution penalty on formal syntax (Dafny's ~77% pass@1 ceiling) |
| Deterministic elaboration | Forces the model to *state* contracts without hand-writing strict syntax |
| Repair loop | Checker-guided convergence instead of single-shot generation |
| Tiered verification | Dafny's documented pain point: unbounded, unpredictable verification effort |
| Trusted FFI boundary | Lets verified code call unverified real-world libraries safely |
| Multi-target compilation *(design)* | Avoids Lean 4's dead-end of no ecosystem to deploy into |

## Experimental status — honest numbers

Ward is a research project with pre-registered experiments and honest reporting. The current verdict after two phases: **surface syntax alone does not beat raw Dafny at pass rate; the boundary + tiered-verification thesis is validated and is the path forward.**

### Phase 0 — 62 tasks, model `opencode/deepseek-v4-flash-free`

| Claim | Result | Verdict |
|---|---|---|
| **A** — surface syntax outperforms raw Dafny | pass@1 exactly tied (54/62 both); pass@2 60/62 vs 61/62; McNemar **p = 1.0000** | ❌ Null — honestly reported |
| **B** — contract-stub trust boundary | **75%** relative reduction in violation escapes (2/8 vs 8/8); 0/6 valid callers rejected | ✅ Both gates met |
| **Phase-1 pivot** — generated boundary enforcement | Naive callers: **8/8 boundary leaks → 0/8** with enforcement; real model 6/6 solved, 0/8 leaks | ✅ Validated |

Toolchain: **57/57 suites green** (grammar 20/20, transpiler 24/24, harness 13/13); oracle **62/62 pass@1**.

### Phase 1 — four arms, 8 realistic module scenarios

| Arm | Solved | Boundary leaks | Verify time |
|---|---|---|---|
| **W** (ward0 + tiers + enforcement) | **8/8** | **0/20** | 12.3 s |
| **D** (raw Dafny) | 6/8 | 4/20 | 16.0 s |
| W−enforce | 7/8 | 1/20 | 14.2 s |
| W−tiers | 3/3 (arm halted, endpoint hang) | 0/8 | — |

**Gate results:** C1 (tiers work as designed) ✅ · C2 (boundary holds at scale, 0/20 leaks) ✅ · C3a (accuracy non-inferior, 8/8 ≥ 6/8−1) ✅ · C3b (effort < 0.7×) ❌ — ratio 0.77. → **Conditional GO for Phase 2 scoping.**

## Repository layout

```
├── README.md                 ← you are here
├── LICENSE                   Apache-2.0
├── docs/                     GitHub Pages landing site
├── files/                    design + experiment docs
│   ├── ward-language-design.md      full language design (surface/core split, §1–§13)
│   ├── PHASE1_REPORT.md             Phase 1 four-arm results
│   └── ward-phase2-scoping.md       core calculus + elaborator scoping
└── phase0/                   Phase 0/1 implementation + experiments
    ├── grammar/              ward0 grammar v0.1 (lark PEG) + tests
    ├── transpiler/           ward0 → Dafny transpiler
    ├── harness/              generate → transpile → dafny verify → hidden tests
    ├── wardcore/             typed core IR (Tier/EffectKind enums) — 15/15 tests
    └── benchmarks/           62 tasks (3 tiers) + 8 w-tasks + 6 B-scenarios
```

## Getting started

```bash
git clone https://github.com/PersistenceOS/WARD.git
cd WARD/phase0

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install lark

# run the full test suites
python -m unittest discover -s grammar -p "test_*.py"
python -m unittest discover -s transpiler -p "test_*.py"
python -m unittest discover -s harness -p "test_*.py"
python -m unittest discover -s wardcore -p "test_*.py"
```

Requires [Dafny 4.11.0](https://github.com/dafny-lang/dafny) and [Z3 4.12.1](https://github.com/Z3Prover/z3) on your PATH for live verification.

## The ward0 subset (v0.1, strict)

- `fn` definitions with `requires` / `ensures` contracts and `old(...)`
- Types: `int`, `bool`, `str`, `Unit`, `Result<T, E>`, `List<T>` (→ Dafny `seq<T>`)
- Bounded `for x in range(lo, hi)` with `invariant` clauses; `if`/`else`; `return`
- Quantified contracts: `forall x in range(lo, hi) :: e`, `exists ... :: e`
- Builtins: `len(xs)`, `is_ok` / `is_err` / `unwrap_ok` / `unwrap_err`
- `extern fn` declarations with optional `trust: "..."` annotations; `enforce_boundary=True` auto-generates a runtime contract-check wrapper around every extern call
- **No** classes, closures, recursion (totality by construction), unbounded loops, or generics beyond `Result` and `List`

## Where we are vs. where we're going

Ward's end-state is a **standalone language** — its own core calculus, its own checker, its own multi-target backends. What's built and measured today is the **ward0 → Dafny prototype**: Dafny + Z3 act as the proving engine, deliberately borrowed so the experiments could test the hypotheses that matter (surface syntax, FFI boundary, tiered verification) without first spending multi-person-years on a from-scratch checker.

| | Today (prototype) | Design (Phase 3+) |
|---|---|---|
| **Surface** | `ward0` — Python/TS-shaped, `requires`/`ensures` contracts | Same surface layer |
| **Core** | transpiled directly to Dafny | ward-core IR — strict typed core calculus (dependent/refinement types, effects, linearity) |
| **Verifier** | Dafny 4.11 + Z3 4.12 | Own SMT-backed checker (design §8, à la Liquid Haskell / F*) |
| **Backends** | — | Multi-target: JS/TS, Go, Python, JVM, WASM/native (design §4d) |

### Roadmap

- **Phase 0–1 — done.** `ward0` grammar v0.1 (lark PEG), `ward0 → Dafny` transpiler, evaluation harness, 62-task benchmark + 8 w-tasks + 6 B-scenarios; boundary + tiered-verification thesis validated (see [reports](files/)).
- **Phase 2 — scoped.** Core calculus + elaborator scoping ([design doc §8](files/ward-phase2-scoping.md)): ward-core IR v0.1 (`Tier`/`EffectKind` enums) complete; next: formalize the type/effect/linearity system.
- **Phase 3+ — next.** Standalone SMT-backed checker, full surface/core elaboration implementation, multi-target backends, composition-first verified library (design §6).

Dafny remains the benchmark Ward is measured against — not the destination. The design's position is explicit: Ward should *match* Dafny on core verification mechanics and differentiate on full-stack concerns (tiered verification §4c, trusted FFI boundary §4e, effect model §4f).

## License

[Apache-2.0](LICENSE) — permissive, with explicit patent grant. See the license for full terms.
