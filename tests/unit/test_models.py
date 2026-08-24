import pytest

from promptguard.domain.models import (
    EvaluationResult,
    GoldenTestCase,
    LLMOutput,
    LLMResponse,
)


def test_valid_llm_output():
    output = LLMOutput(
        category="billing",
        summary="Customer reports being charged twice.",
    )

    assert output.category == "billing"
    assert output.summary == (
        "Customer reports being charged twice."
    )


def test_llm_output_rejects_empty_category():
    with pytest.raises(ValueError):
        LLMOutput(
            category="",
            summary="Customer reports a duplicate charge.",
        )


def test_llm_output_rejects_empty_summary():
    with pytest.raises(ValueError):
        LLMOutput(
            category="billing",
            summary="",
        )

def test_golden_test_case_creation():
    case = GoldenTestCase(
        id="TC-001",
        input="I was charged twice for my subscription.",
        expected_category="billing",
        ideal_summary="Customer reports a duplicate subscription charge.",
        expected_difficulty="easy",
    )

    assert case.id == "TC-001"
    assert case.expected_category == "billing"


def test_llm_response_creation():
    response = LLMResponse(
        output=LLMOutput(
            category="billing",
            summary="Customer reports a duplicate subscription charge.",
        ),
        latency_ms=250.5,
        input_tokens=20,
        output_tokens=12,
    )

    assert response.output.category == "billing"
    assert response.output.summary == (
        "Customer reports a duplicate subscription charge."
    )
    assert response.latency_ms == 250.5
    assert response.input_tokens == 20
    assert response.output_tokens == 12

def test_test_result_creation():
    result = EvaluationResult(
        test_case_id="TC-001",
        expected_category="billing",
        actual_category="billing",
        expected_summary="Customer reports a duplicate subscription charge.",
        actual_summary="Customer was charged twice.",
        category_match=True,
        summary_score=5,
        latency_ms=250.5,
        input_tokens=20,
        output_tokens=12,
    )

    assert result.category_match is True
    assert result.summary_score == 5