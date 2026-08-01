# Ward Phase 0 — Adapted Solo Execution Plan

**Source:** compiled from `ward-phase0-execution-plan.pdf` (§5–§8), adapted to a single
engineer on one machine (Windows 11, RTX 4070 Laptop 8GB, .NET 7, Python 3.11, Node 24).
**Status:** planning document. Not yet started.
**Date:** 2026-07-31

---

## 1. Goal (unchanged from the original plan)

Falsify or validate the two highest-leverage, cheapest-to-test claims **before any
language construction begins**:

- **A (surface-syntax hypothesis):** a Python/TS-shaped surface syntax (`ward0`) that
  deterministically elaborates into Dafny outperforms generating Dafny directly.
- **B (FFI boundary hypothesis):** a contract-stub trust boundary catches more caller
  errors at library call sites than unverified direct calls.

Phase 0 requires **no new language and no model training**.

---

## 2. Environment reality check

| Resource | Original plan assumes | This machine | Impact |
|---|---|---|---|
| Dafny | Installed toolchain | **Not installed** | Install via `dotnet tool install -g dafny` (needs .NET 8 runtime — current is 7.0.410; upgrade .NET first, both take < 1 hour) |
| GPU | 1× A100 (32 GB) | RTX 4070 Laptop, 8 GB VRAM | Cannot run Qwen2.5-Coder-32B or GPT-OSS-120B locally. 7B fits comfortably, 14B fits quantized. See §6 (model decision) |
| Python | 3.x | 3.11.9 ✓ | Fine (lark for PEG grammar, harness) |
| Node | — | v24.15.0 ✓ | Fine (optional, if harness is JS) |
| torch / inference stack | Installed | **Not installed** | Full setup day (llama.cpp or vLLM backend + GGUF model download) |
| Disk | — | 122 GB free ✓ | Fine |
| Team | 2–3 engineers | 1 | Timeline stretches 12 → 16–20 weeks (see §4) |

**Consequence:** absolute model accuracy on this machine will be below the original
plan's expectation. The Phase 0 gates are *relative* comparisons (treatment vs.
baseline under identical conditions), so the experiment remains valid — but weaker
models mean smaller effect sizes, which interacts with the statistics problem in §3.1.

---

## 3. Adaptations to the experiment design

### 3.1 Statistical power (important correction to the original plan)

The original gate A1 requires "statistically significant (p < 0.05) improvement of
≥ 5 absolute percentage points" on pass@1. With 50–80 tasks per cell, that is
**impossible to satisfy**: detecting a 5 pp difference at 80% power needs ~200+
tasks per condition in a paired design. With 65 tasks, you can only detect ~15+ pp
effects at p < 0.05.

**Adaptation:**
- **Primary metric becomes pass@5 (paired).** With k=5 samples per task per
  condition, effect sizes are larger and the paired comparison is much more
  sensitive. Requires ~65–80 tasks per cell to detect meaningful differences.
- Report pass@1 as secondary, exactly as the original plan does with the
  compile-error metric (it prevents metric substitution without being the gate).
- Pre-register: McNemar's test (or exact binomial on discordant pairs) on the
  primary metric; report effect size + confidence interval, not just p.
- **Gate A1 is reinterpreted as directional evidence**, not a hard statistical
  kill: A1 passes if the treatment direction is positive AND the effect is
  consistent across task tiers and both models (§6). This is stated explicitly in
  the go/no-go criteria (§8) so the paper can't be accused of moving goalposts.

### 3.2 Dafny version & `Result<T, E>`

Dafny 4.x has no built-in `Result<T, E>`. The transpiler must emit a hand-rolled
datatype (`datatype Result<T, E> = Ok(value: T) | Err(error: E)`), and hidden tests
must pattern-match on it. Budgeted in §4.

### 3.3 Task set size

- Keep the 3-tier structure from the original plan (Trivial 15 / Standard 30 /
  Tricky 20–35), total ~65–80.
- All tasks must be implementable as **pure functions** in the ward0 subset
  (no heap mutation, no classes) — this is what keeps the transpiler simple.
- 20% held out for final evaluation, frozen after week 4.

### 3.4 Sub-experiment B — unchanged in scope, cheaper to run

