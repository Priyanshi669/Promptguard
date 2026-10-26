"""Unit tests for the calibration command-line interface."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_rubric_sha256,
)
from promptguard.interfaces import calibration_cli


class FakeSummaryMetric:
    """Calibration CLI fake that returns the score encoded in the summary."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Accept the real metric's constructor arguments."""

    async def score(
        self,
        expected_summary: str,
        actual_summary: str,
    ) -> float:
        """Return the judge score encoded in the actual summary."""
        return float(actual_summary)


@pytest.fixture
def calibration_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace provider settings so calibration runs offline."""
    settings = SimpleNamespace(
        nvidia_model="judge-model",
        nvidia_base_url="https://integrate.api.nvidia.com/v1",
        evaluation_timeout_seconds=30.0,
    )
    monkeypatch.setattr(calibration_cli, "get_settings", lambda: settings)
    monkeypatch.setattr(
        calibration_cli,
        "DeepEvalSummaryMetric",
        FakeSummaryMetric,
    )


def write_examples(
    path: Path,
    human_scores: list[int],
    judge_scores: list[int] | None = None,
) -> None:
    """Write a small calibration file with explicit human and judge scores."""
    selected_judge_scores = judge_scores or human_scores
    examples = [
        {
            "id": f"case-{index}",
            "expected_summary": "Expected summary",
            "actual_summary": str(judge_score),
            "human_score": human_score,
        }
        for index, (human_score, judge_score) in enumerate(
            zip(human_scores, selected_judge_scores, strict=True)
        )
    ]
    payload = {
        "metadata": {
            "version": "test-v1",
            "rubric_version": SUMMARY_QUALITY_RUBRIC_VERSION,
            "rubric_sha256": summary_quality_rubric_sha256(),
        },
        "examples": examples,
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_calibration_cli_returns_zero_when_agreement_passes(
    tmp_path: Path,
    calibration_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Perfect agreement should print an auditable report and exit 0."""
    path = tmp_path / "calibration.json"
    write_examples(path, [3, 4, 5])

    exit_code = await calibration_cli._run(
        [
            "--file",
            str(path),
            "--min-samples",
            "3",
            "--max-mae",
            "0",
            "--min-pearson",
            "1",
            "--min-within-one",
            "1",
            "--max-absolute-bias",
            "0",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert report["passed"] is True
    assert report["evaluator"]["judge_model"] == "judge-model"
    assert report["dataset"]["version"] == "test-v1"
    assert report["dataset"]["sample_count"] == 3
    assert report["metrics"]["mean_absolute_error"] == 0.0


@pytest.mark.asyncio
async def test_calibration_cli_returns_one_when_agreement_fails(
    tmp_path: Path,
    calibration_settings: None,
) -> None:
    """A judge that disagrees with human labels must fail the calibration."""
    path = tmp_path / "calibration.json"
    write_examples(path, [5, 5, 5], [3, 3, 3])

    exit_code = await calibration_cli._run(
        [
            "--file",
            str(path),
            "--min-samples",
            "3",
            "--max-mae",
            "0",
            "--min-pearson",
            "1",
            "--min-within-one",
            "1",
            "--max-absolute-bias",
            "0",
        ]
    )

    assert exit_code == 1


@pytest.mark.asyncio
async def test_calibration_cli_writes_output_file(
    tmp_path: Path,
    calibration_settings: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The report can be persisted instead of printed in full."""
    path = tmp_path / "calibration.json"
    output = tmp_path / "report.json"
    write_examples(path, [3, 4, 5])

    exit_code = await calibration_cli._run(
        [
            "--file",
            str(path),
            "--output",
            str(output),
            "--min-samples",
            "3",
            "--max-mae",
            "0",
            "--min-pearson",
            "1",
            "--min-within-one",
            "1",
            "--max-absolute-bias",
            "0",
        ]
    )

    assert exit_code == 0
    assert output.is_file()
    assert "Calibration report written" in capsys.readouterr().out
    assert json.loads(output.read_text(encoding="utf-8"))["passed"] is True
