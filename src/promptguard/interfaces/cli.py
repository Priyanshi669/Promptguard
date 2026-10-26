"""Command-line interface for PromptGuard."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.contracts import (
    EvaluationRunRepository,
    Notifier,
    ReportFormatter,
)
from promptguard.application.drift_service import DriftService
from promptguard.application.evaluation_service import EvaluationService
from promptguard.application.reporting_service import ReportingService
from promptguard.application.run_service import EvaluationRunService
from promptguard.config import get_settings
from promptguard.domain.models import EvaluationReport, GoldenTestCase
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.infrastructure.dataset.json_repository import JsonDatasetRepository
from promptguard.infrastructure.evaluation.deepeval_metrics import DeepEvalSummaryMetric
from promptguard.infrastructure.evaluation.evaluator_config import (
    build_evaluator_config,
)
from promptguard.infrastructure.llm.client import NVIDIAClient
from promptguard.infrastructure.notifications.slack import NoopNotifier, SlackNotifier
from promptguard.infrastructure.path_safety import validate_identifier
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.infrastructure.prompts.yaml_repository import PromptRepository
from promptguard.infrastructure.reporting.html_renderer import TextReportFormatter

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_PROMPT_VERSION = "v1"
LATEST_BASELINE = "latest"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(prog="promptguard")
    parser.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION)
    parser.add_argument(
        "--baseline",
        default=None,
        help="Baseline run ID, or 'latest' for the most recent persisted run.",
    )
    parser.add_argument("--runs-dir", default=str(PROJECT_ROOT / "runs"))
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N cases (N must be at least 1).",
    )
    parser.add_argument(
        "--min-category-accuracy", type=float, default=None,
        help="Fail the run when category accuracy falls below this value.",
    )
    parser.add_argument(
        "--min-summary-score", type=float, default=None,
        help="Fail the run when average summary score falls below this value.",
    )
    parser.add_argument(
        "--max-latency-ms", type=float, default=None,
        help="Fail the run when average latency exceeds this value.",
    )
    parser.add_argument(
        "--max-category-accuracy-drop", type=float, default=None,
        help="Fail when category accuracy drops by more than this absolute amount.",
    )
    parser.add_argument(
        "--max-summary-score-drop", type=float, default=None,
        help="Fail when average summary score drops by more than this absolute amount.",
    )
    parser.add_argument(
        "--max-category-accuracy-drop-pct", type=float, default=None,
        help="Fail when category accuracy drops by more than this percentage.",
    )
    parser.add_argument(
        "--max-summary-score-drop-pct", type=float, default=None,
        help="Fail when average summary score drops by more than this percentage.",
    )
    return parser.parse_args(argv)


def _build_thresholds(args: argparse.Namespace) -> RegressionThresholds | None:
    """Build regression thresholds from CLI flags, if any are set."""
    if (
        args.min_category_accuracy is None
        and args.min_summary_score is None
        and args.max_latency_ms is None
        and args.max_category_accuracy_drop is None
        and args.max_summary_score_drop is None
        and args.max_category_accuracy_drop_pct is None
        and args.max_summary_score_drop_pct is None
    ):
        return None
    return RegressionThresholds(
        minimum_category_accuracy=args.min_category_accuracy,
        minimum_summary_score=args.min_summary_score,
        maximum_average_latency_ms=args.max_latency_ms,
        maximum_category_accuracy_drop=args.max_category_accuracy_drop,
        maximum_summary_score_drop=args.max_summary_score_drop,
        maximum_category_accuracy_drop_pct=(
            args.max_category_accuracy_drop_pct
        ),
        maximum_summary_score_drop_pct=args.max_summary_score_drop_pct,
    )


def _build_notifier() -> Notifier:
    """Return Slack notifications when configured, else a safe default."""
    settings = get_settings()
    if settings.slack_webhook_url:
        return SlackNotifier(settings.slack_webhook_url)
    return NoopNotifier()


def _apply_limit(
    test_cases: list[GoldenTestCase], limit: int | None,
) -> list[GoldenTestCase]:
    """Return the first N cases when a limit is configured."""
    if limit is None:
        return test_cases
    if limit <= 0:
        raise ValueError("--limit must be at least 1.")
    return test_cases[:limit]


def _extract_system_prompt(prompt_config: dict) -> str:
    """Extract the system prompt from the prompt configuration."""
    system_prompt = prompt_config.get("system_prompt")

    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError(
            "Prompt configuration must contain a non-empty 'system_prompt'."
        )

    return system_prompt


def _extract_categories(prompt_config: dict) -> list[str]:
    """Extract the allowed category taxonomy from prompt config, if present."""
    categories = prompt_config.get("categories", [])
    if not isinstance(categories, list):
        return []
    return [str(c) for c in categories]


def _extract_aliases(prompt_config: dict) -> dict[str, str]:
    """Extract category aliases from prompt config, if present."""
    aliases = prompt_config.get("category_aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {str(k): str(v) for k, v in aliases.items()}


def _build_run_service(
    prompt_version: str,
    runs_dir: Path,
    notifier: Notifier,
) -> tuple[EvaluationRunService, str, list, dict[str, Any]]:
    """Wire the orchestrator with concrete infrastructure adapters."""
    validate_identifier(prompt_version, kind="prompt version")
    dataset_path = PROJECT_ROOT / "dataset" / prompt_version / "golden_dataset.json"
    prompts_dir = PROJECT_ROOT / "prompts"
    settings = get_settings()

    test_cases = JsonDatasetRepository(dataset_path).load()
    prompt_config = PromptRepository(prompts_dir).load(prompt_version)
    system_prompt = _extract_system_prompt(prompt_config)
    categories = _extract_categories(prompt_config)
    aliases = _extract_aliases(prompt_config)
    eval_config: dict[str, Any] = {
        "categories": categories,
        "category_aliases": aliases,
        "temperature": settings.nvidia_temperature,
        "seed": settings.nvidia_seed,
        "evaluator": build_evaluator_config(settings),
    }

    evaluation_service = EvaluationService(
        llm_client=NVIDIAClient(),
        summary_metric=DeepEvalSummaryMetric(),
        allowed_categories=categories,
        category_aliases=aliases,
    )

    repository: EvaluationRunRepository = JsonEvaluationRunRepository(runs_dir)
    formatter: ReportFormatter = TextReportFormatter()

    run_service = EvaluationRunService(
        evaluation_service=evaluation_service,
        reporting_service=ReportingService(),
        comparison_service=ComparisonService(),
        regression_service=RegressionService(),
        drift_service=DriftService(),
        repository=repository,
        formatter=formatter,
        notifier=notifier,
    )
    return run_service, system_prompt, test_cases, eval_config


async def _run(argv: list[str] | None = None) -> int:
    """Run the evaluation pipeline and return the process exit code."""
    args = _parse_args(argv)
    validate_identifier(args.prompt_version, kind="prompt version")
    if args.baseline is not None:
        validate_identifier(args.baseline, kind="baseline run ID")
    settings = get_settings()

    run_service, system_prompt, test_cases, eval_config = _build_run_service(
        prompt_version=args.prompt_version,
        runs_dir=Path(args.runs_dir),
        notifier=_build_notifier(),
    )
    test_cases = _apply_limit(test_cases, args.limit)

    try:
        async with asyncio.timeout(settings.evaluation_timeout_seconds):
            outcome = await run_service.run(
                test_cases=test_cases,
                system_prompt=system_prompt,
                prompt_version=args.prompt_version,
                model=settings.nvidia_model,
                dataset=f"golden-{args.prompt_version}",
                thresholds=_build_thresholds(args),
                baseline_run_id=args.baseline,
                eval_config=eval_config,
            )
    except TimeoutError as exc:
        raise RuntimeError(
            "Evaluation exceeded the configured run timeout "
            f"({settings.evaluation_timeout_seconds:g}s)."
        ) from exc

    if outcome.report_text is not None:
        print(outcome.report_text)

    if outcome.notification_error is not None:
        print(f"Warning: {outcome.notification_error}", file=sys.stderr)

    if outcome.skipped_runs:
        print(
            "Warning: persisted evaluation run files were skipped:",
            file=sys.stderr,
        )
        for diagnostic in outcome.skipped_runs:
            print(f"  {diagnostic}", file=sys.stderr)

    return _exit_code_for(outcome.report)


def _exit_code_for(report: EvaluationReport) -> int:
    """Map a completed report to a distinct process exit code."""
    if report.regression is None:
        return 2
    status = report.regression.status
    if status == "PASS":
        return 0
    if status == "FAIL":
        return 1
    return 2


def _configure_console_output() -> None:
    """Keep console printing alive for characters the console cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def run() -> None:
    """Run the CLI."""
    _configure_console_output()
    try:
        exit_code = asyncio.run(_run())
    except Exception as exc:
        print(f"PromptGuard failed: {exc}")
        raise SystemExit(2) from exc
    raise SystemExit(exit_code)