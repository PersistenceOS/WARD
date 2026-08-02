# WARD ships proofs, not promises — implementation plan

**Status:** MOSTLY BUILT (Phase A emitter + Phase C checker complete, gate E9 G1–G4 green;
Phase-3 standalone re-deriver scoped and **probed feasible** — 5/5 committed artifacts
re-derive their `emitted_dafny_sha256` in stdlib-only Python; see §10). The vision and the
hooks below are unchanged; the sections marked *built* are now measured, not design.

**Scoping home:** pre-registered into Phase 2 as **R11 / scope item 3 / gate E9** in
`../files/ward-phase2-scoping.md` (Phase-2.5 preview, additive to — never load-bearing for —
E1–E8).

**One-line thesis:** today Ward *verifies* (a process, on a machine with Dafny+Z3); the
revolutionary step is to make the proof a *shippable artifact* — every AI-generated program
ships with a machine-checkable correctness certificate that anyone can validate in seconds
with a tiny checker, no model, no Dafny, no Z3, and no trust in the Ward lab.

---

## 1. Why this is genuinely new (and honest about what isn't)

**Attribution:** proof-carrying code is a settled idea (Necula, 1997; evidence-carrying code
after it). The reason it never went industrial: **proofs were too expensive to generate for
human-written code.** The generator cost more than the correctness was worth.

**The economics flip (the part that is ours to claim):** AI generation inverts that equation.
Compute is cheap; the expensive verification loop already runs as part of generation. A model
that costs a dollar to produce code can produce the proof artifact for pennies more — and the
*checker* costs nothing and runs anywhere. The unclaimed combination: *LLM synthesis + tiered
proof production + self-contained certificates as the shipping format.*

**What is NOT claimed:** no new proof theory (we implement Hoare logic, per the README's
Formal foundations); no new mathematics. The novelty is the *productization* of verification
for the AI-synthesis setting.

---

## 2. The gap: verification-as-process vs certificate-as-artifact

| | Today (built) | Certificate (this plan) |
|---|---|---|
| When | at generation time, in the loop | ships with the code, checkable forever |
| Where | on the machine with Dafny + Z3 + transpiler | anywhere — CI, auditor, regulator |
| Who | the developer running the harness | any third party, without trusting Ward the lab |
| Artifact | `verified: true/false` in a run log | `pay.ward0` + `pay.proof` (portable, checkable) |
| Trust | the Ward toolchain is the only judge | a tiny standalone checker is the judge |

The engine is identical; the deliverable differs. That is the whole argument.

---

## 3. The certificate format (.proof) — design sketch

A `pay.proof` is a JSON artifact binding code, contracts, proof outcomes, and the trust
boundary into one tamper-evident structure:

```jsonc
{
  "format": "ward-cert/v0.1",
  "module": "pay",
  "source_sha256": "…",                    // hash of pay.ward0
  "toolchain": {"ward_core": "0.1", "dafny": "4.11.0", "z3": "4.12.1",
                 "enforce_boundary": true, "verification_time_limit": 30},
  "functions": [
    {
      "name": "pay",
      "tier": "Proven",                    // from wardcore tier_pass
      "obligations": 7,
      "verify_s": 1.4,                     // from dafny_runner verify_s
      "proof": "verified",                 // or "timeout" / "failed"
      "emitted_dafny_sha256": "…"          // binds source → emitted Dafny (E1 byte-identical gate)
    }
  ],
  "trust_boundary": [                      // exposes exactly what was trusted
    {"extern": "stripe_charge", "trust": "gateway contract", "monitor": true}
  ],
  "verdict": "VALID"                       // only meaningful via the checker (below)
}
```

**Design constraints (why each field):**

- `source_sha256` + `emitted_dafny_sha256`: the certificate is only meaningful if the code
  checked is the code shipped. The Phase-2 E1 gate already proves transpilation is
  **70/70 byte-identical** — deterministic emission is what makes a hash-binding certificate
  sound at all. This is the single most important existing result for this plan.
