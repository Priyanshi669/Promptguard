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
  run_service.py           orchestrates the full pipeline
  contracts.py             EvaluationRunRepository, ReportFormatter,
                           Notifier, LLMClient
infrastructure/   concrete adapters (NVIDIA, DeepEval, JSON, Slack, text)
interfaces/       CLI composition root
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

Reports are persisted as complete JSON runs under `runs/` and rendered
to the console. When a baseline is used, the comparison deltas are stored
on the report and rendered even when the gate passes. Every report carries
an `eval_config_hash` fingerprinting the prompt, dataset, model, test
cases, and sampling configuration. Gate thresholds are persisted
separately and are deliberately excluded from the fingerprint, so changing
a release policy does not invalidate a comparable baseline. A baseline
without a fingerprint is INCONCLUSIVE rather than silently accepted.

Timestamps are normalized to UTC and must be timezone-aware. Reports with
naive, missing, or malformed timestamps are skipped when listing runs and
selecting the latest baseline; skipped files are recorded in
`skipped_runs`. Aggregate accuracy, score, latency, and token totals are
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
copied into the image.

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
