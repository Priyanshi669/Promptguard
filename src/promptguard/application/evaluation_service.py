"""Application service for evaluating LLM responses."""

from promptguard.domain.models import (
    EvaluationResult,
    GoldenTestCase,
)
from promptguard.infrastructure.llm.contract import LLMClient


class EvaluationService:
    """Evaluate an LLM against a golden test case."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm_client = llm_client

    async def evaluate(
        self,
        test_case: GoldenTestCase,
        system_prompt: str,
    ) -> EvaluationResult:
        """Run one golden test case against the configured LLM."""

        response = await self._llm_client.generate(
            system_prompt=system_prompt,
            user_input=test_case.input,
        )

        actual_output = response.output

        category_match = (
            actual_output.category.strip().lower()
            == test_case.expected_category.strip().lower()
        )

        summary_score = self._score_summary(
            expected=test_case.ideal_summary,
            actual=actual_output.summary,
        )

        return EvaluationResult(
            test_case_id=test_case.id,
            expected_category=test_case.expected_category,
            actual_category=actual_output.category,
            expected_summary=test_case.ideal_summary,
            actual_summary=actual_output.summary,
            category_match=category_match,
            summary_score=summary_score,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    @staticmethod
    def _score_summary(
        expected: str,
        actual: str,
    ) -> float:
        """Return a basic deterministic summary similarity score."""

        expected_words = set(expected.lower().split())
        actual_words = set(actual.lower().split())

        if not expected_words or not actual_words:
            return 1.0

        overlap = len(expected_words & actual_words)
        score = 1.0 + (4.0 * overlap / len(expected_words))

        return min(5.0, score)