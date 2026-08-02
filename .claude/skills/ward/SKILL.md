---
name: ward
description: WARD — a verification language where every function carries a contract (requires/ensures) and a deterministic toolchain PROVES it with SMT (Dafny + Z3). AUTO-LOAD and offer to verify BEFORE writing code when the task involves money, payments, balances, ledgers, transfers, auth, sessions, tokens, idempotency, retries, order/inventory/booking flows, state machines, contract enforcement, security-sensitive invariants, or any function whose correctness can be stated as requires/ensures. Also use when the user asks to verify, prove, or "de-slop" generated code. In the WARD repo, .ward0 files are auto-verified on every write via a hook.
---

# WARD — prove AI-written code isn't slop

WARD is a Python/TypeScript-shaped language in which **every function carries a
contract** (`requires` / `ensures`). A deterministic toolchain proves the
contract with an SMT-backed verifier (today: `ward0` → Dafny + Z3).
**"Compiles" means "provably satisfies its stated contract"** — not
"type-checks and looks plausible."

The pitch: AI code that *looks* right, passes tests, and is subtly wrong is the
dangerous kind of slop. WARD makes it **fail** at the checker, before it ships.

## When to use this skill

- The user wants a function **proven correct** (not just unit-tested).
- Code touches money, auth, ledgers, balances, invariants — where a subtle bug is catastrophic.
- The user says "verify this", "prove this", "de-slop this", "make this not-slop".
- You are about to write consequential logic and want the checker to catch your mistakes instead of tests agreeing with them.

## Proactive use — verify as you write, not after

- **Offer WARD early, not after the code exists.** When a task matches the
  triggers above (money, auth, idempotency, state, any `requires`/`ensures`
  invariant), propose it in chat BEFORE writing: *state the contract first,
  then the body, then prove it* — not "the code is done, let's check it."
- **In the WARD repo, verification is automatic.** A Claude Code hook
  (`ward_hook.py`, installed by `ward.py setup`) runs `ward.py check` on every
  `.ward0` file you write or edit, and injects the result back into the
  conversation — `✓ PROVED`, or the failing obligations + counterexamples to
  fix. Treat it as a live linter: fix failures before moving on.
- **When writing ward0 yourself:** write the contract lines first (edges
  included: bounds, empty inputs, negative amounts), then the body. The
  checker will confirm or fail the contract on the next write.

## How to invoke WARD

The CLI lives at the repo root of the WARD checkout:

```bash
# from the WARD repo
python ward.py check path/to/file.ward0            # elaborate + prove + diagnose
python ward.py check path/to/file.ward0 --json     # machine-readable (agents)
python ward.py check path/to/file.ward0 --enforce  # add extern contract-check wrappers
python ward.py emit  path/to/file.ward0            # just transpile to Dafny
python ward.py proof path/to/file.ward0            # emit a .proof certificate
```

Use the repo venv's python so `lark` is available (Windows:
`phase0/.venv/Scripts/python`; POSIX: `phase0/.venv/bin/python`). Requires
`dafny` 4.11 + `z3` 4.12 on PATH for live verification.

## The ward0 subset (v0.1, strict)

```ward0
fn withdraw(balance: int, amount: int) -> int
  requires amount > 0                // promise BEFORE: amount is positive
  requires amount <= balance         // promise BEFORE: no overdraft
  ensures result == balance - amount // promise AFTER: result is exactly right
{
    return balance - amount;
}
```

- `fn` definitions with `requires` / `ensures` contracts.
- Types: `int`, `bool`, `str`, `Unit`, `Result<T, E>`, `List<T>`.
- Bounded `for x in range(lo, hi)` with `invariant`; `if`/`else`; `return`.
- Quantified contracts: `forall x in range(lo, hi) :: e`.
- Builtins: `len(xs)`, `is_ok` / `is_err` / `unwrap_ok` / `unwrap_err`.
- `extern fn` + `trust: "..."` for unverified library calls; the toolchain
  *generates* a runtime contract-check wrapper (boundary enforcement).
- **No** classes, closures, recursion (totality by construction), unbounded loops.

### Rules of the road for writing ward0

1. **Contracts are annotations, not statements** — no trailing `;` after
   `requires`/`ensures` (the `{` follows the last contract line directly).
2. **No method calls inside contracts** — only pure builtins
   (`len`/`is_ok`/`is_err`/`unwrap_ok`/`unwrap_err`) and constructors.
3. **Every `extern fn` needs a contract and a `trust:` line.**
4. **State the edges** — bounds, empty inputs, negative amounts. The checker
   makes you prove them.
5. **Tighten, don't vacuously satisfy.** `ensures true` verifies trivially and
   WARD flags it: a Proven tier on a vacuous spec is a demotion risk (see the
   anti-slop advisory in `check` output).

## The repair loop (how you should use the output)

When `ward check` fails, it prints **structured error triples** in ward0
surface terms — `(location, violated_obligation, counterexample)`:

```
postcondition  transfer:ensures
    postcondition of transfer — ensures is_ok(result) == (amount <= 1000000) — could not be proved on this return path
    counterexample: {'amount': '1000001', 'result': 'Result.Ok(1000001)'}
```

Use these, not guesses: fix the named function's contract or body so the
counterexample can't occur, re-run `ward check`, iterate until `✓ PROVED`.
If the triple carries an `I1 tightness` advisory (`tau < TAU0`), the *spec* is
too weak — strengthen the `ensures` clause to pin the output value before the
tier means anything.

## What WARD does NOT do (be honest)

- It does **not** fix slop for you — it makes bad output **fail**.
- It does **not** prove elegance, style, or performance — only correctness
  against the stated contract.
- The surface-syntax-superiority claim was **falsified** (Phase 0, p = 1.0000).
  WARD wins on boundary enforcement, tiered proof cost, and checker-guided
  convergence — never on "syntax makes the model write better."
- Research prototype: `ward0` → Dafny + Z3 is the measured pipeline; the
  standalone SMT checker's first slice (`z3_backend`, Z3-direct, no Dafny)
  and the multi-target first slice (`py_backend` → Python, E11 gate PASS)
  are built; the composition-first verified library remains phases 3+.

## Experimental features you can use today

- **Specification Tightness τ (I1)** — the anti-slop index: measures how much
  output entropy a contract pins down (`tau = 1 − log2|Y_perm| / log2|Y|`).
  Calibrated `TAU0 = 0.2`: vacuous specs score ~0, honest Proven specs ≥ 0.234.
  Wired into `check` output as the advisory.
- **Boundary Immunity (I2)** — a proved metatheorem: no extern implementation,
  even adversarial, can corrupt core invariants; worst case is a caught
  `Err("contract violation")`.
- **Certificates (E9)** — `ward.py proof` emits a machine-checkable `.proof`;
  verify it anywhere with `phase0/harness/cert_check.py` (no Dafny/Z3 needed).
- **Tiered verification** — `Tested` (no proof) → `Contracted` (bounded proof
  + test fallback) → `Proven` (full SMT). Tier routing is a core pass.
- **Effects, dependency pinning, linearity** — core passes (T5/E4b/T7) that
  catch hallucinated dependencies, undeclared effect escapes, and
  copy/drop/double-use of linear-typed money/tokens.

## Reference

- `files/ward-language-design.md` — full design doc
- `files/PHASE1_REPORT.md` — Phase 1 four-arm results
- `phase0/` — implementation + experiments
