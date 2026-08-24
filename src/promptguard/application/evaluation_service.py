"""Service for running evaluations."""
from promptguard.domain.models import (
    EvaluationResult,
    GoldenTestCase,
)
from promptguard.infrastructure.llm.client import (
    LLMClient,
)


class EvaluationService:

    def __init__(
        self,
        llm_client: LLMClient,
    ):
        self.llm_client = llm_client

    async def evaluate_case(
        self,
        test_case: GoldenTestCase,
        system_prompt: str,
    ) -> EvaluationResult:

        response = await self.llm_client.generate(
            system_prompt=system_prompt,
            user_input=test_case.input,
        )

        category_match = (
            response.output.category.lower()
            == test_case.expected_category.lower()
        )

        return EvaluationResult(
            test_case_id=test_case.id,
            expected_category=test_case.expected_category,
            actual_category=response.output.category,
            expected_summary=test_case.ideal_summary,
            actual_summary=response.output.summary,
            category_match=category_match,
            summary_score=1.0,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )