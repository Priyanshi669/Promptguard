"""Command-line entrypoint for judge calibration."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from promptguard.application.calibration_service import JudgeCalibrationService
from promptguard.config import get_settings
from promptguard.domain.calibration import CalibrationThresholds
from promptguard.infrastructure.calibration.json_repository import (
    load_calibration_dataset,
)
from promptguard.infrastructure.evaluation.deepeval_metrics import (
    DeepEvalSummaryMetric,
)
from promptguard.infrastructure.evaluation.evaluator_config import (
    build_evaluator_config,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse calibration CLI arguments."""
    parser = argparse.ArgumentParser(
        prog="promptguard-calibrate",
        description="Measure summary-judge agreement with human labels.",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="JSON file containing human-labelled calibration examples.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write the calibration report to this path instead of stdout.",
    )
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--max-mae", type=float, default=0.75)
    parser.add_argument("--min-pearson", type=float, default=0.6)
    parser.add_argument("--min-within-one", type=float, default=0.8)
    parser.add_argument("--max-absolute-bias", type=float, default=0.5)
    return parser.parse_args(argv)


def _build_thresholds(args: argparse.Namespace) -> CalibrationThresholds:
    """Build calibration acceptance limits from CLI flags."""
    return CalibrationThresholds(
        minimum_samples=args.min_samples,
        maximum_mae=args.max_mae,
        minimum_pearson=args.min_pearson,
        minimum_within_one_agreement=args.min_within_one,
        maximum_absolute_bias=args.max_absolute_bias,
    )


async def _run(argv: list[str] | None = None) -> int:
    """Run calibration and return a distinct process exit code."""
    args = _parse_args(argv)
    settings = get_settings()
    dataset = load_calibration_dataset(args.file)
    evaluator = build_evaluator_config(settings)

    try:
        async with asyncio.timeout(settings.evaluation_timeout_seconds):
            report = await JudgeCalibrationService().calibrate(
                dataset=dataset,
                summary_metric=DeepEvalSummaryMetric(),
                evaluator=evaluator,
                thresholds=_build_thresholds(args),
            )
    except TimeoutError as exc:
        raise RuntimeError(
            "Calibration exceeded the configured run timeout "
            f"({settings.evaluation_timeout_seconds:g}s)."
        ) from exc

    report_json = json.dumps(
        report.model_dump(mode="json"),
        indent=2,
        sort_keys=True,
    )
    if args.output is not None:
        output_path = Path(args.output)
        output_path.write_text(report_json + "\n", encoding="utf-8")
        print(f"Calibration report written to {output_path}")
    else:
        print(report_json)

    return 0 if report.passed else 1


def _configure_console_output() -> None:
    """Keep console printing alive for characters the console cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def run() -> None:
    """Run the calibration CLI."""
    _configure_console_output()
    try:
        exit_code = asyncio.run(_run())
    except Exception as exc:
        print(f"PromptGuard calibration failed: {exc}")
        raise SystemExit(2) from exc
    raise SystemExit(exit_code)
