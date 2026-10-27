"""End-to-end CLI integration tests with deterministic provider fakes."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from promptguard.domain.models import GoldenTestCase, LLMOutput, LLMResponse
from promptguard.interfaces import cli


class FakeLLMClient:
    """CLI-facing fake that returns scripted outputs by user input."""

    responses: ClassVar[dict[str, LLMOutput]] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Accept the real client's constructor arguments."""

    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """Return the configured output for the given input."""
        return LLMResponse(
            output=self.responses[user_input],
            latency_ms=10.0,
            input_tokens=12,
            output_tokens=6,
        )


class FakeSummaryMetric:
    """CLI-facing fake summary metric with a fixed score."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Accept the real metric's constructor arguments."""

    async def score(
        self,
        expected_summary: str,
        actual_summary: str,
    ) -> float:
        """Return a passing score."""
        return 4.0


def script(
    cases: list[GoldenTestCase],
    overrides: dict[str, str] | None = None,
) -> dict[str, LLMOutput]:
    """Build deterministic outputs for the selected cases."""
    overrides = overrides or {}
    return {
        case.input: LLMOutput(
            category=overrides.get(case.id, case.expected_category),
            summary=case.ideal_summary,
        )
        for case in cases
    }


@pytest.fixture
def cli_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace providers and settings so the CLI runs without network access."""
    settings = SimpleNamespace(
        nvidia_model="test-model",
        nvidia_request_timeout_seconds=10.0,
        evaluation_timeout_seconds=900.0,
        nvidia_temperature=0.0,
        nvidia_seed=7,
        slack_webhook_url=None,
        bootstrap_seed=20261026,
        bootstrap_resamples=500,
        bootstrap_confidence_level=0.95,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "NVIDIAClient", FakeLLMClient)
    monkeypatch.setattr(cli, "DeepEvalSummaryMetric", FakeSummaryMetric)


@pytest.mark.asyncio
async def test_cli_returns_zero_when_gate_passes(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    cli_environment: None,
) -> None:
    """A passing evaluation should exit 0 and persist one run."""
    FakeLLMClient.responses = script(golden_cases[:3])

    exit_code = await cli._run(
        [
            "--limit",
            "3",
            "--runs-dir",
            str(tmp_path),
            "--min-category-accuracy",
            "1.0",
            "--min-summary-score",
            "4.0",
        ]
    )

    assert exit_code == 0
    runs = list(tmp_path.glob("*.json"))
    assert len(runs) == 1
    report = json.loads(runs[0].read_text(encoding="utf-8"))
    assert report["evaluator"]["judge_model"] == "test-model"
    assert report["evaluator"]["judge_rubric_sha256"]


@pytest.mark.asyncio
async def test_cli_returns_one_when_gate_fails(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    cli_environment: None,
) -> None:
    """A failed threshold should exit 1."""
    FakeLLMClient.responses = script(
        golden_cases[:3],
        {case.id: "technical" for case in golden_cases[:3]},
    )

    exit_code = await cli._run(
        [
            "--limit",
            "3",
            "--runs-dir",
            str(tmp_path),
            "--min-category-accuracy",
            "1.0",
        ]
    )

    assert exit_code == 1


@pytest.mark.asyncio
async def test_cli_returns_two_without_a_gate(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    cli_environment: None,
) -> None:
    """No thresholds and no baseline should be INCONCLUSIVE (exit 2)."""
    FakeLLMClient.responses = script(golden_cases[:2])

    exit_code = await cli._run(
        ["--limit", "2", "--runs-dir", str(tmp_path)],
    )

    assert exit_code == 2


@pytest.mark.asyncio
async def test_cli_latest_baseline_fails_on_corrupted_run(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    cli_environment: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A corrupted latest run must fail clearly and surface diagnostics."""
    FakeLLMClient.responses = script(golden_cases[:2])
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

    exit_code = await cli._run(
        [
            "--limit",
            "2",
            "--runs-dir",
            str(tmp_path),
            "--baseline",
            "latest",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "persisted evaluation run files were skipped" in captured.err
    assert "bad.json" in captured.err
