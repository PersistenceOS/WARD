# WARD — agent guide

WARD is a **verification language for AI-written code**. Every function carries
a contract (`requires` / `ensures`), and a deterministic toolchain **proves** it
with an SMT-backed verifier (today: `ward0` → Dafny + Z3) before anything runs.
*"Compiles" means "provably satisfies its stated contract"* — not "looks
plausible" and not "my tests passed."

This file is read by Claude Code, Cursor, OpenCode, Cline, and other agent
tools. It tells you how to use WARD to catch the dangerous kind of AI slop:
code that looks right, passes tests, and is subtly wrong.

## Quick start

```bash
# verify a ward0 file (elaborate -> prove -> diagnose)
python ward.py check path/to/file.ward0

# machine-readable output (for agents)
python ward.py check path/to/file.ward0 --json

# add extern contract-check wrappers (boundary enforcement)
python ward.py check path/to/file.ward0 --enforce

# emit a .proof certificate (checkable standalone, no Dafny/Z3)
python ward.py proof path/to/file.ward0
```

Use the repo venv's python so `lark` is available:
- Windows: `phase0/.venv/Scripts/python`
- POSIX: `phase0/.venv/bin/python`

Requires `dafny` 4.11 + `z3` 4.12 on PATH for live verification.

## The language in 60 seconds

```ward0
fn withdraw(balance: int, amount: int) -> int
  requires amount > 0                // promise BEFORE: amount is positive
  requires amount <= balance         // promise BEFORE: no overdraft
  ensures result == balance - amount // promise AFTER: result is exactly right
{
    return balance - amount;
}
```

- Contracts are **annotations, not statements** — no trailing `;` after
  `requires`/`ensures`.
- No method calls inside contracts — pure builtins (`len`, `is_ok`, `is_err`,
  `unwrap_ok`, `unwrap_err`) only.
- `extern fn` needs a contract + `trust: "..."`; the toolchain generates a
  runtime contract-check wrapper around it.
- Totality by construction: bounded `for` loops only, no recursion.

## When WARD fires automatically

- **Auto-verify hook (Claude Code):** after `ward.py setup`, every `.ward0`
  file you write or edit is checked automatically (a PostToolUse hook runs
  `ward.py check --json`) and the result is injected back into the
  conversation — `✓ PROVED`, or the failing obligations + counterexamples to
  fix. Treat it as a live linter: fix failures before moving on. Before a
  write, a PreToolUse nudge reminds you to state the contract first. Disable
  by removing the WARD hook entries from `~/.claude/settings.json`.
- **Proactive suggestion:** for contract-shaped work — money, payments,
  balances, ledgers, auth, sessions, tokens, idempotency, retries, state
  machines, order/inventory/booking flows, any invariant that can be stated
  as `requires`/`ensures` — propose verification EARLY: state the contract
  before writing the body, not after the code exists.
- **Always available:** `python ward.py check file.ward0` on demand.

## How to use WARD as an agent

1. **Write ward0** (or ask the model to) with real contracts — state the edges.
2. **Run `python ward.py check file.ward0`** — WARD proves it or fails it.
3. **Read the structured triples** on failure — `(location, obligation,
   counterexample)` in ward0 surface terms, e.g.
   `postcondition of transfer — ensures is_ok(result) == (amount <= 1000000) —
   could not be proved on this return path; counterexample: amount=1000001`.
4. **Fix the named fn and retry** — iterate until `✓ PROVED`.
5. **Heed the tightness advisory**: if output shows a Proven fn with
   `tau < TAU0` (a vacuous spec like `ensures true`), the tier is a lie —
   strengthen the `ensures` clause to pin the output value.

## Honest limits (do not overclaim)

- WARD makes bad output **fail**; it does not make the model write better.
- It proves correctness against the stated contract — not elegance, style,
  or performance.
- Surface-syntax superiority over raw Dafny was **falsified** (Phase 0,
  p = 1.0000). WARD's measured wins: boundary enforcement (0/20 leaks vs
  4/20), tiered proof cost, and checker-guided convergence.
- Research prototype: `ward0` → Dafny + Z3 is the measured pipeline; the
  standalone SMT checker's first slice (`z3_backend`, Z3-direct, no Dafny)
  and the multi-target first slice (`py_backend` → Python, E11 gate PASS)
  are built; the composition-first verified library remains phases 3+.

## Where everything is

- `ward.py` — the CLI (this repo root)
- `phase0/` — implementation (grammar, transpiler, harness, wardcore)
- `files/ward-language-design.md` — full design
- `files/PHASE1_REPORT.md` — measured results
- `.claude/skills/ward/SKILL.md` — Claude Code skill (same content, richer)
