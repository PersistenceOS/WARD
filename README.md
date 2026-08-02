<div align="center">

# 🛡️ WARD

**Ward AI slop.**

### A verification language for AI-written full-stack software

**Write code. Write a promise. The computer proves the promise.**

*WARD — Why Another Rust Derivative? It isn't. It's the language that wards off AI slop.*

Ward is a Python/TypeScript-shaped language in which every function carries a contract (`requires` / `ensures`). A deterministic toolchain proves the contract with an SMT-backed verifier (today: `ward0` → Dafny + Z3). *"Compiles" means "provably satisfies its stated contract" — not "type-checks and looks plausible."*

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Dafny](https://img.shields.io/badge/Dafny-4.11.0-important.svg)](https://github.com/dafny-lang/dafny)
[![Z3](https://img.shields.io/badge/Z3-4.12.1-orange.svg)](https://github.com/Z3Prover/z3)
[![Tests](https://img.shields.io/badge/suites-57%2F57%20green-success.svg)](phase0/README.md)
[![Phase 0](https://img.shields.io/badge/phase-0%20complete-9cf.svg)](files/ward-phase0-solo-execution-plan.md)
[![Phase 1](https://img.shields.io/badge/phase-1%20conditional%20GO-ff69b4.svg)](files/PHASE1_REPORT.md)

---

</div>

## Quick overview of Ward

**The problem.** "It compiles" and "the tests pass" do not mean the code is right. For AI-written software — produced fast, never fully hand-checked — wrong code can ship and fail catastrophically: a payment, an auth check, a ledger.

**The idea.** You write code *and* a promise about what it must do. Ward proves the promise mathematically before anything runs. If the proof fails, it names the exact line and obligation, and the model fixes and retries.

**Who it's for.** AI agents writing full-stack applications — web, backend, payments — where correctness is consequential and verification effort must stay bounded.

```ward0
// ward0 — surface syntax
fn withdraw(balance: int, amount: int) -> int
  requires amount > 0                // promise BEFORE: amount is positive
  requires amount <= balance         // promise BEFORE: no overdraft
  ensures result == balance - amount // promise AFTER: result is exactly right
{
    return balance - amount;
}
```

## What Ward actually prevents

AI code slop isn't one thing. Ward targets the failure class that actually hurts — code that *looks* right, passes tests, and is subtly wrong — and is honest about the rest.

| Slop type | What it looks like | Ward's answer | Status |
|---|---|---|---|
| **Silent correctness slop** | compiles, passes tests, subtly wrong — an overdraft slips through, an over-grant returns `Ok` | contracts + verifier + generated boundary enforcement | ✅ **Measured**: 0/20 boundary leaks (Phase-1 W), 75% fewer escapes (Phase-0 B) |
| **Compile-failure slop** | output that doesn't parse | grammar-constrained decoding | 🔜 design (§3) |
| **Edge-case slop** | missing boundaries, empty inputs | contracts force the model to state edges | 🟡 partial |
| **Cheating slop** | trivial or vacuous promises that satisfy the checker | adversarial critic + mechanical test generation | 🔜 design (§7) |
| **Hallucinated-dependency slop** | wrong versions, imaginary APIs | effect tracking + dependency pinning | 🔜 design (§4/§5) |
| **Aesthetic slop** | bloat, over-engineering, duplication | — a verifier proves *correctness*, not elegance | ❌ not a goal |

**The honest pitch:** Ward doesn't make the model write better — it makes bad output *fail*. On the failure mode that actually matters, the measured numbers are **8/8 solved with 0/20 boundary leaks** (vs. raw Dafny's 6/8 and 4/20) and a **75% reduction** in contract-violation escapes. The dangerous kind of AI slop can't ship. The trade-off is real (verify-time ratio 0.77 vs. the 0.7× target) — that's exactly why verification is tiered. The long-term answer to the rest of slop is the composition-first library (design §6): write less code, so there's less slop to catch.

## Design

Ward does *not* compete with Dafny or Lean 4 on verification mechanics — it differentiates on three levers for the full-stack, AI-generation setting:

| Lever | Ward's answer |
|---|---|
| **Familiar surface** | Python/TS-shaped `ward0` the model already writes fluently; contracts are plain statements, not formal syntax |
| **Tiered verification** | Proof cost scales with consequence: `Tested` → `Contracted` → `Proven` |
| **Trusted FFI boundary** | `extern fn` + mandatory `trust:` annotations; the toolchain *generates* a runtime contract-check wrapper around every unverified-library call |

### Verification tiers

| Tier | Required | Typical code | Verify time* |
|---|---|---|---|
| **Tested** | property-based tests generated from the signature; no proof | UI glue, formatting, simple CRUD | ~0.0 s |
| **Contracted** | `requires`/`ensures` checked; bounded proof search | business logic, validation | ~1.3 s |
| **Proven** | full SMT proof, no timeout fallback | auth, payments, data integrity | ~1.4 s |

*smoke-example timings from Phase-1 w-tasks (w6/w3/w1).

### Architecture

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

### The AI loop

1. **Model writes** familiar code plus plain-language promises.
2. **WARD proves** the promises; structured errors name the failing line and obligation.
3. **Model repairs** against the error and retries — checker-guided convergence.
4. **Hard to game (design):** an adversarial pass tries to break the promise; tests are generated mechanically from the contract, so the model cannot write tests that agree with its own bugs.

## Results

Ward is a research project: pre-registered hypotheses, pre-registered gates, and negative results published alongside positive ones.

### Phase 0 — 62 tasks, model `opencode/deepseek-v4-flash-free`

| Hypothesis | Result | Verdict |
|---|---|---|
| **A** — surface syntax beats raw Dafny | pass@1 tied (54/62 both arms); pass@2 60/62 vs 61/62; McNemar **p = 1.0000** | ✕ null (reported) |
| **B** — contract-stub trust boundary | **75%** fewer violation escapes (2/8 vs 8/8); 0/6 valid callers rejected | ✓ gates met |
| **Enforcement** — generated runtime boundary checks | 8/8 boundary leaks → **0/8**; real model 6/6 solved, 0/8 leaks | ✓ validated |

Toolchain: **57/57 suites green** (grammar 20/20, transpiler 24/24, harness 13/13); oracle **62/62 pass@1**.

### Phase 1 — four arms, 8 module scenarios

| Arm | Solved | Boundary leaks | Verify |
|---|---|---|---|
| **W** (full stack) | **8/8** | **0/20** | 12.3 s |
| **D** (raw Dafny) | 6/8 | 4/20 | 16.0 s |
| W − enforce | 7/8 | 1/20 | 14.2 s |
| W − tiers | 3/3\* | 0/8 | — |

\*arm halted by an endpoint hang, not a correctness failure.

**Gates:** C1 tiers ✓ · C2 boundary at scale (0/20) ✓ · C3a accuracy (8/8 ≥ 6/8 − 1) ✓ · C3b effort ratio 0.77 vs target ≤ 0.7× ✕ → **Conditional GO for Phase 2.**

**Verdict:** the surface-syntax-superiority claim is dropped; the boundary + tiered-verification thesis is validated and is the path forward.

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| 0–1 | ✅ done | `ward0` grammar, transpiler, harness; 62 tasks + 8 w-tasks + 6 B-scenarios; thesis validated |
| 2 | 🔬 scoped | core calculus + elaborator ([scoping doc](files/ward-phase2-scoping.md)); ward-core IR v0.1 complete |
| 3+ | 🧭 next | standalone SMT-backed checker, multi-target backends, composition-first verified library ([design §6](files/ward-language-design.md)) |

**Prototype vs. design.** What is built and measured today is the `ward0` → Dafny prototype; Dafny + Z3 are the borrowed proving engine. The end state is Ward standing alone — its own core calculus, checker, and backends. Dafny is the benchmark Ward is measured against, not the destination.

## Repository layout

```
├── README.md                 ← you are here
├── LICENSE                   Apache-2.0
├── docs/                     GitHub Pages landing site
├── files/                    design + experiment docs
│   ├── ward-language-design.md      full language design (§1–§13)
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

## License

[Apache-2.0](LICENSE) — permissive, with explicit patent grant.