6–10 stub scenarios (payment, auth, DB, REST, currency) with deliberately buggy
stub behavior in 30–50% of hidden tests. No GPU required. This is the most
runnable experiment for a solo engineer and produces the most publishable,
unambiguous numbers. **Run it even if A's power is weak** — B is not power-limited.

---

## 4. Timeline (solo, 16 weeks + 2 weeks buffer)

| Week | Work | Deliverable |
|---|---|---|
| 1 | Environment: upgrade .NET, install Dafny CLI, verify `dafny verify` on a hand-written example; Python venv + lark; repo skeleton (`phase0/` with `grammar/`, `transpiler/`, `harness/`, `benchmarks/`, `experiments/`); **model decision** (decision point, §6) | Toolchain smoke test passes |
| 2 | ward0 grammar v0.1 (PEG, strict subset: fn defs, `requires`/`ensures`/`old()`, if/else, bounded for, return, int/bool/str/Unit/`Result<T,E>`/`List<T>`; no classes/closures/generics beyond Result+List); grammar tests; internal review against the 10 task examples | `ward0.lark` + grammar test suite |
| 3–4 | Transpiler `ward0 → Dafny` (~1.5K lines): syntax-directed translation, `old(x)` → Dafny `old`, `-=` → field update, `Result<T,E>` → hand-rolled datatype, **hard-error on anything untranslatable**; transpiler test suite; task set v1 (65–80 tasks with contracts, 3 tiers); freeze 20% holdout | Transpiler + tests + `tasks.json` |
| 5–6 | Evaluation harness: prompt templates (Direct-Dafny vs Ward-Surface), model invocation (local or API), parse/verify orchestration (generate → transpile → `dafny verify` → hidden-test run on compiled code), result logging (JSONL) | Harness runs end-to-end on 5 smoke tasks |
| 7–8 | Constrained decoding for the Ward-Surface arm (XGrammar or Outlines if the chosen backend supports it; otherwise fallback: grammar check + regenerate — documented limitation); Sub-experiment B stubs + caller scenarios + hidden tests with error injection | Decoding integration; B benchmark |
| 9–10 | **Run Experiment A** (all conditions × all tasks × k=5). Record per-task: syntax-error rate, verification-pass rate, hidden-test pass rate, time | Full A dataset |
| 11–12 | **Run Experiment B** (baseline vs. trust-boundary conditions; measure violation catch rate, false-positive rate, bug escape rate) | Full B dataset |
| 13–14 | Held-out rerun with frozen prompts (contamination check); statistical analysis (McNemar on primary, effect sizes + CIs, tier-stratified breakdowns); **Phase 0.5 Verus spike if A1 shows positive signal** (build-vs-extend sanity check: transpiler port ~1 week) | Analysis notebook + results tables |
| 15–16 | Write-up (workshop-paper length 6–10 pages, incl. negative results), go/no-go presentation | Report + decision doc |
| 17–18 | Buffer (retries, hardware failures, unexpected model quality issues) | — |

**Optional (not on the Phase-0 critical path):** trace-seeded verified corpus build —
see §10. Deferred to Phase 5; only worth weeks of work if the go/no-go is positive.

---

## 5. Deliverables checklist (mapped to original plan §7)

1. Reproducible benchmark suite: 65–80 tasks + 6–10 FFI scenarios, documented. ✓ (weeks 3–6)
2. Transpiler codebase `ward0 → Dafny` (~1.5K lines) with tests. ✓ (weeks 3–4)
3. Evaluation harness with constrained-decoding integration. ✓ (weeks 5–8)
4. Technical report with all numbers including negative results. ✓ (weeks 15–16)
5. Go/no-go decision document: proceed to Phase 1 / pivot to Verus extension / halt. ✓ (week 16)
6. (Optional, Phase 5) Trace-seeded verified corpus pipeline — see §10. ✓ (post-go/no-go)

---

## 6. Model decision (open — see §4, week 1)

