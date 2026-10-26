# PromptGuard

Production-oriented LLM evaluation and regression detection system.

## Architecture

```
domain/           pure Pydantic models + rules (no infrastructure imports)
  models.py         EvaluationResult, EvaluationReport
  regression.py     EvaluationComparison, RegressionResult
  thresholds.py     RegressionThresholds, ThresholdViolation
  drift.py          CategoryDriftResult
application/      services + contracts (ABCs implemented by infrastructure)
  evaluation_service.py    evaluates golden cases -> EvaluationResult[]
  reporting_service.py     aggregates results -> EvaluationReport
  comparison_service.py    baseline deltas + threshold evaluation
  drift_service.py         category-distribution drift
  calibration_service.py   judge/human agreement and bias metrics
  run_service.py           orchestrates the full pipeline
  contracts.py             EvaluationRunRepository, ReportFormatter,
                           Notifier, LLMClient
infrastructure/   concrete adapters (NVIDIA, DeepEval, JSON, Slack, text)
  calibration/             human-labelled calibration-data loader
  evaluation/rubric.py     versioned, content-hashed judge rubric
  evaluation/evaluator_config.py  non-secret evaluator identity
interfaces/       CLI composition roots
  calibration_cli.py       promptguard-calibrate entrypoint
```

## Data flow

```
evaluate -> aggregate -> compare (optional baseline) -> drift
        -> thresholds -> persist -> report -> notify (optional)
```

## CLI

```
promptguard [--prompt-version v1] [--baseline latest|RUN_ID] [--limit N]
            [--min-category-accuracy 0.8] [--min-summary-score 3.5]
            [--max-latency-ms 2000]
            [--max-category-accuracy-drop 0.05]
            [--max-summary-score-drop 0.4]
            [--max-category-accuracy-drop-pct 10]
            [--max-summary-score-drop-pct 15]
            [--runs-dir runs]
```

`--prompt-version` and `--baseline` values must be simple identifiers
containing only letters, digits, dots, hyphens, or underscores, and must
start with a letter or digit. Repository paths are resolved and constrained
to their configured directories. Threshold values must be finite; an infinite
latency threshold is rejected rather than disabling the latency gate.

Exit codes: `0` = PASS, `1` = FAIL, `2` = INCONCLUSIVE or a
configuration/provider/data error. A run is INCONCLUSIVE when no
thresholds are configured, when a configured drop threshold cannot be
evaluated because the baseline is missing or incompatible, when no cases
were evaluated, or when the case set or critical-case set differs from
the baseline. Absolute thresholds can pass without a baseline. All
configured thresholds are mandatory: every absolute threshold must pass,
and every drop threshold must be evaluable and pass. Any violation fails
the run; mixed absolute and drop policies never pass on a partial
evaluation. `--limit` must be at least `1`; an empty run can never satisfy
a quality gate.

### Judge calibration

```
promptguard-calibrate --file calibration.json [--output report.json]
                      [--min-samples 20] [--max-mae 0.75]
                      [--min-pearson 0.6] [--min-within-one 0.8]
                      [--max-absolute-bias 0.5]
```

Calibration input must be an object with dataset metadata and an
`examples` list. Every example requires a human-reviewed score on the same
1-5 scale as the judge. The rubric version and content hash must match the
current rubric, so labels cannot silently survive a rubric change:

```json
{
  "metadata": {
    "version": "calibration-v1",
    "rubric_version": "v1",
    "rubric_sha256": "<current summary-quality rubric hash>"
  },
  "examples": [
    {
      "id": "case-1",
      "expected_summary": "Human reference summary",
      "actual_summary": "Candidate summary",
      "human_score": 4
    }
  ]
}
```

