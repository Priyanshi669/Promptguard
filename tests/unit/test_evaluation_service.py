"""Unit tests for the evaluation service."""

from unittest.mock import AsyncMock

import pytest

from promptguard.application.evaluation_service import EvaluationService
from promptguard.domain.models import (
    GoldenTestCase,
    LLMOutput,
    LLMResponse,
)


@pytest.fixture
def golden_test_case() -> GoldenTestCase:
    """Return a sample golden test case."""

    return GoldenTestCase(
        id="case-001",
        input="I was charged twice for the same order.",
        expected_category="billing",
        ideal_summary="Customer reports a duplicate charge.",
        expected_difficulty="easy",
    )


@pytest.fixture
def mock_llm_client() -> AsyncMock:
    """Return a mocked LLM client."""

    client = AsyncMock()

    client.generate.return_value = LLMResponse(
        output=LLMOutput(
            category="billing",
            summary="Customer reports a duplicate charge.",
        ),
        latency_ms=25.0,
        input_tokens=20,
        output_tokens=10,
    )

    return client


@pytest.mark.asyncio
async def test_evaluate_returns_evaluation_result(
    golden_test_case: GoldenTestCase,
    mock_llm_client: AsyncMock,
) -> None:
    """Service should convert an LLM response into an evaluation result."""

    service = EvaluationService(mock_llm_client)

    result = await service.evaluate(
        test_case=golden_test_case,
        system_prompt="Classify the customer message.",
    )

    assert result.test_case_id == "case-001"
    assert result.expected_category == "billing"
    assert result.actual_category == "billing"
    assert result.category_match is True

    assert (
        result.expected_summary
        == "Customer reports a duplicate charge."
    )
    assert (
        result.actual_summary
        == "Customer reports a duplicate charge."
    )

    assert result.summary_score == 5.0
    assert result.latency_ms == 25.0
    assert result.input_tokens == 20
    assert result.output_tokens == 10


@pytest.mark.asyncio
async def test_evaluate_detects_category_mismatch(
    golden_test_case: GoldenTestCase,
    mock_llm_client: AsyncMock,
) -> None:
    """Service should detect an incorrect category."""

    mock_llm_client.generate.return_value = LLMResponse(
        output=LLMOutput(
            category="technical",
            summary="Customer reports a duplicate charge.",
        ),
        latency_ms=30.0,
        input_tokens=20,
        output_tokens=10,
    )

    service = EvaluationService(mock_llm_client)

    result = await service.evaluate(
        test_case=golden_test_case,
        system_prompt="Classify the customer message.",
    )

    assert result.category_match is False
    assert result.expected_category == "billing"
    assert result.actual_category == "technical"


@pytest.mark.asyncio
async def test_evaluate_sends_correct_input_to_llm(
    golden_test_case: GoldenTestCase,
    mock_llm_client: AsyncMock,
) -> None:
    """Service should pass the golden test input to the LLM client."""

    service = EvaluationService(mock_llm_client)

    await service.evaluate(
        test_case=golden_test_case,
        system_prompt="Classify the customer message.",
    )

    mock_llm_client.generate.assert_awaited_once_with(
        system_prompt="Classify the customer message.",
        user_input="I was charged twice for the same order.",
    )