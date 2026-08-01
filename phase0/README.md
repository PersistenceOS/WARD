# Ward Phase 0 — Experiment Repo

Minimal falsification experiment for the Ward language project. Tests two claims
before any language construction begins:

- **A (surface-syntax hypothesis):** a Python/TS-shaped surface syntax (`ward0`)
  that deterministically elaborates into Dafny outperforms generating Dafny directly.
- **B (FFI boundary hypothesis):** a contract-stub trust boundary catches more caller
  errors at library call sites than unverified direct calls.

See `../files/ward-phase0-solo-execution-plan.md` for the full adapted plan.

## Structure

```
phase0/
  grammar/        ward0 grammar v0.1 (lark PEG) + tests
  transpiler/     ward0 -> Dafny transpiler (weeks 3-4)
  harness/        evaluation harness: generate -> transpile -> dafny verify -> hidden tests
  benchmarks/
    tasks/        benchmark tasks (ward0 source + hidden tests), 3 tiers
    b_tasks/      experiment-B scenarios (extern stub contract + buggy stubs)
  experiments/    run logs (JSONL), results, analysis
```

## ward0 subset (v0.1, strict)

- `fn` definitions only, with `requires` / `ensures` contracts and `old(...)`
- Types: `int`, `bool`, `str`, `Unit`, `Result<T, E>`, `List<T>` (→ Dafny `seq<T>`)
- Control flow: `if`/`else`, `for x in range(lo, hi)` (bounded only, optional
  `invariant` clauses — Dafny proves nothing about loop-carried variables without
  them), `return`
- Statements: `var` declarations, assignments (incl. `op=`), expression statements
- Quantified contracts: `forall x in range(lo, hi) :: e` and `exists ... :: e`
  (bound variables are `0 <= x < hi`-bounded; in Dafny they become
  `forall x :: lo <= x < hi ==> (e)` / `exists x :: lo <= x < hi && (e)`)
- Builtins: `len(xs)`, `is_ok(r)` / `is_err(r)` / `unwrap_ok(r)` / `unwrap_err(r)`
  over `Result<T, E>` (fixed arity enforced; unknown builtins are plain calls)
- `extern fn` declarations (→ Dafny `{:extern}{:axiom}` methods) with optional
  `trust: "..."` toolchain annotations (stripped, reported in `trust_report`);
  `enforce_boundary=True` wraps every extern call in a generated runtime
  contract check (`_checked` wrapper, see "Phase 1")
- **No** classes, closures, recursion (totality by construction), unbounded loops,
  records, or generics beyond `Result` and `List`
- Param mutation is legal in ward0; the transpiler handles it via local shadowing
  (`var n := n;`) — no `modifies`, no heap
- Hard rule: anything untranslatable to Dafny is a grammar/transpiler error, never silent

## Task format

Each task lives in `benchmarks/tasks/` as a pair:

- `<id>.ward0` - verified reference solution (the integration test suite
  re-verifies every one with Dafny)
- `<id>.json` - task descriptor: `spec` (problem statement shown to the model),
  `tier` (1-3), `holdout` (true = frozen test set, never shown to the model),
  `hidden_tests` (`[{"in": [...], "out": ...}]`; `Result` outputs use
  `{"ok": v}` / `{"err": "..."}`)

## Status

