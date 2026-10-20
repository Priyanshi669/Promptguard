"""DeepEval-based evaluation metrics."""

from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams

from promptguard.domain.metrics import SummaryMetric
from promptguard.infrastructure.evaluation.nvidia_judge import NVIDIAJudge


class DeepEvalSummaryMetric(SummaryMetric):
    """Evaluate summary quality using DeepEval G-Eval."""

    def __init__(self, model: str | None = None) -> None:
        """Initialize the DeepEval metric."""
        self._metric = GEval(
            name="Summary Quality",
            criteria=(
                "Evaluate whether the actual summary accurately captures "
                "the meaning and important information in the expected "
                "summary. Penalize factual contradictions, invented "
                "information, and omission of important information. "
                "Allow reasonable paraphrasing and differences in wording."
            ),
            evaluation_params=[
                SingleTurnParams.EXPECTED_OUTPUT,
                SingleTurnParams.ACTUAL_OUTPUT,
            ],
            model=NVIDIAJudge(model=model),
            threshold=0.5,
        )

    async def score(
        self,
        expected_summary: str,
        actual_summary: str,
    ) -> float:
        """Return a normalized 1-5 summary score."""
        test_case = LLMTestCase(
            input="Evaluate this customer support summary.",
            expected_output=expected_summary,
            actual_output=actual_summary,
        )

        await self._metric.a_measure(test_case)

        if self._metric.score is None:
            raise ValueError("DeepEval returned no summary score.")

        return self._normalize_score(self._metric.score)

    @staticmethod
    def _normalize_score(score: float) -> float:
        """Convert DeepEval's 0-1 score to the project's 1-5 scale."""
        bounded_score = max(0.0, min(1.0, score))
        return 1.0 + (4.0 * bounded_score)
