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
    mock_summary_metric: AsyncMock,
) -> None:
    """Service should convert an LLM response into an evaluation result."""

    service = EvaluationService(
    llm_client=mock_llm_client,
    summary_metric=mock_summary_metric,
)
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
    mock_summary_metric: AsyncMock,
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

    service = EvaluationService(
    llm_client=mock_llm_client,
    summary_metric=mock_summary_metric,
)

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
    mock_summary_metric: AsyncMock,
) -> None:
    """Service should pass the golden test input to the LLM client."""

    service = EvaluationService(
    llm_client=mock_llm_client,
    summary_metric=mock_summary_metric,
)

    await service.evaluate(
        test_case=golden_test_case,
        system_prompt="Classify the customer message.",
    )

    mock_llm_client.generate.assert_awaited_once_with(
        system_prompt="Classify the customer message.",
        user_input="I was charged twice for the same order.",
    )


@pytest.mark.asyncio
async def test_evaluate_dataset_uses_fake_infrastructure(
    golden_test_case: GoldenTestCase,
    mock_llm_client: AsyncMock,
    mock_summary_metric: AsyncMock,
) -> None:
    """The complete application flow should work without external services."""
    second_case = golden_test_case.model_copy(update={"id": "case-002"})
    service = EvaluationService(
        llm_client=mock_llm_client,
        summary_metric=mock_summary_metric,
    )

    results = await service.evaluate_dataset(
        test_cases=[golden_test_case, second_case],
        system_prompt="Classify the customer message.",
    )

    assert [result.test_case_id for result in results] == [
        "case-001",
        "case-002",
    ]
    assert all(result.category_match for result in results)
    assert mock_llm_client.generate.await_count == 2
    assert mock_summary_metric.score.await_count == 2

@pytest.fixture
def mock_summary_metric() -> AsyncMock:
    """Provide a mock summary metric."""
    metric = AsyncMock()
    metric.score.return_value = 5.0
    return metric    