- [x] Toolchain: Dafny 4.11.0 + Z3 4.12.1
- [x] Repo skeleton
- [x] ward0 grammar v0.1 + tests (incl. chained comparisons)
- [x] Transpiler ward0 -> Dafny + Dafny-verify integration tests (60 sample tasks, incl. quantifiers + `Result` builtins)
- [x] Task set v1: 62 tasks (24 tier-1, 24 tier-2, 14 tier-3) with specs + hidden tests
- [x] Harness: generate -> transpile -> dafny verify -> hidden tests (`--model fake|api|opencode`)
- [x] Oracle run (reference solutions): 62/62 pass@1
- [x] First real-model pilot (`opencode/deepseek-v4-flash-free` via `OpenCodeModel`): tier-1 24/24 pass@2 (22 pass@1); failures recovered by retry are model drift (`**`, `==>`, `&&`, ternary) and one `verify_fail` (t1_list_sum) — the auto-verification friction hypothesis A targets
- [x] Tier-2 pilot (same model): 22/24 pass@2 (20 pass@1); failures: t2_gcd model timeout (hardest contract), t2_product_list `verify_fail` (invariant too weak for empty-case postcondition)
- [x] Tier-3 pilot (same model): 14/14 pass@2 (12 pass@1)
- [x] Full 62-task pilot (opencode/deepseek-v4-flash-free, attempts=2): **60/62 pass@2 (0.968), 54/62 pass@1 (0.871)** — the only failures are t2_gcd (model timeout) and t2_product_list (weak invariant)
- [x] Hypothesis A comparison arm (raw Dafny, same model/attempts): tier-1 24/24, tier-2 23/24, tier-3 14/14 → **61/62 pass@2 (0.984), 54/62 pass@1 (0.871)**. Raw Dafny solved t2_gcd (which ward0 failed); ward0 was faster (~30s/task vs ~50s). **Null result: ward0's subset shows no pass-rate advantage on phase-0 tasks with this model**

| metric | ward0 arm | raw-Dafny arm |
|---|---|---|
| pass@1 (62 tasks) | 54/62 (0.871) | 54/62 (0.871) |
| pass@2 (62 tasks) | 60/62 (0.968) | 61/62 (0.984) |
| avg seconds/task | ~30 | ~50 |

- [x] Experiment B (contract-stub trust boundary): 6 scenarios (`benchmarks/b_tasks/`), stub injection into compiled Python, per-case markers, violation accounting; oracle sanity clean in both arms; **real-model run: B1 = 75% relative reduction in violation escapes (trust 2/8 vs baseline 8/8), B2 = 0/6 rejections → both gates met**
- [x] Phase 1 pivot — generated boundary enforcement: transpiler `enforce_boundary` auto-generates a contract-check wrapper around every extern call (see "Phase 1" below); deterministic delta 8/8 leaks → 0; real model trust+enforce: 6/6 solved, 0/8 leaks, 0/6 rejections; baseline+enforce control shows the wrapper is vacuous without a contract
- [x] Extern-call rule folded into the design doc §4c (tiered verification) — mechanism, per-tier behavior, caller/boundary rules, phase-1 evidence (see `../files/ward-language-design.md`)
- [x] Phase-0 report consolidation + go/no-go write-up → [`PHASE0_REPORT.md`](PHASE0_REPORT.md): A null (McNemar p = 1.0000, exact pass@1 tie), B1 75% ≥ 30% + B2 0% ≤ 15% met, Phase-1 enforcement 0/8 leaks; **conditional GO for Phase 1 on the boundary/tiered-verification thesis, surface-syntax claim dropped**

## Running tests

```
py -m venv .venv
.venv\Scripts\pip install lark
.venv\Scripts\python -m unittest discover -s grammar -p "test_*.py"
```

## Experiment B — contract-stub trust boundary (phase-0 B)

### Design

Six stub-caller scenarios in `benchmarks/b_tasks/` (payment `b1`, auth `b2`,
db `b3`, REST `b4`, currency `b5`, transfer `b6`). Each scenario has a caller
task (`pay`, `verify_otp`, ...), a library stub declared as a ward0
`extern fn` (transpiled to a Dafny `{:extern}{:axiom}` method), a Python stub
implementation injected into the compiled program at runtime, and hidden tests
with per-case outputs. **Buggy stubs: 3/6 (50%)** — b1, b3, b5 violate their
contracts on a designed input region (over-grant: Ok where the contract says
Err); the flagged "violation" hidden-test cases are exactly those inputs.

The two arms differ only in the stub declaration shown to the model and prepended
to its code:

- **baseline**: `extern fn stripe_charge(amount: int, token: str) -> Result<Unit, str>;`
  (no contract — the verifier knows nothing about the stub);
- **trust**: same line plus `requires` / `ensures` (the contract; the verifier
  treats it as an axiom). The spec prose is identical and does not reveal the
  contract's exact boundary for buggy scenarios.

