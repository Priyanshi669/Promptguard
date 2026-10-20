"""Application service for evaluating LLM responses."""

from promptguard.application.contracts import LLMClient
from promptguard.domain.metrics import SummaryMetric
from promptguard.domain.models import (
    EvaluationResult,
    GoldenTestCase,
)
from promptguard.domain.normalization import CategoryNormalizer


class EvaluationService:
    """Evaluate an LLM against golden test cases."""

    def __init__(
        self,
        llm_client: LLMClient,
        summary_metric: SummaryMetric,
        allowed_categories: set[str] | list[str] | None = None,
        category_aliases: dict[str, str] | None = None,
    ) -> None:
        """Initialize the evaluation service.

        Args:
            llm_client: LLM client for generating responses.
            summary_metric: Metric for scoring summaries.
            allowed_categories: Canonical category slugs for normalization.
            category_aliases: Optional alias mapping -> canonical slug.
        """
        self._llm_client = llm_client
        self._summary_metric = summary_metric
        self._normalizer = CategoryNormalizer(
            allowed_categories=allowed_categories or set(),
            aliases=category_aliases,
        )

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

        normalized_category, normalization_applied = self._normalizer.normalize(
            actual_output.category
        )

        category_match = (
            normalized_category.strip().lower()
            == test_case.expected_category.strip().lower()
        )

        summary_score = await self._summary_metric.score(
            expected_summary=test_case.ideal_summary,
            actual_summary=actual_output.summary,
        )

        return EvaluationResult(
            test_case_id=test_case.id,
            expected_category=test_case.expected_category,
            actual_category=actual_output.category,
            normalized_category=normalized_category,
            normalization_applied=normalization_applied,
            expected_summary=test_case.ideal_summary,
            actual_summary=actual_output.summary,
            category_match=category_match,
            summary_score=summary_score,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )

    async def evaluate_dataset(
        self,
        test_cases: list[GoldenTestCase],
        system_prompt: str,
    ) -> list[EvaluationResult]:
        """Evaluate all golden test cases."""
        results: list[EvaluationResult] = []

        for test_case in test_cases:
            result = await self.evaluate(
                test_case=test_case,
                system_prompt=system_prompt,
            )
            results.append(result)

        return results