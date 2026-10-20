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
promptguard [--prompt-version v1] [--baseline latest|RUN_ID]
            [--min-category-accuracy 0.8] [--min-summary-score 3.5]
            [--max-latency-ms 2000] [--runs-dir runs]
```

Exit code `0` when the run passes (or no thresholds were configured),
`1` on regression failure, `2` on configuration/data errors.
Reports are persisted as complete JSON runs under `runs/` and rendered
to the console. Slack notification is enabled only when
`SLACK_WEBHOOK_URL` is set in `.env`.

## Development

```
uv run pytest tests/unit -vv
uv run ruff check src tests scripts
uv run mypy src
```