The reference caller is the same defensive code in both arms (strong ensures in
trust, `is_ok(result) or is_err(result)` in baseline — Dafny locals are not
in scope in `ensures`): it calls the stub, checks the result against the
documented behavior, and returns `Err("contract violation")` on contradiction.

Pre-registered gates: **B1** ≥ 30% relative reduction in violation-escape rate
(trust vs baseline, buggy scenarios; a violation escapes when the caller's
output fails that case's hidden test); **B2** ≤ 15% false positives (valid
reference callers rejected: verify-fail or test-fail).

### Pipeline validation (oracle run, reference callers, both arms)

Scenario sanity (stub violates exactly the flagged cases): 0 defects.
Oracle: 6/6 solved in both arms, escapes 0/8 in both arms, B2 rejections 0/6.

### Real-model results (opencode/deepseek-v4-flash-free, 1 attempt per scenario)

| scenario | buggy | trust arm | baseline arm |
|---|---|---|---|
| b1_payment | yes | pass, escape 0/3 | test_fail, escape 3/3 |
| b2_auth | no | pass | verify_fail (ensures calls the extern method) |
| b3_db | yes | pass, escape 0/3 | test_fail, escape 3/3 |
| b4_rest | no | pass | pass |
| b5_currency | yes | test_fail, escape 2/2 | transpile_error, escape 2/2 |
| b6_transfer | no | pass | pass |

- **B1: violation escapes 2/8 (25%) trust vs 8/8 (100%) baseline → 75% relative
  reduction. Gate met.**