- `emitted_dafny_sha256` is **declared, not independently re-derived** at Level-1 checking:
  the standalone checker has no transpiler, so it validates the field's presence/format and
  its internal consistency with the module, but the source→emitted binding is *trusted* at
  this level. Independent re-derivation arrives only with the Phase-3 standalone checker
  (which will re-transpile and compare). This is stated explicitly so the checker never
  implies more checking than it can do.
- `enforce_boundary` + `verification_time_limit` in the toolchain block are what make
  `monitor: true` checkable: the checker validates each `trust_boundary.monitor` flag
  against the recorded `enforce_boundary` setting instead of guessing.
- `trust_boundary`: the one place a certificate cannot prove everything. The manifest makes
  the trust explicit and reviewable — the auditor sees *exactly* which externs were trusted,
  with which `trust:` strings, and whether the runtime monitor wrapper is present.
- `tier` per function: the certificate inherits the tier semantics — Tested-tier entries
  carry property-test evidence, not full proofs; Proven-tier entries carry full proof. A
  checker must reject a Proven claim whose obligations were actually discharged at Contracted.

---

## 4. The checker — a standalone verifier

`cert-check pay.ward0 pay.proof` is a standalone, dependency-free checker. Its job:

1. **Rebind** — recompute `source_sha256` of the `.ward0` and compare to the certificate.
2. **Validate structure** — every field present, hashes well-formed, tiers legal
   (from `wardcore/ir.py`'s Tier enum semantics), obligations ≥ 0.
3. **Validate the trust manifest** — every extern declared in the source appears in the
   manifest with a non-empty `trust:` string; `monitor: true` where `enforce_boundary`
   applies. Missing or vacuous trust = reject (this makes Phase-2's T3 rule —
   "contract-less externs are structurally impossible" — visible to third parties).
4. **Validate the verdict against tier rules** — Proven entries must record
   `proof: "verified"` with obligations > 0; Contracted may record bounded outcomes;
   Tested must NOT claim full proofs.
5. **Exit code** — 0 = VALID, 1 = INVALID with the failing field named.

**Honesty constraint:** the checker validates the *artifact* and the *tier semantics*, not the
SMT proof itself. This is the "self-contained" level — it moves trust from "the Ward lab" to
"a published, reviewable artifact format." Two distinct later milestones are kept separate:
(1) **Level-2 re-derivation of the emitted Dafny** (Phase-3 standalone checker, §10 — built
and probed feasible): the checker re-transpiles the bound ward0 source itself and verifies
`emitted_dafny_sha256` independently, closing the declared-not-re-derived gap; (2) a full
independent *re-proof* of the SMT obligations with no Dafny/Z3 — still the long pole, a
separate future milestone.

**Implementation note:** the minimal checker is a *stripped structural re-implementation*
(~150-250 lines, no heavy deps) — it must run on a machine with neither Dafny nor Z3 nor the
ward-core package. Reusing `validate_module()` wholesale would pull in the full IR
type-checker; that is a fine *richer* variant (CI-side), but it is not the portable artifact
checker this plan's pitch promises. The two are stated separately so the "tiny checker"
claim and the "reuse existing logic" claim don't contradict each other.

---

## 5. Where it plugs into the existing code — hook by hook

| Plan piece | Existing code it uses | Change needed |
|---|---|---|
| Certificate emission | `phase0/transpiler/transpiler.py` — `transpile()`, `trust_report`, `_wrapper_dafny` | emit a `trust_boundary` manifest from the already-collected `trust_report` (today it is collected but not exported) |
| Per-function proof outcome | `phase0/harness/dafny_runner.py` — `verify()` returns `(ok, detail)`; `verify_dafny()` already supports `--filter-symbol` per-symbol verification | record `verify_s` + per-symbol outcomes into the artifact (the harness already measures these in run logs) |
| Tier assignment | `phase0/wardcore/ir.py` (Tier/EffectKind enums) + `tier_pass.py` + `validate_module()` | the certificate's per-function `tier` comes straight from `tier_pass`; `validate_module()` becomes the checker's structural validator |
| Determinism guarantee | `phase0/experiments/runs/e1_gate_verify.log` — **70/70 byte-identical** plain + enforce | none — this is the load-bearing existing result |
| The checker | `phase0/wardcore/validate_module()` (trust/contract mandatory checks) | a thin new module (`cert_check.py`): stripped structural re-implementation for the portable artifact checker, with the richer `validate_module()`-based variant reserved for CI-side use (see §4 implementation note) |
| Cost measurement | `phase0/harness/evaluate*.py` + token-economics accounting (`T_arm = G·A + ¼Σ\|cᵢ\|`) | measure the certificate's marginal cost per task |

**What is genuinely new code (small):**
1. `phase0/harness/certificate.py` — assembles the `.proof` JSON from existing outputs.
2. `phase0/harness/cert_check.py` — the standalone checker (~150-250 lines).
3. A `--emit-cert` flag on the future `ward check` CLI.

---

## 6. Implementation phases (all marked design until measured)

**Phase A — feasibility probe (cheapest, do this first).**
Take 3-5 w-tasks through the existing harness and emit a prototype certificate JSON for each.
Measure: how many extra seconds/tokens certificate production adds on top of the measured
verify + token economics. Success = marginal cost is single-digit percent.

**Phase B — emission.** Wire `certificate.py` into the harness (or the `ward check` CLI when it
exists), producing `.proof` for every verified module. The `trust_boundary` manifest comes
from the existing `trust_report`.

**Phase C — the checker.** `cert_check.py` using `validate_module()` semantics; end-to-end
test: certificate validates, tampered source/trust/verdict fails. This is the first
*demonstrable* artifact for the README ("verify it yourself, no Dafny").

**Phase D — integration + publication.** Checker runs in CI; `.proof` format documented;
README's roadmap updated to show verification-today → certificate-as-shipping-format.

---

## 7. Pre-registered gates (repo's fact-check discipline)

- **G1 (cost):** certificate production adds ≤ 5% to measured per-task verify+token cost.
- **G2 (fidelity):** the checker's verdict agrees with the harness's measured verdicts on the
  8 w-tasks (0 false VALID, 0 false INVALID).
- **G3 (tamper-evidence):** modifying source, a trust string, or a recorded verdict
  invalidates the certificate (exit 1) in every test case.
- **G4 (no-Dafny):** the checker runs on a machine without Dafny or Z3 installed.

## 8. Explicit non-claims

- Not a new logic (Hoare logic; see README Formal foundations).
- Not a cryptographic signature or PKI attestation — it is a *reproducibility artifact*;
  a signed/attested variant is a later, separate decision.
- Not an independent **re-proof** of the SMT obligations — that remains a separate future
  milestone. Phase-3 Level-2 (§10) independently re-derives the *emitted Dafny* from the
  bound source (closing the hash-binding gap); it does not re-run the solver.
- Not a guarantee that the model wrote good code — only that the shipped code satisfies the
  shipped contracts, at the declared tier, and that the trust boundary is fully visible.

---

## 10. Phase 3 — the standalone re-deriver (Level-2 checking) — BUILT + PROBED

**What this closes:** at Level-1, `emitted_dafny_sha256` is *declared, not independently
re-derived* — the checker has no transpiler, so the source→emitted binding is trusted. That
is the one declared honesty gap of the Level-1 checker. Phase 3 closes it inside the same
standalone, dependency-free checker.

**Design (built, `phase0/harness/cert_rederive.py`):** a stdlib-only re-implementation of the
ward0→Dafny emission — hand-rolled lexer + recursive-descent parser (mirroring the tree
shapes the emit logic indexes, including lark's `?`-inlining of single-child rules) + the
emit logic ported verbatim from `transpiler/transpiler.py` (hoisted-call `wN` counter with
`_used_names` collision skip, `_subst` result→r in wrapper ensures, var_decl type-drop,
`and`/`or`→`&&`/`||`, factor MINUS-vs-parens, quantifier bounds, `call_stmt` discard, both
enforce modes incl. the `_checked` wrappers). No Dafny, no Z3, no lark, no transpiler, no
wardcore, no model — stdlib only, so **G4 (no-Dafny) is preserved** by construction and by a
fresh-interpreter test.

**Why this is sound:** the certificate is only meaningful if the code checked is the code
shipped. E1 already proves the *real* emission is byte-identical (deterministic hashes bind
code↔proof); the re-deriver independently re-derives that same emission inside the checker,
so a certificate passes Level-2 **iff** the bound source actually transpiles to the recorded
Dafny. No code is shared with the real transpiler — a bug in one cannot silently propagate to
the other; corpus identity is enforced by tests.

**Checker wiring (built):** `cert_check.py --re-derive` runs `validate_level2(proof, source)`
— re-transpiles the bound source honoring `toolchain.enforce_boundary`, compares the
re-derived hash to the recorded `emitted_dafny_sha256`, and reports a named violation on any
mismatch or re-transpile failure. **Never raises** (malformed toolchain/source → clean
INVALID, exit 1). Level-1 checks are untouched (`--re-derive` off by default), so G2/G3 and
the Level-1 exit contract are unchanged.

**Feasibility probe (measured, 2026-08-01):**
- **Byte-identity:** the re-transpiler matches `Ward0Transpiler` **98/98** (task × enforce)
across the full composed benchmark corpus (w1–w12 + t2/t3) — both enforce modes.
- **Artifacts:** **5/5** committed `cert_probe` artifacts re-derive their recorded
  `emitted_dafny_sha256` (`python -m harness.cert_rederive --probe`):

```
task                source_ok  enforce hash_match
w1_payment_chain    True       False   True
w4_order_placement  True       False   True
w5_currency_roundtripTrue      False   True
w6_crud_handler     True       False   True
w7_idempotency      True       False   True
re-derived emitted_dafny_sha256 matches on 5/5 artifacts
```

- **Tests:** `harness/test_cert_check.py` 25/25 (incl. Level-2 fidelity, byte-identity,
tamper on source *and* on the recorded emitted hash, never-raises on malformed inputs, CLI
exit codes, `--source` required). Full regression green: wardcore suite, transpiler/harness/
grammar suites, gates E1 (73/73 + 73/73) / E3 / E4 / E4b / E5 / E5-real / E6 / E8.

**Honest boundary:** the re-transpiler's soundness is scoped to the E1-validated corpus
(keywords like `range` are lexed globally here, contextually in lark — corpus-invisible, and
byte-identity is re-checked by tests on every composed source).

**Gate (pre-registered here):** **G5 (Level-2 re-derivation):** every committed `cert_probe`
artifact re-derives its recorded `emitted_dafny_sha256` in stdlib-only code, and tampering
with either the source or the recorded hash invalidates under `--re-derive` (exit 1) while
leaving Level-1 behavior unchanged. **Verdict: PASS** (5/5).

---

## 9. Why this is the industrial-revolutionary one

*Status note: §10 (Phase-3 Level-2 re-deriver) now sits after §9 — the productization pitch
above is unchanged; §10 is the deeper honesty upgrade that makes the certificate verifiable
end-to-end by a third party with no Ward code at all.*

Verification is a *process*; certificates are a *market*. Every AI coding tool in 2026 can
claim "we verify." None ships a format that lets a third party check the claim without the
tool, the model, or the vendor. Ward already has the three things this needs that nobody else
has assembled: a **deterministic emission** result (E1: 70/70 byte-identical), a **tiered
cost control** (measured C1/C2), and a **visible trust boundary** (`trust_report` +
`enforce_boundary` wrappers). The certificate turns those three into a product.
