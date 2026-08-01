# Contributing to WARD

Ward is a research project with pre-registered experiments and honest reporting.
Before opening an issue or PR, read the current phase report — the thesis has
already pivoted once, and the "current truth" lives in the reports, not the
marketing.

## Ground rules

1. **No claim goes into the README or docs that isn't measured.** This project
   explicitly dropped a surface-syntax-superiority claim after a null result
   (McNemar p = 1.0000). Add claims only with run logs.
2. **Reproduce before you report.** Every result in `phase0/` is backed by JSONL
   run logs in `phase0/experiments/runs/` and a pre-registered design doc.
3. **No AGPL-sourced text in any released dataset.** The license gate in the
   phase-0 plan is project law. If you add benchmark or training material, keep
   provenance auditable.
4. **Everything untranslatable must fail loudly.** The ward0 → Dafny transpiler
   hard-errors on anything it can't translate 1:1. Never add a silent fallback.

## Development setup

```bash
git clone https://github.com/PersistenceOS/WARD.git
cd WARD/phase0
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install lark
```

Requires [Dafny 4.11.0](https://github.com/dafny-lang/dafny) and
[Z3 4.12.1](https://github.com/Z3Prover/z3) on your PATH for live verification.

## Running tests

```bash
python -m unittest discover -s grammar -p "test_*.py"
python -m unittest discover -s transpiler -p "test_*.py"
python -m unittest discover -s harness -p "test_*.py"
python -m unittest discover -s wardcore -p "test_*.py"
```

All four suites must stay green (currently 57/57).

## Adding a benchmark task

1. Write a verified reference solution as `<id>.ward0` (it must re-verify with
   Dafny — the integration suite enforces this).
2. Write the `<id>.json` descriptor: `spec`, `tier` (1–3), `holdout` flag, and
   `hidden_tests`.
3. Never show holdout tests to the model — they're frozen by definition.
4. Add a real-model run log before claiming a result.

## Code of conduct

Be rigorous, be honest about negative results, and assume good faith. This is a
falsification-first project: finding out something doesn't work *is* a
contribution.
