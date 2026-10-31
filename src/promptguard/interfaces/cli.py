"""Command-line interface for PromptGuard."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from promptguard.application.contracts import Notifier
from promptguard.application.run_service import EvaluationRunService
from promptguard.application.wiring import build_default_run_service, build_notifier
from promptguard.config import get_settings
from promptguard.domain.models import EvaluationReport, GoldenTestCase
from promptguard.domain.statistics import BootstrapConfig
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.infrastructure.evaluation.deepeval_metrics import DeepEvalSummaryMetric
from promptguard.infrastructure.llm.client import NVIDIAClient
from promptguard.infrastructure.llm.errors import describe_provider_error
from promptguard.infrastructure.path_safety import validate_identifier

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
    parser.add_argument(
        "--max-category-drift", type=float, default=None,
        help=(
            "Slack-alert when category-distribution drift (total "
            "variation distance to the baseline) exceeds this 0-1 value."
        ),
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
        and args.max_category_drift is None
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
        maximum_category_drift=args.max_category_drift,
    )


def _build_notifier() -> Notifier:
    """Return the settings-composed notifier (Slack when configured)."""
    return build_notifier(get_settings())


def _apply_limit(
    test_cases: list[GoldenTestCase], limit: int | None,
) -> list[GoldenTestCase]:
    """Return the first N cases when a limit is configured."""
    if limit is None:
        return test_cases
    if limit <= 0:
        raise ValueError("--limit must be at least 1.")
    return test_cases[:limit]


def _build_run_service(
    prompt_version: str,
    runs_dir: Path,
    notifier: Notifier,
) -> tuple[EvaluationRunService, str, list, dict[str, Any]]:
    """Wire the orchestrator through the shared composition point."""
    return build_default_run_service(
        prompt_version=prompt_version,
        runs_dir=runs_dir,
        notifier=notifier,
        settings=get_settings(),
        llm_client=NVIDIAClient(),
        summary_metric=DeepEvalSummaryMetric(),
    )


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
                bootstrap=BootstrapConfig(
                    seed=settings.bootstrap_seed,
                    resamples=settings.bootstrap_resamples,
                    confidence_level=settings.bootstrap_confidence_level,
                ),
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
        api_key = getattr(get_settings(), "nvidia_api_key", None)
        print(f"PromptGuard failed: {describe_provider_error(exc, api_key)}")
        raise SystemExit(2) from exc
    raise SystemExit(exit_code)