| Option | Fits 8 GB? | Setup | Cost | Notes |
|---|---|---|---|---|
| **A. Local Qwen2.5-Coder-14B** (GGUF Q4) via llama.cpp | Yes (tight) | 1–2 days | Free | Slow (~1–2 min/task at pass@5); reproducible; matches "open-weight model" spirit of the plan |
| **B. Local Qwen2.5-Coder-7B** | Yes (comfortable) | 1 day | Free | Fastest local option; lowest absolute accuracy — may compress the A/B gap and dilute the treatment effect |
| **C. API model** (DeepSeek-V3/Chat, or GPT-OSS-120B if cheap API exists) | N/A | Hours | ~$50–200 for full run | Stronger model = more realistic effect sizes; less reproducible (model versions drift); requires API key |
| **D. Hybrid** | — | — | — | Local for smoke/dev; API for the final recorded runs |

**Recommendation:** A for development, then D (record final runs on API) if budget
allows — the recorded-runs model matters less than identical conditions across both
arms. Cross-check arm on a second model only if time permits (original plan's
"cross-check" role).

---

## 7. Risk register (solo additions, on top of original plan §10)

| Risk | Severity | Mitigation |
|---|---|---|
| Dafny's `Result<T,E>`/datatype syntax + Qwen's weak Dafny fluency inflate Direct-Dafny syntax-error rate, making A2 trivially easy but A1's baseline noise high | Medium | Verify on 10 hand-written Dafny tasks first; report per-tier breakdowns; consider skipping Direct-Dafny condition on Tricky tier if syntax-error rate > 50% (document why) |
| 8 GB VRAM limits model choice | Medium | §6 options; relative comparison stays valid |
| Solo timeline slippage | High | Buffer weeks 17–18; B experiment independent of A — do B even if A slips |
| Local inference too slow for pass@5 over 150+ task-runs | Medium | Start early (week 9); cache all generations (JSONL); resume from cache |
| Hidden-test suite buggy → vacuous-verification false signal (A3 gate) | Medium | Cross-check hidden tests against a correct reference solution per task before running models; only then freeze |
| Prompt leakage / contamination | Low | Held-out set frozen; prompts frozen in week 13 rerun |
| Constrained decoding not supported by chosen backend | Medium | Fallback documented: post-hoc grammar validation + single retry; this weakens A2's claim but not A1's |

---

## 8. Go/no-go criteria (adapted, pre-registered)

Proceed to Phase 1 only if:

| Gate | Original threshold | Adapted threshold (solo, pass@5 primary) |
|---|---|---|
| **A1** | ≥ 5 pp pass@1, p < 0.05 | Positive direction on pass@5 across ≥ 2 of 3 tiers AND on both models (if two models used); report CI. Statistical significance reported honestly, not gated on |
| **A2** | Ward-Surface syntax-error rate < 2% | Ward-Surface syntax-error rate < 2% (transpiler-level; grammar-constrained) |
| **B1** | ≥ 30% relative reduction in runtime bugs vs. baseline | ≥ 30% relative reduction in contract-violation escape rate vs. baseline |
| **B2** | ≤ 15% false positives | ≤ 15% of valid caller implementations rejected |

Decision rules (from original plan, kept):
- A1 fails → core hypothesis (surface syntax helps) falsified → halt or pivot.
- B1 fails, B2 passes → FFI boundary redesign before Phase 1.
- Both B fail → gradual-verification approach needs rethinking.

---

## 9. First concrete steps (this week)

