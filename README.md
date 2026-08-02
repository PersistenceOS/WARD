<div align="center">

# 🛡️ WARD

**Ward AI slop.**

### A verification language for AI-written full-stack software

*WARD — **W**hy **A**nother **R**ust **D**erivative? It isn't. It's the language that wards off AI slop.*

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Dafny](https://img.shields.io/badge/Dafny-4.11.0-important.svg)](https://github.com/dafny-lang/dafny)
[![Z3](https://img.shields.io/badge/Z3-4.12.1-orange.svg)](https://github.com/Z3Prover/z3)
[![Phase 1](https://img.shields.io/badge/phase-1%20conditional%20GO-ff69b4.svg)](files/PHASE1_REPORT.md)

</div>

---

## The 60-second version (no jargon)

AI writes a lot of code now. Most of it is checked the lazy way — "does it compile?" and "do the tests pass?" **Neither of those proves the code is right.**

WARD is a language where **every function carries a promise about what it must do**, and a computer **proves the promise mathematically** before the code is allowed to run. Not "looks right." *Proven right.*

```ward0
fn withdraw(balance: int, amount: int) -> int
  requires amount > 0                // promise BEFORE: amount is positive
  requires amount <= balance         // promise BEFORE: no overdraft
  ensures result == balance - amount // promise AFTER: result is exactly right
{
    return balance - amount;
}
```

If the code breaks its promise, WARD says so — naming the exact line, the exact promise, and a concrete counterexample — and the AI fixes it and retries. **AI writes the code. WARD proves it isn't slop.**

---

## Why this exists: the worst kind of AI slop

"AI slop" isn't one thing. The dangerous kind is **silent correctness slop** — code that *looks* right, *passes the tests*, and is **subtly wrong**: an overdraft slips through, an over-grant returns `Ok`, an auth check is always true. Tests written by the same model tend to agree with the same bug.

| Slop type | What it looks like | WARD's answer | Status |
|---|---|---|---|
| **Silent correctness slop** | compiles, passes tests, subtly wrong | contracts + verifier + generated boundary enforcement | ✅ **Measured** |
| Compile-failure slop | output that doesn't parse | grammar-constrained decoding | 🔜 design |
| Edge-case slop | missing boundaries, empty inputs | contracts force the model to state edges | 🟡 partial |
| Cheating slop | vacuous promises that satisfy the checker | **Specification Tightness τ** — see below | ✅ **Built** |
| Hallucinated-dependency slop | wrong versions, imaginary APIs | effect tracking + dependency pinning | ✅ **Built** |
| Aesthetic slop | bloat, duplication, over-engineering | — a verifier proves *correctness*, not elegance | ❌ not a goal |

**WARD doesn't make the model write better — it makes bad output *fail*.** On the failure mode that matters, the measured numbers are below.

---

## How it works

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
2. **WARD proves** the promises; structured errors name the failing line, the obligation, and a counterexample.
3. **Model repairs** against the error and retries — checker-guided convergence.
4. **Anti-slop layer:** an instrument measures how *specific* each promise is, so the model can't satisfy the checker with `ensures true` (the scientific core — next section).

### What this changes for you — the human in the loop

Without a checker, AI code is judged by eye: you read the diff, hope the tests the *same model* wrote are right, and ship. WARD moves the acceptance test from your eyeballs to a machine:

| Without WARD | With WARD |
|---|---|
| You prompt in prose: *"handle insufficient funds and return the new balance"* | You write the contract once: `ensures is_ok(result) == (amount <= balance)` — the model implements against a checkable spec |
| The model's tests were written by the same model — they agree with the same bug | The verifier is independent; a wrong `Ok` fails with a named counterexample (`amount=110, balance=100`) |
| Edge cases live in your head — or get cut when the model gets lazy | Edges are *stated* in `requires` / `ensures` and *proven* — the model can't skip them |
| You review every repaired attempt to tell good from bad | The repair loop is checker-guided: `(location, obligation, counterexample)` → model fixes → re-verify until ✓ |
| A subtle bug costs an incident, later | A subtle bug costs one failed verification, now |

**Prompt-wise, your prompt gets shorter and sharper.** You don't have to enumerate every edge case in the prompt or argue the model into handling them — you declare the contract, and the checker enforces it on every attempt. The model can still write sloppy code; WARD just makes sure sloppiness *fails* instead of *shipping*.

### Where it bites: example use cases

Three scenarios from the repo's own benchmark corpus (all measured, Phase 1):

**1. A two-account ledger — a bank that approves overdrafts.** `ledger_debit(balance, amount)` is documented to return `Err` when `amount > balance`. The buggy ledger under test approves up to `balance + 20`. Naive AI code checks `is_ok` and moves the money — the overdraft ships. WARD's caller *must* verify the service's actual behavior against its contract on every call:

```ward0
extern fn ledger_debit(balance: int, amount: int) -> Result<int, str>
  requires balance >= 0
  requires amount >= 0
  ensures is_ok(result) == (amount <= balance);  // the promise; extern defs
  trust: "oracle reference stub"                // end with ';', trust follows
```

When the ledger lies (`amount=110, balance=100`), the generated boundary wrapper converts the violation into `Err("contract violation")`. Measured: **0/20 boundary leaks** vs. raw Dafny's **4/20**.

**2. Idempotent charging — you can't double-charge a customer.** The dedup service promises `Ok(key+1)` for keys below 500 and `Ok(0)` for fresh keys; the gateway promises to decline anything above its limit. AI code that skips the "does the service match its contract?" check double-charges on retry or misses a decline. The checker forces the caller to compare every service's actual return against its documented contract — retries can't double-charge, over-limit charges can't silently succeed.

**3. A currency round-trip — an FX exploit at the boundary.** `fx_convert(amount, 2)` is documented to accept only `amount <= 1000`. A service that honors `1100` lets a `501 USD → EUR → USD` round-trip succeed when it must fail (501 doubles to 1002). WARD catches the boundary violation before it ships.

**The pattern:** in all three, the code *looks right* and the happy-path tests *pass* — the dangerous kind of slop. What fails it is a machine checking the contract, not a human rereading the code.

### The language the model writes

The model never writes dependent types, proof scripts, or ownership annotations. It writes Python/TypeScript-shaped code with contracts:

```ward0
fn sum_between(xs: List<int>, lo: int, hi: int) -> int
  requires 0 <= lo and lo <= hi and hi <= len(xs)
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

External libraries (`extern fn`) carry a mandatory contract + `trust:` line, and the toolchain **generates a runtime contract-check wrapper** around every unverified call — a verified boundary, not a convention.

---

## The science: Hoare triples, plus something new

WARD is not a new proof theory — it implements a 50-year-old one, and that's the point. Every ward0 function is literally a **Hoare triple** (Hoare, 1969):

$$\{P\}\; c\; \{Q\}$$

`requires` is the precondition $P$, the body is the program $c$, `ensures` is the postcondition $Q$.

**Total correctness.** "Proved" means the triple holds *and* the program terminates — free by construction, since ward0 has bounded loops only and no recursion:

$$\vdash \{P\}\; c\; \{Q\} \;\Longleftrightarrow\; \forall \sigma.\; \sigma \models P \;\Rightarrow\; [\![c]\!](\sigma)\!\downarrow \wedge [\![c]\!](\sigma) \models Q$$

**What the solver checks.** Dafny emits a verification condition and Z3 proves validity by showing the negation is unsatisfiable:

$$\text{unsat}\Big(\neg\big(P(\vec{x}) \wedge [\![c]\!](\vec{x}) = \vec{x}' \Rightarrow Q(\vec{x}')\big)\Big)$$

**The repair loop.** The model emits candidates $c_1, c_2, \ldots$ until one satisfies the contract — the verifier, not the human, is the acceptance predicate:

$$c^{*} = \min_{n \geq 1}\{\, n : \vdash \{P\}\; c_n\; \{Q\} \,\}$$

### New: Specification Tightness τ — making Hoare honest

A Hoare triple is only as meaningful as its $Q$. `ensures true` is a perfectly valid triple — and **completely useless**. An AI model can game the checker by writing promises so weak they're trivially true: the proof passes, the slop ships. That's the hole in classic Hoare logic for the AI-generation setting, and it's what WARD's new instrument closes.

**Specification Tightness τ** measures how much of the output space a contract actually pins down:

$$\tau(x) = 1 - \frac{\log_2 |Y_{perm}(x)|}{\log_2 |Y|}, \qquad \tau = \mathbb{E}_{x \sim P}[\tau(x)]$$

- $Y$ = the bounded output space (derived from the contract's own literals + fixed anchors)
- $Y_{perm}(x)$ = the outputs the contract permits at input $x$
- **τ = 1**: the contract pins the output completely (maximally anti-slop)
- **τ = 0**: `ensures true` — zero output entropy constrained (vacuous)

τ is measured over a bounded input grid and **calibrated** so the instrument itself is gated, not asserted: the vacuous control scores **0.0**, the reference corpus's honest Proven specs floor at **0.234** — so the calibrated threshold **τ₀ = 0.2** separates "real promise" from "checker gaming" with a visible margin. A Proven tier on a τ < τ₀ spec is **flagged** (advisory today, demotion in strict mode) — and the specific weak `ensures` clause is named, giving the AI a concrete spec-fixing target. This is what makes the anti-slop claim *measured* instead of marketed.

**Boundary enforcement.** Every `extern fn` gets a generated runtime wrapper that converts a contract violation into `Err("contract violation")`:

$$W(x) = \begin{cases} s(x) & \text{if } s(x) \models \text{contract}_s \\[2pt] \text{Err}(\text{"contract violation"}) & \text{otherwise} \end{cases}$$

---

## Measured results (honest, small sample)

WARD is a research project: pre-registered hypotheses, pre-registered gates, and negative results published alongside positive ones. One model (`opencode/deepseek-v4-flash-free`). **Small sample — directionally strong, not statistically conclusive.**

### The headline numbers

| Metric | WARD (ward0) | Raw Dafny |
|---|---|---|
| Module scenarios solved | **8/8** | 6/8 |
| Boundary leaks (violations escaping the checked boundary) | **0/20** | 4/20 |
| Tokens per solved task | **850** | 916 |

### Token economics: a small premium, bought back with fewer attempts

**Verification itself costs 0 model tokens** — Dafny + Z3 are deterministic local tools, not an LLM. The only token spend is what the model writes and how many times it retries:

| | W (ward0) | D (raw dafny) |
|---|---|---|
| Attempts (8 tasks) | 9 (1 extra) | 13 (5 extra) |
| Solved | **8/8** | 6/8 |
| Guide tokens | 5,661 (9×629) | 2,808 (13×216) |
| Output tokens | 1,143 | 2,691 |
| **Total** | 6,804 | 5,499 |
| **Tokens per solved task** | **850** | 916 |

Read honestly: WARD pays an up-front premium — a 629-token guide (vs. Dafny's 216) is re-sent on every attempt — and buys it back with **fewer attempts**: per correct result, WARD is ~7% cheaper (850 vs. 916), and it delivered 8/8 where raw Dafny delivered 6/8. The guide is a fixed constant: it dominates tiny tasks (83% of WARD's spend here) and shrinks to noise on real programs, where the attempt savings dominate. To get 8 correct results at D's rate would take ~10.7 tasks ≈ 7,330 tokens — the same outcome, WARD cheaper. **The honest asterisk:** if verification doesn't converge, attempts — and re-sent guide tokens — compound. That ceiling is exactly what the tiered design bounds.

### The negative result, published

WARD's surface syntax does **not** make the model write better code — pass rates tied (p = 1.0000). That claim is dropped and on the record. What WARD wins on is the *boundary, the tiers, and checker-guided convergence* — not syntax magic.

### Phase 1 verdict: Conditional GO

C1 tiers ✅ · C2 boundary at scale (0/20) ✅ · C3a accuracy ✅ (8/8 vs 6/8) · C3b effort ratio 0.77 vs 0.7× ❌ — the effort advantage didn't reach the pre-registered bar. The boundary + tiered-verification thesis carries; the effort claim is scoped down. Full detail in [`files/PHASE1_REPORT.md`](files/PHASE1_REPORT.md).

---

## Experimental features (built, tested, available)

| Feature | What it does | Where |
|---|---|---|
| **Specification Tightness τ** | anti-slop index: flags Proven fns whose contract is too weak (τ < 0.2); names the weak clause | `wardcore/tightness_gate.py`, wired into `ward.py check` |
| **Boundary Immunity theorem** | a *proved* metatheorem (Dafny 5/5): no extern implementation — even adversarial — can corrupt the core; worst case is a caught `Err` | `experiments/forcecompile/boundary_immunity_metatheorem.dfy` |
| **Certificates (`.proof`)** | every verified module ships a machine-checkable certificate, validated by a dependency-free checker — no Dafny/Z3 needed to verify it | `harness/certificate.py` + `cert_check.py`, `ward.py proof` |
| **Tiered verification** | `Tested` → `Contracted` → `Proven`; proof cost scales with consequence | core pass (T6) |
| **Effects tracking** | a fn calling a `net` extern without declaring `net` fails elaboration | core pass (T5) |
| **Dependency pinning** | version drift / unresolved / ambiguous deps are hard errors | core pass (E4b) |
| **Linearity** | money/tokens consumed exactly once on every path — copy/drop/double-use fail | core pass (T7) |
| **Structured repair errors** | verifier output → `(location, obligation, counterexample)` triples in ward0 terms, with the τ advisory attached | `wardcore/error_translation.py` |
| **Z3-direct backend (E10)** | verifies ward-core IR directly with Z3 — no Dafny in the loop; externs as axiom methods, path-walk VC construction, per-fn effort records | `wardcore/z3_backend.py` (14 unit tests green; E10 gate PASS — dafny/z3 agree) |
| **Python backend (E11)** | the first multi-target backend — compiles ward-core IR to Python (Result/Unit/List mapping, extern stubs with runtime `_checked` contract wrappers, tier-aware requires asserts); functional parity with the Dafny path on w1-w8 + 22 t2/t3 tasks | `wardcore/py_backend.py` (14 unit tests green; E11 gate PASS, legs A-D) |
| **Certificate re-derivation (Phase 3)** | the standalone checker re-transpiles the bound source itself and compares hashes — the Level-1 "declared, not re-derived" gap is closed | `harness/cert_rederive.py` (stdlib-only, probed 5/5) |

---

## Roadmap

| Phase | Status | Scope |
|---|---|---|
| 0–1 | ✅ done | `ward0` grammar, transpiler, harness; thesis validated (boundary + tiers) |
| 2 | 🔬 scoped | core calculus + elaborator ([scoping doc](files/ward-phase2-scoping.md)); ward-core IR v0.1 complete; τ + Boundary Immunity instruments built |
| 3 | ✅ first slice built | **standalone SMT-backed checker — Ward standing alone, no Dafny**: `z3_backend` verifies ward-core IR directly with Z3 (14 unit tests green; E10 gate PASS); `cert_rederive` closes the certificate's Level-1 gap (stdlib-only re-transpiler, probed 5/5) |
| 4 | ✅ first slice built | **multi-target backends**: `py_backend` compiles ward-core IR to Python with runtime contract-checked externs — functional parity with the Dafny path on w1-w8 + 22 t2/t3 (E11 gate PASS, legs A-D) |
| 5 | 🧭 next | composition-first verified library ([design §6](files/ward-language-design.md)) — the content-addressed, proof-cached function registry |

**Prototype vs. design.** The measured pipeline is still `ward0` → Dafny; Dafny + Z3 are the borrowed proving engine. But Ward's own checker is no longer hypothetical: `z3_backend` verifies the core IR directly with Z3 — no Dafny in the loop — certificates can now be independently re-derived with stdlib only, and Ward now *compiles* to a second target: `py_backend` emits Python from the same IR with the same functional behavior (E11 parity gate PASS). The composition-first verified library remains the open end of the roadmap.

---

## Repository layout

```
├── README.md                 ← you are here
├── ward.py                   ← the CLI (check / emit / proof / setup)
├── install.sh / install.ps1  ← one-line installers (Wire up section)
├── AGENTS.md                 ← agent guide (Claude Code, Cursor, OpenCode, Cline…)
├── .claude/skills/ward/      ← Claude Code skill
├── .cursor/rules/ward.mdc    ← Cursor rule
├── LICENSE                   Apache-2.0
├── docs/                     GitHub Pages landing site
├── files/                    design + experiment docs
│   ├── ward-language-design.md      full language design (§1–§13)
│   ├── PHASE1_REPORT.md             Phase 1 four-arm results
│   └── ward-phase2-scoping.md       core calculus + elaborator scoping
└── phase0/                   Phase 0/1/2 implementation + experiments
    ├── grammar/              ward0 grammar v0.1 (lark PEG)
    ├── transpiler/           ward0 → Dafny transpiler
    ├── harness/              generate → transpile → dafny verify → hidden tests
    │   ├── certificate.py    .proof emission (ward-cert v0.1)
    │   └── cert_check.py     standalone .proof validator (no Dafny/Z3)
    ├── wardcore/             typed core IR + passes (tiers/effects/deps/linearity/tightness)
    └── benchmarks/           62 tasks (3 tiers) + 8 w-tasks + 6 B-scenarios
```

## Getting started (from source)

```bash
git clone https://github.com/PersistenceOS/WARD.git
cd WARD/phase0

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install lark

# try the CLI on a real example
cd ..
phase0/.venv/Scripts/python ward.py check phase0/benchmarks/w_tasks/w5_currency_roundtrip.ward0
```

Requires [Dafny 4.11.0](https://github.com/dafny-lang/dafny) and [Z3 4.12.1](https://github.com/Z3Prover/z3) on your PATH for live verification.

---

## Wire up WARD in your AI tools

WARD is a CLI first — every agent tool can drive it. Two ways to get set up: the **one-line installer** (fresh machine, nothing pre-cloned) or the **one-command setup** (from an existing checkout). Both end with the same result: the Claude Code skill and Cursor rule installed globally, so the tools know about WARD in *any* project.

### One-line install (any OS)

```bash
# macOS / Linux / Git Bash on Windows:
curl -fsSL https://raw.githubusercontent.com/PersistenceOS/WARD/main/install.sh | bash
```

```powershell
# native Windows PowerShell:
iex (irm https://raw.githubusercontent.com/PersistenceOS/WARD/main/install.ps1)
```

This clones the repo to `~/WARD` (override with `WARD_DIR=...`), creates the `phase0/.venv`, installs `lark`, runs `ward.py setup`, and prints a ready-check. Dafny + Z3 are *checked*, not installed — see the note at the end.

### One-command setup (from the repo)

```bash
python ward.py setup                # install skill + rule globally, check venv + toolchain
python ward.py setup --create-venv  # also create phase0/.venv + install lark if missing
python ward.py setup --dry-run      # show what it would do, write nothing
```

### Quick check (any tool)

```bash
# Windows venv:
phase0/.venv/Scripts/python ward.py check your_file.ward0
# POSIX:
phase0/.venv/bin/python ward.py check your_file.ward0
# machine-readable (agents):
phase0/.venv/Scripts/python ward.py check your_file.ward0 --json
```

### Claude Code

`ward.py setup` installs the skill to `~/.claude/skills/ward/` — Claude Code auto-loads it whenever you ask to verify or de-slop code, in any project. (`AGENTS.md` in the repo root also teaches any agent the workflow.)

### Cursor

`ward.py setup` installs the rule to `~/.cursor/rules/ward.mdc` — Cursor auto-applies it to `.ward0` files project-wide. (The repo also ships a project-scoped copy at `.cursor/rules/ward.mdc`.)

### VS Code

Run the CLI from the integrated terminal, or add it as a task:

```jsonc
// .vscode/tasks.json
{
  "version": "2.0.0",
  "tasks": [{
    "label": "ward check",
    "type": "shell",
    "command": "python ward.py check ${file}",
    "problemMatcher": []
  }]
}
```

### OpenCode / Cline / any agent

The repo root `AGENTS.md` is read automatically by these tools. Tell the agent *"verify this with ward"* and it will run `python ward.py check <file>` and repair against the structured errors.

---

## The ward0 subset (v0.1, strict)

- `fn` definitions with `requires` / `ensures` contracts and `old(...)`
- Types: `int`, `bool`, `str`, `Unit`, `Result<T, E>`, `List<T>` (→ Dafny `seq<T>`)
- Bounded `for x in range(lo, hi)` with `invariant` clauses; `if`/`else`; `return`
- Quantified contracts: `forall x in range(lo, hi) :: e`, `exists ... :: e`
- Builtins: `len(xs)`, `is_ok` / `is_err` / `unwrap_ok` / `unwrap_err`
- `extern fn` declarations with mandatory `trust: "..."` annotations; `enforce_boundary` auto-generates a runtime contract-check wrapper
- **No** classes, closures, recursion (totality by construction), unbounded loops, or generics beyond `Result` and `List`

## License

[Apache-2.0](LICENSE) — permissive, with explicit patent grant.
