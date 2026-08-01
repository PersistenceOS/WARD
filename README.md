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

## Roadmap

- **Phase 2** — core calculus + elaborator scoping ([design doc](files/ward-phase2-scoping.md)), ward-core IR v0.1 done
- **Phase 3+** — surface/core elaboration, multi-target backends, composition-first verified library (design §6)

## License

[Apache-2.0](LICENSE) — permissive, with explicit patent grant. See the license for full terms.