1. Upgrade .NET to 8.x (`winget upgrade Microsoft.DotNet.Runtime.8` or dotnet-install script) and install Dafny: `dotnet tool install -g dafny` — verify `dafny --version`.
2. Create repo skeleton under `WARD/phase0/` (folders listed in §4 week 1).
3. Write the ward0 grammar v0.1 (PEG) + grammar tests.
4. Draft the first 10 benchmark tasks (from the PDF's examples: `max_of_list`, `is_sorted`, `transfer`, `validate_email`, `apply_discount`, `binary_search`, `run_length_encode`, …) with contracts in both ward0 and Dafny by hand.
5. **Decide the model** (§6) once the harness smoke test exists — the decision is deliberately deferred until the harness can prove it works with a cheap local model.

Everything in this plan is runnable by one person on this machine; the experiment
remains falsifiable, and the deliverables are the same as the original Phase 0.

---

## 10. Optional Phase-5 input: trace-seeded verified corpus

**Status:** optional; not needed for Phase 0, deferred to Phase 5 (§8 of the design
doc). Uses the local `Complete-FABLE.5-traces-2M` mirror (228,968 verified rows;
`phase0/experiments/fable_report.json` + `fable_analysis.py` quantify it).

**Framing — "mine trace tasks → rewrite → verify → own corpus".** The traces
contain **no ward0/Dafny and no verification content**; they are raw material for
building a *verified synthetic corpus*, never finished training data. The released
corpus is original work (own spec prose + own verified solutions), so it carries
neither AGPL copyleft nor Anthropic anti-distillation exposure in any serious
reading. Do **not** SFT directly on raw trace text.

### 10.1 Pipeline

1. **Mine** — extract candidate task prompts from trace message bodies. Prefer
   permissive, well-structured sources first (see ledger below).
2. **Rewrite** — re-express each task in your own words as a ward0 spec. Ideas are
   not copyrightable expression; copied text is. Never copy trace text verbatim.
3. **Verify** — write the ward0 reference (contracts + hidden tests), transpile,
   `dafny verify`; keep only what proves. Optionally also mine agentic
   repair-loop episodes (Bash error → edit → re-run) as Phase-5 behavior data.
4. **Own corpus** — the verified pairs (spec → verified ward0) become the SFT/RL
   corpus, trained against the verifier as reward.

### 10.2 Per-source license ledger (from the HF card's attribution table)

| Source | Rows | License | Phase-5 use |
|---|---|---|---|
| `greghavens/fable-5-coding-and-debugging-traces` | 13,938 | CC-BY-4.0 | **Seed source (preferred)** — has explicit `lang`/`domain`/`tools` fields |
| `Roman1111111/claude-sonnet-4.6-100000X-filtered` | 107,085 | MIT | Usable (attribution noted) |
| `Roman1111111/claude-opus-4.6-10000x` | 9,598 | MIT | Usable |
| `angrygiraffe/claude-opus-4.6-4.7-reasoning-8.7k` | 17,363 | Apache-2.0 | Usable |
| `Met4physics/claude-opus-4-8-xhigh-reasoning-8.7k` | 17,348 | Apache-2.0 | Usable |
| `TeichAI/lordx64-claude-opus-4.7-max-cleaned` | 4,801 | Apache-2.0 | Usable |
| `TeichAI/Claude-Sonnet-4.6-Reasoning-1100x` + `Claude-Opus-4.6-Reasoning-887x` | 1,967 | Apache-2.0 | Usable |
| `Poumrm/Mythos-5-and-Fabel-5-Class-Model-Outputs` | 1,321 | Apache-2.0 | Usable |
| `victor/fable-5-boeing-747-trace` · `TheFusionCube/Fable-5-CoT-Traces` | 1,162 | MIT | Usable |
| `licongxu` · `AlinCiocan` · `victor/worldcup` (small) | 703 | CC-BY-4.0 | Usable |
| `1EYE4ALL/Fable-5-traces` | 16,979 | AGPL-3.0 | **Ideas only** (rewritten specs; no verbatim text in any released set) |
| `lordx64/fable-tool-use-sft` · `Swarm-AI-Research/fable5-traces-sft` · `Glint-Research/Fable-5-traces` · `lordx64/agentic-distill-fable-5-sft` · `cfahlgren1` | 18,299 | AGPL-3.0 | **Ideas only** (excludes 1EYE4ALL, own row above) |
| `armand0e/claude-fable-5-claude-code` (11,947) + other `see upstream` sources | ~18,000 | see upstream | **Exclude** until the upstream license is confirmed |

**Rules:**
- **Rewrite-don't-copy:** every seed becomes your own spec prose + your own
  verified solution. No verbatim trace text in the released corpus.
- **Provenance ledger:** per corpus row record `{seed_trace_id, source_dataset,
  source_license, rewritten_spec, verified_ward0_path, dafny_verified_at}`.
- **License gate:** no AGPL-sourced text in any released SFT set; `see upstream`
  sources are excluded until verified.
- The HF card's `license: mit` tag covers only the mirror's packaging, not the rows.
