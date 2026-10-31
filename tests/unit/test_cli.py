"""Unit tests for the command-line interface."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APITimeoutError
from pydantic import ValidationError

from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import GoldenTestCase
from promptguard.interfaces import cli


def test_cli_import_exposes_project_root() -> None:
    """Importing the CLI should not make an external API call."""
    assert (cli.PROJECT_ROOT / "pyproject.toml").is_file()


def test_run_delegates_to_async_entrypoint() -> None:
    """The synchronous CLI wrapper should delegate to its async entrypoint."""
    async_entrypoint = AsyncMock(return_value=0)

    with (
        patch.object(cli, "_run", async_entrypoint),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli.run()

    assert excinfo.value.code == 0
    async_entrypoint.assert_awaited_once_with()

def test_run_reports_configuration_errors() -> None:
    """Data or configuration errors should exit with a distinct code."""

    def raise_configuration_error(coro):
        coro.close()
        raise FileNotFoundError("dataset missing")

    with (
        patch.object(
            cli.asyncio,
            "run",
            side_effect=raise_configuration_error,
        ),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli.run()

    assert excinfo.value.code == 2

def test_run_reports_provider_errors_as_configuration_exit() -> None:
    """Unhandled provider failures must not collide with a gate FAIL exit."""

    def raise_provider_error(coro):
        coro.close()
        raise ConnectionError("provider unavailable")

    with (
        patch.object(
            cli.asyncio,
            "run",
            side_effect=raise_provider_error,
        ),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli.run()

    assert excinfo.value.code == 2


def test_run_prints_actionable_provider_error(
    capsys: pytest.CaptureFixture,
) -> None:
    """Provider failures should print remediation guidance, not raw text."""

    def raise_timeout_error(coro):
        coro.close()
        request = httpx.Request("POST", "https://example.com/v1")
        raise APITimeoutError(request=request)

    settings = MagicMock()
    settings.nvidia_api_key = "test-api-key"

    with (
        patch.object(
            cli.asyncio,
            "run",
            side_effect=raise_timeout_error,
        ),
        patch.object(cli, "get_settings", return_value=settings),
        pytest.raises(SystemExit) as excinfo,
    ):
        cli.run()

    captured = capsys.readouterr()
    assert "PromptGuard failed:" in captured.out
    assert "NVIDIA_REQUEST_TIMEOUT_SECONDS" in captured.out
    assert excinfo.value.code == 2


@pytest.mark.asyncio
async def test_run_enforces_evaluation_deadline() -> None:
    """A stuck evaluation must not outlive the configured run deadline."""
    async def slow_run(**kwargs):
        await asyncio.sleep(1)
        raise AssertionError("deadline did not cancel the run")

    settings = MagicMock()
    settings.evaluation_timeout_seconds = 0.01
    settings.nvidia_model = "test-model"
    settings.bootstrap_seed = 20261026
    settings.bootstrap_resamples = 10
    settings.bootstrap_confidence_level = 0.95
    service = SimpleNamespace(run=slow_run)

    with (
        patch.object(
            cli,
            "_build_run_service",
            return_value=(service, "system", [], {}),
        ),
        patch.object(cli, "_build_notifier", return_value=MagicMock()),
        patch.object(cli, "get_settings", return_value=settings),
        pytest.raises(RuntimeError, match="run timeout"),
    ):
        await cli._run(["--prompt-version", "v1"])


@pytest.mark.asyncio
async def test_run_rejects_invalid_prompt_version_before_setup() -> None:
    """Prompt versions must not traverse outside the dataset directory."""
    with pytest.raises(ValueError, match="prompt version"):
        await cli._run(["--prompt-version", "../outside"])


@pytest.mark.asyncio
async def test_run_rejects_invalid_baseline_before_setup() -> None:
    """Baseline IDs must not traverse outside the runs directory."""
    with pytest.raises(ValueError, match="baseline run ID"):
        await cli._run(["--baseline", "../outside"])


def test_build_run_service_rejects_invalid_prompt_version() -> None:
    """The service builder must reject unsafe prompt versions directly."""
    with pytest.raises(ValueError, match="prompt version"):
        cli._build_run_service(
            prompt_version="../outside",
            runs_dir=MagicMock(),
            notifier=MagicMock(),
        )


def test_parse_args_defaults() -> None:
    """Defaults should keep the basic CLI path free of thresholds."""
    args = cli._parse_args([])
    assert args.prompt_version == "v1"
    assert args.baseline is None
    assert cli._build_thresholds(args) is None


def test_parse_args_builds_thresholds() -> None:
    """Threshold flags should map onto the domain configuration."""
    args = cli._parse_args(
        [
            "--min-category-accuracy",
            "0.8",
            "--min-summary-score",
            "3.5",
            "--max-latency-ms",
            "1500",
            "--baseline",
            "latest",
        ]
    )

    thresholds = cli._build_thresholds(args)

    assert thresholds is not None
    assert thresholds.minimum_category_accuracy == 0.8
    assert thresholds.minimum_summary_score == 3.5
    assert thresholds.maximum_average_latency_ms == 1500
    assert args.baseline == "latest"


def test_build_thresholds_rejects_infinite_latency() -> None:
    """An infinite latency threshold must not disable the latency gate."""
    args = cli._parse_args(["--max-latency-ms", "inf"])

    with pytest.raises(ValidationError):
        cli._build_thresholds(args)


def test_parse_args_builds_drop_thresholds() -> None:
    """Absolute and percentage drop flags should map onto the configuration."""
    args = cli._parse_args(
        [
            "--max-category-accuracy-drop",
            "0.05",
            "--max-summary-score-drop",
            "0.4",
            "--max-category-accuracy-drop-pct",
            "10",
            "--max-summary-score-drop-pct",
            "15",
        ]
    )

    thresholds = cli._build_thresholds(args)

    assert thresholds is not None
    assert thresholds.maximum_category_accuracy_drop == 0.05
    assert thresholds.maximum_summary_score_drop == 0.4
    assert thresholds.maximum_category_accuracy_drop_pct == 10
    assert thresholds.maximum_summary_score_drop_pct == 15


def golden_case(case_id: str) -> GoldenTestCase:
    """Build a minimal golden case for limit tests."""
    return GoldenTestCase(
        id=case_id,
        input="input",
        expected_category="billing",
        ideal_summary="ideal",
        expected_difficulty="easy",
    )


def test_exit_code_without_regression_is_inconclusive() -> None:
    """A completed report without a gate decision must exit 2."""
    report = ReportingService().build_report([], "v1", "m", "d")
    assert cli._exit_code_for(report) == 2


def test_apply_limit_slices_first_n_cases() -> None:
    """A positive limit should keep only the first N cases."""
    cases = [golden_case(f"case-{index}") for index in range(5)]
    limited = cli._apply_limit(cases, 3)
    assert [case.id for case in limited] == ["case-0", "case-1", "case-2"]


def test_apply_limit_without_limit_keeps_all_cases() -> None:
    """The default run should keep the complete dataset."""
    cases = [golden_case("case-0"), golden_case("case-1")]
    assert cli._apply_limit(cases, None) == cases


def test_apply_limit_rejects_non_positive_values() -> None:
    """Zero and negative limits are configuration errors."""
    with pytest.raises(ValueError, match="at least 1"):
        cli._apply_limit([golden_case("case-0")], 0)
    with pytest.raises(ValueError, match="at least 1"):
        cli._apply_limit([golden_case("case-0")], -1)