# PROMPTGUARD EVALUATION EXECUTION SUMMARY

Execution date: 2026-10-26
Commits: `bd9ec57` (pushed to origin/main), `435e82c` (pinned evaluation steps, local, unpushed)

## Plan statuses

| Plan | Status |
|---|---|
| Plan 1: production baseline | PASS |
| Plan 2: baseline comparison + controlled regression | PASS |
| Plan 3: verification + evidence bundle | PASS |
| Calibration (Level 2.2) | BLOCKED — human labels/review unavailable |

## Plan 1: production baseline

- Run ID: `44af1572-2146-4497-91fa-55252b48499f`
- Report: `runs/44af1572-2146-4497-91fa-55252b48499f.json`
- Command: `uv run promptguard --limit 10`
- Prompt version: v1, dataset: golden-v1, model: `nvidia/nemotron-3-ultra-550b-a55b`
- Category accuracy 80.00% (8/10), avg summary score 4.60/5,
  avg latency 5761.68ms, tokens 5397 in / 1503 out
- Regression: INCONCLUSIVE (expected — first run, no baseline and no thresholds)
- 2 persistent category misses: `banking77-007` (expected
  `balance_not_updated_after_bank_transfer`, got `pending_transfer`),
  `banking77-009` (expected `beneficiary_not_allowed`, got `failed_transfer`)
- Eval config hash: `aafd707c401b07889b6000e07b52a76c0c2c4e2a79d9dc32ed927265d5234868`

## Plan 2: baseline comparison + controlled regression

- Explicit baseline (never `latest`): `44af1572-2146-4497-91fa-55252b48499f`
- Candidate run ID: `bf766263-2be2-406c-9cf8-c6ffd2451ab3`
- Report: `runs/bf766263-2be2-406c-9cf8-c6ffd2451ab3.json`
- Command: `uv run promptguard --limit 10 --baseline 44af1572-2146-4497-91fa-55252b48499f --max-category-accuracy-drop 0.05 --max-summary-score-drop 0.5`
  with `NVIDIA_REQUEST_TIMEOUT_SECONDS=60` (transport-only, not part of
  eval_config_hash)
- Thresholds: `maximum_category_accuracy_drop=0.05`,
  `maximum_summary_score_drop=0.5`
- Deltas: category accuracy +0.00%, summary 4.60 → 4.56 (−0.04),
  latency −2252.62ms, output tokens +122, category drift (TV) 0.0000
- Gate: **PASS** (exit 0); per-case: unchanged_pass ×8,
  unchanged_failure ×2 — the same two persistent category misses, not
  new regressions
- Identical eval_config_hash on both runs; the candidate's
  baseline_run_id resolves to a persisted file
- Controlled-regression test
  (`tests/integration/test_pipeline.py::test_baseline_regression_detects_new_failure_and_drift`)
  PASSES: asserts FAIL status, `category_accuracy_drop_pct` violation,
  per-case `new_failure`, and drift TV 0.2 on a scripted degraded candidate

## Plan 3: verification + evidence bundle

| Check | Command | Result |
|---|---|---|
| Tests | `uv run pytest -q` | PASS — 377 passed, 1 skipped (pre-existing Windows symlink skip) |
| Lint | `uv run ruff check src tests scripts` | PASS — All checks passed |
| Type check | `uv run mypy src` | PASS — no issues in 38 source files |
| Lock | `uv lock --check` | PASS — exit 0, 90 packages |

Evidence bundle: `reports/execution-evidence/` (chosen because the repo
already has a `reports/` directory; no new top-level directory invented).

Reproducibility metadata checks:
- Baseline and candidate reports both retained in `runs/`
- Both run IDs unique; the candidate's baseline reference resolves
- Evaluator fingerprint present in both reports, including pinned
  evaluation steps, `evaluation_steps_sha256`
  (`e6a4392e0ca106b0c27d0906169e81dc20134bcc4fcab13fcfb9ecb31560b817`),
  rubric sha256
  (`7ef7ca7fdfc4703c97ea933f9c5aa7a6afb0d658f647ce2e4ed2aaac2637b1d5`),
  and deepeval version 4.1.10
- Prompt and dataset versions recorded on both reports
- Thresholds and comparison results retained on the candidate report
- Errors are visible (INCONCLUSIVE reasons recorded in report JSON)

## Calibration status: BLOCKED — human labels/review unavailable

- `ensure_manifest_approved()` gate intact and active (refuses the
  provisional manifest; produces no candidate outputs)
- `card_pin_inclusion_review.csv` decision fields blank pending a real
  human reviewer; blank is also what existing tests require
- LLM recommendations exist only as PROVISIONAL/MODEL-GENERATED notes
  (`dataset/calibration/v1/card_pin_recommendations.md`) and are
  advisory-only, never used for approval
- No human-calibration report exists or is claimed

## Known limitations (full details in limitations-and-blockers.md)

- Judge score variability across identical-config live reruns
- Cost not captured (tokens and latency are)
- No explicit report schema_version field
- Intermittent judge `KeyError: 'score'` aborts a run (DeepEval
  4.1.10 fallback path drops the schema kwarg); retry is the workaround
- Default 10s provider timeout too low under slowness; 60s env override used
- `ruff format --check` fails on ~43 pre-existing unrelated files
- Windows symlink path-safety test skipped

## Canonical/production integrity

- Production prompt: UNCHANGED (v1)
- Canonical golden dataset: UNCHANGED (golden-v1, 50 cases)
- Regression infra: demonstrated working (live PASS gate, live per-case
  statuses, automated FAIL-path test, drift TV, baseline compatibility
  validation)
- Evaluator calibration: NOT demonstrated (blocked on human card_pin
  review and annotations)

Exact next action for the calibration blocker: a human reviewer fills
`dataset/calibration/v1/card_pin_inclusion_review.csv` decision fields
from their own review; then deterministic manifest regeneration;
then annotation; then calibration runs.
