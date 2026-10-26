import math

import pytest
from pydantic import ValidationError

from promptguard.application.comparison_service import ComparisonService
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import (
    EvaluationReport,
    EvaluationResult,
    GoldenTestCase,
    LLMOutput,
    LLMResponse,
)
from promptguard.domain.regression import EvaluationComparison
from promptguard.domain.thresholds import RegressionThresholds


def test_valid_llm_output():
    output = LLMOutput(
        category="billing",
        summary="Customer reports being charged twice.",
    )

    assert output.category == "billing"
    assert output.summary == ("Customer reports being charged twice.")


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


@pytest.mark.parametrize(
    "field",
    ["id", "input", "expected_category", "ideal_summary"],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_golden_case_rejects_blank_required_text(
    field: str,
    value: str,
) -> None:
    """Blank case evidence must be rejected at the domain boundary."""
    values = {
        "id": "TC-001",
        "input": "I was charged twice.",
        "expected_category": "billing",
        "ideal_summary": "Customer reports a duplicate charge.",
        "expected_difficulty": "easy",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        GoldenTestCase(**values)


def test_golden_case_strips_required_text() -> None:
    """Surrounding whitespace should not create distinct or blank evidence."""
    case = GoldenTestCase(
        id="  TC-001  ",
        input="  I was charged twice.  ",
        expected_category="  billing  ",
        ideal_summary="  Customer reports a duplicate charge.  ",
        expected_difficulty="easy",
    )

    assert case.id == "TC-001"
    assert case.input == "I was charged twice."
    assert case.expected_category == "billing"
    assert case.ideal_summary == "Customer reports a duplicate charge."


@pytest.mark.parametrize(
    ("category", "summary"),
    [("   ", "valid summary"), ("billing", "   ")],
)
def test_llm_output_rejects_whitespace_only_fields(
    category: str,
    summary: str,
) -> None:
    """Whitespace-only model output is not valid structured evidence."""
    with pytest.raises(ValidationError):
        LLMOutput(category=category, summary=summary)


def test_critical_case_requires_rationale():
    with pytest.raises(ValueError, match="critical_rationale"):
        GoldenTestCase(
            id="TC-002",
            input="I lost my card.",
            expected_category="lost_or_stolen_card",
            ideal_summary="Customer reports a lost card.",
            expected_difficulty="medium",
            critical=True,
        )


def test_critical_case_accepts_documented_rationale():
    case = GoldenTestCase(
        id="TC-002",
        input="I lost my card.",
        expected_category="lost_or_stolen_card",
        ideal_summary="Customer reports a lost card.",
        expected_difficulty="medium",
        critical=True,
        critical_rationale="Lost cards require immediate blocking guidance.",
    )

    assert case.critical
    assert case.critical_rationale


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


@pytest.mark.parametrize(
    "field",
    [
        "test_case_id",
        "expected_category",
        "actual_category",
        "expected_summary",
        "actual_summary",
    ],
)
@pytest.mark.parametrize("value", ["", "   "])
def test_result_rejects_blank_required_text(
    field: str,
    value: str,
) -> None:
    """Blank result evidence must be rejected at the domain boundary."""
    values = {
        "test_case_id": "TC-001",
        "expected_category": "billing",
        "actual_category": "billing",
        "expected_summary": "Customer reports a duplicate charge.",
        "actual_summary": "Customer was charged twice.",
        "category_match": True,
        "summary_score": 4.0,
        "latency_ms": 1.0,
        "input_tokens": 1,
        "output_tokens": 1,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        EvaluationResult(**values)


def build_report(category_match: bool = True) -> EvaluationReport:
    """Build a valid one-case report for aggregate-invariant tests."""
    result = EvaluationResult(
        test_case_id="TC-001",
        expected_category="billing",
        actual_category="billing" if category_match else "technical",
        expected_summary="Customer reports a duplicate charge.",
        actual_summary="Customer was charged twice.",
        category_match=category_match,
        summary_score=4.0,
        latency_ms=250.0,
        input_tokens=20,
        output_tokens=12,
    )
    return ReportingService().build_report([result], "v1", "m", "d")


def test_empty_report_is_valid_with_zero_aggregates() -> None:
    """Empty runs remain loadable and are gated as insufficient evidence."""
    report = ReportingService().build_report([], "v1", "m", "d")
    loaded = EvaluationReport.model_validate(report.model_dump())
    assert loaded.case_count == 0
    assert loaded.category_accuracy == 0.0
    assert loaded.evidence_issues()


def test_report_rejects_blank_result_id_evidence() -> None:
    """A mutated blank case id cannot remain trustworthy evidence."""
    report = build_report()
    report.results[0].test_case_id = "   "

    assert any(
        "blank test case ids" in issue
        for issue in report.evidence_issues()
    )


@pytest.mark.parametrize(
    "field",
    ["run_id", "prompt_version", "model", "dataset", "baseline_run_id"],
)
def test_report_rejects_blank_identifiers(field: str) -> None:
    """Persisted report identity fields cannot be blank."""
    payload = build_report().model_dump()
    payload[field] = "   "

    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(payload)


def test_report_rejects_mutated_blank_identifier_evidence() -> None:
    """A mutated blank report identity must not satisfy a quality gate."""
    report = build_report()
    report.run_id = "   "

    assert any(
        "blank run_id" in issue for issue in report.evidence_issues()
    )


@pytest.mark.parametrize(
    "field",
    [
        "expected_category",
        "actual_category",
        "expected_summary",
        "actual_summary",
    ],
)
def test_report_rejects_mutated_blank_result_text(field: str) -> None:
    """Mutated result text must still be rejected as evidence."""
    report = build_report()
    setattr(report.results[0], field, "   ")

    assert any(
        field in issue for issue in report.evidence_issues()
    )


def test_comparison_rejects_blank_run_ids() -> None:
    """Comparison identifiers must remain non-empty when persisted."""
    comparison = ComparisonService().compare(
        build_report(), build_report(),
    )
    payload = comparison.model_dump()
    payload["current_run_id"] = "   "

    with pytest.raises(ValidationError):
        EvaluationComparison.model_validate(payload)


def test_thresholds_reject_infinite_latency() -> None:
    """An infinite latency threshold would disable the latency gate."""
    with pytest.raises(ValidationError):
        RegressionThresholds(maximum_average_latency_ms=math.inf)


def test_report_rejects_case_count_mismatch() -> None:
    """A report whose count disagrees with its results is invalid."""
    payload = build_report().model_dump()
    payload["case_count"] = 2
    with pytest.raises(ValidationError, match="case_count"):
        EvaluationReport.model_validate(payload)


def test_report_rejects_inconsistent_category_accuracy() -> None:
    """Aggregate accuracy cannot disagree with the case evidence."""
    payload = build_report(category_match=False).model_dump()
    payload["category_accuracy"] = 1.0
    with pytest.raises(ValidationError, match="category_accuracy"):
        EvaluationReport.model_validate(payload)


def test_report_rejects_inconsistent_token_totals() -> None:
    """Token totals must be derived from the case results."""
    payload = build_report().model_dump()
    payload["total_input_tokens"] = 999
    with pytest.raises(ValidationError, match="total_input_tokens"):
        EvaluationReport.model_validate(payload)


def test_report_rejects_non_finite_latency() -> None:
    """Infinite aggregate latency is not a usable quality signal."""
    payload = build_report().model_dump()
    payload["average_latency_ms"] = math.inf
    with pytest.raises(ValidationError, match="average_latency_ms"):
        EvaluationReport.model_validate(payload)


def test_report_rejects_nan_accuracy() -> None:
    """NaN accuracy is not a usable quality signal."""
    payload = build_report().model_dump()
    payload["category_accuracy"] = math.nan
    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(payload)


def test_report_rejects_out_of_range_accuracy() -> None:
    """Accuracy must remain within its declared range."""
    payload = build_report().model_dump()
    payload["category_accuracy"] = 1.5
    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(payload)


def test_report_rejects_missing_required_metric() -> None:
    """Missing required metrics cannot be silently defaulted."""
    payload = build_report().model_dump()
    payload.pop("average_summary_score")
    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(payload)


def test_result_rejects_non_finite_latency() -> None:
    """Per-case latency must be finite."""
    with pytest.raises(ValidationError, match="latency_ms"):
        EvaluationResult(
            test_case_id="TC-001",
            expected_category="billing",
            actual_category="billing",
            expected_summary="expected",
            actual_summary="actual",
            category_match=True,
            summary_score=4.0,
            latency_ms=math.inf,
            input_tokens=1,
            output_tokens=1,
        )


def test_result_rejects_nan_summary_score() -> None:
    """Per-case summary scores must be finite."""
    with pytest.raises(ValidationError):
        EvaluationResult(
            test_case_id="TC-001",
            expected_category="billing",
            actual_category="billing",
            expected_summary="expected",
            actual_summary="actual",
            category_match=True,
            summary_score=math.nan,
            latency_ms=1.0,
            input_tokens=1,
            output_tokens=1,
        )