The command scores each candidate with the configured judge and reports
mean absolute error, RMSE, Pearson and Spearman correlation, exact and
within-one agreement, and mean bias. It also records the dataset version,
content hash, sample count, rubric identity, evaluator identity, and every
per-example human/judge score. High correlation alone cannot pass: the
MAE, within-one agreement, and absolute-bias limits are mandatory. Exit
codes are `0` when all calibration limits pass, `1` when they fail, and `2`
for configuration or provider errors. Do not use provisional or
model-generated labels as human ground truth.

Calibration data preparation is documented in
[`dataset/calibration/README.md`](dataset/calibration/README.md). The v1
plan selects 100 unlabeled BANKING77 cases, two per prompt category, and
requires human-written references plus 20% double annotation. The
repository currently contains only the schema, sampling plan, and
unlabeled manifest; no reference labels are included. The `card_pin`
entries are provisional: BANKING77 has no exact label, and the proposed
`get_physical_card`/`pin_blocked` mapping needs human inclusion review. For
the v1 plan, run the command with `--min-samples 100` only after that
review is complete.

Reports are persisted as complete JSON runs under `runs/` and rendered
to the console. When a baseline is used, the comparison deltas are stored
on the report and rendered even when the gate passes. Every report carries
an `eval_config_hash` fingerprinting the prompt, dataset, model, test
cases, sampling configuration, and evaluator identity: judge provider and
model, endpoint, rubric version and content hash, and DeepEval version.
Changing any of those evaluator inputs invalidates prior baselines.
Gate thresholds are persisted separately and are deliberately excluded
from the fingerprint, so changing a release policy does not invalidate a
comparable baseline. A baseline without a fingerprint is INCONCLUSIVE
rather than silently accepted. Reports created before evaluator identity
was added remain loadable, but a legacy fingerprint cannot prove judge
identity and will not match a new run; that baseline is INCONCLUSIVE
rather than silently trusted.

Timestamps are normalized to UTC and must be timezone-aware. Reports with
naive, missing, or malformed timestamps are skipped when listing runs and
recorded in `skipped_runs`. Selecting `--baseline latest` refuses to fall
back to an older report when any persisted run file is invalid; the run is
INCONCLUSIVE and the skipped-file diagnostics are printed by the CLI.
Explicit baseline IDs still load their requested report. Aggregate accuracy,
score, latency, and token totals are
validated against the stored case results, and inconsistent evidence is
INCONCLUSIVE rather than trusted. Slack notification is enabled only when
`SLACK_WEBHOOK_URL` is set in `.env`; delivery failures are reported as
warnings and do not change the quality-gate decision.

The application retry policy is the only retry layer: SDK retries are
disabled, and transient provider failures, including timeouts, connection
errors, rate limits, and 5xx responses, are retried up to three attempts.
Requests time out after `NVIDIA_REQUEST_TIMEOUT_SECONDS` (default `10`).
The complete CLI evaluation is bounded by `EVALUATION_TIMEOUT_SECONDS`
(default `900`), so a stuck provider cannot outlive the CI job. Invalid
JSON, schema violations, blank required text, and empty model responses are
deterministic failures and are not retried. Non-finite or out-of-range judge
scores are rejected rather than clamped into a passing score.

Critical cases are marked in the golden dataset with `critical: true`
and a `critical_rationale`. Any critical-case failure fails the run even
when aggregate metrics and baseline comparisons pass.

The Docker build context is filtered by `.dockerignore`; local `.env`
files, VCS data, virtual environments, caches, runs, and reports are not
copied into the image. The runtime image installs the production dependency
set without the dev dependency group and runs as the unprivileged `appuser`.

Set `NVIDIA_TEMPERATURE` and `NVIDIA_SEED` in `.env` to make generation
and judging reproducible (both are optional).

## Development

```
uv run pytest
uv run ruff check src tests scripts
uv run mypy src
```

Test suites:

| Suite | Purpose |
|---|---|
| `tests/unit/` | Fast, isolated tests for each component |
| `tests/integration/` | End-to-end pipeline, persistence, notifications, and CLI wiring |
| `tests/evaluation/` | Evaluation semantics, normalization, drift, and quality-gate acceptance |
