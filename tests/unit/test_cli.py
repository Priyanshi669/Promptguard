"""Unit tests for the command-line interface."""

from unittest.mock import AsyncMock, patch

import pytest

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


def golden_case(case_id: str) -> GoldenTestCase:
    """Build a minimal golden case for limit tests."""
    return GoldenTestCase(
        id=case_id,
        input="input",
        expected_category="billing",
        ideal_summary="ideal",
        expected_difficulty="easy",
    )


def test_apply_limit_slices_first_n_cases() -> None:
    """A positive limit should keep only the first N cases."""
    cases = [golden_case(f"case-{index}") for index in range(5)]
    limited = cli._apply_limit(cases, 3)
    assert [case.id for case in limited] == ["case-0", "case-1", "case-2"]


def test_apply_limit_without_limit_keeps_all_cases() -> None:
    """The default run should keep the complete dataset."""
    cases = [golden_case("case-0"), golden_case("case-1")]
    assert cli._apply_limit(cases, None) == cases


def test_apply_limit_rejects_negative_values() -> None:
    """A negative limit is a configuration error."""
    with pytest.raises(ValueError, match="non-negative"):
        cli._apply_limit([golden_case("case-0")], -1)


def test_apply_limit_supports_empty_smoke_runs() -> None:
    """A zero limit should produce an explicitly empty run."""
    assert cli._apply_limit([golden_case("case-0")], 0) == []