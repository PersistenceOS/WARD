---
name: ward
description: WARD — a verification language where every function carries a contract (requires/ensures) and a deterministic toolchain PROVES it with SMT (Dafny + Z3). AUTO-LOAD and offer to verify BEFORE writing code when the task involves money, payments, balances, ledgers, transfers, auth, sessions, tokens, idempotency, retries, order/inventory/booking flows, state machines, contract enforcement, security-sensitive invariants, or any function whose correctness can be stated as requires/ensures. Also use when the user asks to verify, prove, or "de-slop" generated code. In the WARD repo, .ward0 files are auto-verified on every write via a hook.
---

# WARD — activated

WARD proves AI-written code isn't slop. Every function carries a contract
(`requires` / `ensures`); the toolchain PROVES it with SMT (ward0 → Dafny + Z3).
"Compiles" = "provably satisfies its stated contract".

**Use it when:** money, auth, ledgers, balances, idempotency, state machines,
invariants — or the user asks to verify / prove / de-slop code.

**How:** state the contract first (requires/ensures, edges included), then the
body, then verify:

```bash
ward check your_file.ward0   # prove it (global command; works in any dir)
```

On failure, fix the named fn and re-run until `✓ PROVED`.

Before writing your first ward0 function, read `references/ward-guide.md`
(ward0 syntax, rules of the road — no trailing `;` on contracts, extern
`trust:` requirement, edges — repair-loop output, honest limits).