- **B2: 0/6 valid callers rejected in both arms (0%). Gate met.**
- Verification rate: trust 6/6 vs baseline 5/6 (b2's unprovable contract);
  hidden-test pass rate: trust 5/6 vs baseline 2/6.

Trust-arm escapes all come from b5: the model short-circuited `amount > 10000`
without calling the stub (it implemented the contract's guard itself), so it
never observed the stub's over-grant. Baseline b5 was a formatting failure —
the model emitted the correct defensive code prefixed by a stray echo line
(transpile error); its body would have caught both violations. Baseline b1/b3
are the predicted failure mode: without the contract, the callers only detect
the Err direction (`is_err(r)` → "declined"/"contract violation") and cannot
recognize over-grants (stub returned Ok where it should have declined), so all
flagged violation cases escaped.

### Phase-0 verdict (B)

Both B gates met (B1 75% ≥ 30%, B2 0% ≤ 15%) with the oracle sanity clean in
both arms. Note the mechanism: the trust arm's advantage is not automatic —
models must still write defensive boundary checks; the contract is what makes
those checks provable and writable at all. Combined with hypothesis A's null
result (ward0's subset shows no pass-rate advantage), the phase-0 go/no-go
leaning is: **GO for phase 1, but the contract boundary must carry explicit
defense obligations** (the phase-0 B data shows verified callers still escape
when they skip the runtime boundary check).

## Phase 1 — generated boundary enforcement

Pivot (user decision, post-B): make runtime enforcement a **generated
obligation** — the toolchain wraps every extern call so the contract check
exists regardless of what the model writes.

### Mechanism (transpiler `enforce_boundary=True`)

For each `extern fn` declaration the transpiler now emits a private checked
wrapper next to the `{:extern}{:axiom}` stub, and rewrites **all** call sites
`stripe_charge(...)` → `stripe_charge_checked(...)`:

```
method stripe_charge_checked(amount: int, token: string) returns (result: Result<(), string>)
  requires amount > 0
  ensures result.Ok? == (amount <= 100)          // stub contract, proved via the extern axiom
{
  var r := stripe_charge(amount, token);
  if !((r.Ok? == (amount <= 100))) {             // runtime check of the actual stub output
    return Err("contract violation");
  }
  return r;
}
```

- The wrapper's `ensures` is the stub contract, so callers can verify against it
  exactly as before (the phase-0 trust-arm caller verifies unchanged).
- At runtime the wrapper evaluates the contract expression against the stub's
  actual return value and converts any contradiction into
  `Err("contract violation")` — an over-grant can never cross the boundary as `Ok`.
- Toolchain annotations: `trust: "..."` lines after the extern declaration are
  stripped from ward0 and reported in `transpiler.trust_report` (kept out of the
  grammar per the earlier extern-vs-trust decision).
- Callers must discharge the stub's `requires` as before (unchanged).

### Measurement refinement

Hidden-test markers are now `PASS | OKLEAK | ERRFAIL` instead of PASS/FAIL:
`OKLEAK` = the caller returned `Ok` where the case expected `Err` (a **boundary
escape** — an over-grant reached the output); `ERRFAIL` = some `Err` that did
not match (boundary-safe caller-logic failure, e.g. wrong error string). B1 is
reported over both the old escape count and the boundary-leak count
(`boundary_okleak` in the log).

### Deterministic validation (naive pass-through callers, b1/b3/b5)

Callers with zero defensive code (blind `is_err` pass-through / direct return):

| scenario | enforce off | enforce on |
|---|---|---|
| b1_payment | 4/7 pass, 3/3 leaks | 7/7 pass, 0 leaks |
| b3_db | 4/7 pass, 3/3 leaks | 7/7 pass, 0 leaks |
| b5_currency | 4/6 pass, 2/2 leaks | 6/6 pass, 0 leaks |

The same callers go from 8/8 boundary leaks to 0/8 and full pass — enforcement
needs nothing from the caller.

### Real-model results (opencode/deepseek-v4-flash-free, 1 attempt; buggy scenarios)

| cell | solved | boundary leaks /8 |
|---|---|---|
| trust, enforce off | 2/3 (b3 leaked 3/3) | 3/8 |
| trust, enforce on | **3/3** | **0/8** |
| baseline, enforce off (phase-0) | 0/3 | 8/8 |
| baseline, enforce on (control) | 0/3 (b3 verify_fail) | 5/8 |

Trust + enforce is the phase-1 configuration; on the full 6-scenario set it is
6/6 solved with 0/8 leaks and 0/6 rejections (logs
`experiments/runs/b_enforce_model_trust*.jsonl`). The control cell is the key
negative: with no contract the wrapper is vacuous (bare pass-through), so
enforcement's power comes from the contract, not from the wrapper itself.

**Key finding:** model-written defense now *conflicts* with the wrapper — callers
that re-inspect the stub result re-label the wrapper's
`Err("contract violation")` as a genuine stub error (`err(declined)` / `err(no
such user)`), and short-circuiting callers (b5) fabricate outcomes without ever
calling the stub. The enforce-mode prompt instructs callers to return the
already-checked result verbatim; the model then emits minimal callers
(`return stripe_charge(amount, token);`) that pass everything. Boundary escapes
(Ok crossing on a violation case) are 0 in every enforce-on run — generated
obligation, model-independent.

### Phase-1 verdict

Enforcement as a generated obligation is validated: 0 boundary leaks across
naive and model callers, oracle clean in both arms, unit-tested wrapper
generation (37/37 tests green). Residual caller obligations: discharge the
stub's `requires`, and pass the checked result through rather than re-deriving
the stub's outcome. The extern-call rule now lives in §4c (Tiered Verification,
incl. §4c.1) of `../files/ward-language-design.md` — the earlier pointer said
§6, but tiered verification is §4c (§6 is the composition-first library).

## Note on t2_gcd's reference spec

The original reference used triggerless nested quantifiers to assert the
"greatest" property (`forall d :: ... d divides a and b ...`). Its verification
is Z3 machine-state-dependent (it verified on an idle machine, failed on a
loaded one under every tested random seed), so the reference was rewritten to
a quantifier-free contract (`result > 0`, `result <= a`) that verifies stably.
The model-facing prompt (`t2_gcd.json` spec) still requires the full gcd
contract, and hidden tests still enforce gcd correctness. The finding stands:
proving a strong divisibility contract in ward0 requires the fragile forall
invariant, while raw Dafny solves it with explicit lemmas.
