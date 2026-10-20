"""Unit tests for DeepEval summary scoring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from promptguard.infrastructure.evaluation.deepeval_metrics import (
    DeepEvalSummaryMetric,
)


@pytest.mark.parametrize(
    ("raw_score", "expected_score"),
    [
        (-0.5, 1.0),
        (0.0, 1.0),
        (0.5, 3.0),
        (1.0, 5.0),
        (2.0, 5.0),
    ],
)
def test_normalize_score_bounds_to_domain_range(
    raw_score: float,
    expected_score: float,
) -> None:
    """DeepEval's score should be converted and bounded to the 1-5 domain."""
    assert DeepEvalSummaryMetric._normalize_score(raw_score) == expected_score


@pytest.mark.asyncio
async def test_score_uses_deepeval_measurement() -> None:
    """Summary scoring should pass expected and actual text to G-Eval."""
    metric = MagicMock()
    metric.score = 0.75

    async def measure(test_case) -> None:
        assert test_case.expected_output == "Expected summary"
        assert test_case.actual_output == "Actual summary"

    metric.a_measure = AsyncMock(side_effect=measure)

    with (
        patch(
            "promptguard.infrastructure.evaluation.deepeval_metrics.NVIDIAJudge",
            return_value=MagicMock(),
        ),
        patch(
            "promptguard.infrastructure.evaluation.deepeval_metrics.GEval",
            return_value=metric,
        ),
    ):
        summary_metric = DeepEvalSummaryMetric()

    score = await summary_metric.score(
        "Expected summary",
        "Actual summary",
    )

    assert score == 4.0
    metric.a_measure.assert_awaited_once()