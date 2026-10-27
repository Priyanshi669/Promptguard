"""Unit tests for classification metrics, verified against hand calculations."""

import pytest

from promptguard.application.reporting_service import ReportingService
from promptguard.domain.classification import classification_metrics
from promptguard.domain.models import EvaluationResult
from promptguard.infrastructure.reporting.html_renderer import (
    TextReportFormatter,
)

_seq = iter(range(10000))


def result(
    expected: str,
    actual: str,
    normalized: str | None = None,
) -> EvaluationResult:
    """Build one evaluation result with controlled category evidence."""
    effective = normalized if normalized is not None else actual
    return EvaluationResult(
        test_case_id=f"case-{next(_seq)}",
        expected_category=expected,
        actual_category=actual,
        normalized_category=effective,
        expected_summary="ideal summary",
        actual_summary="actual summary",
        category_match=effective.strip().lower() == expected.strip().lower(),
        summary_score=4.0,
        latency_ms=100.0,
        input_tokens=1,
        output_tokens=1,
    )


def scenario_a() -> list[EvaluationResult]:
    """3x A, 2x B, 1x C with realistic mistakes."""
    return [
        result("A", "A"),
        result("A", "A"),
        result("A", "B"),
        result("B", "B"),
        result("B", "C"),
        result("C", "C"),
    ]


def test_hand_calculated_per_class_metrics() -> None:
    metrics = classification_metrics(scenario_a())

    a = metrics.per_class["a"]
    assert (a.support, a.tp, a.fp, a.fn) == (3, 2, 0, 1)
    assert a.precision == pytest.approx(1.0)
    assert a.recall == pytest.approx(2 / 3)
    assert a.f1 == pytest.approx(0.8)

    b = metrics.per_class["b"]
    assert (b.support, b.tp, b.fp, b.fn) == (2, 1, 1, 1)
    assert b.precision == pytest.approx(0.5)
    assert b.recall == pytest.approx(0.5)
    assert b.f1 == pytest.approx(0.5)

    c = metrics.per_class["c"]
    assert (c.support, c.tp, c.fp, c.fn) == (1, 1, 1, 0)
    assert c.precision == pytest.approx(0.5)
    assert c.recall == pytest.approx(1.0)
    assert c.f1 == pytest.approx(2 / 3)


def test_hand_calculated_aggregates() -> None:
    metrics = classification_metrics(scenario_a())

    assert metrics.total_cases == 6
    assert metrics.accuracy == pytest.approx(2 / 3)
    assert metrics.macro_precision == pytest.approx(2 / 3)
    assert metrics.macro_recall == pytest.approx(13 / 18)
    assert metrics.macro_f1 == pytest.approx((0.8 + 0.5 + 2 / 3) / 3)
    assert metrics.weighted_precision == pytest.approx(0.75)
    assert metrics.weighted_recall == pytest.approx(2 / 3)
    assert metrics.weighted_f1 == pytest.approx(
        (0.8 * 3 + 0.5 * 2 + (2 / 3) * 1) / 6
    )


def test_hand_calculated_confusion_matrix() -> None:
    metrics = classification_metrics(scenario_a())

    assert metrics.confusion == {
        "a": {"a": 2, "b": 1, "c": 0},
        "b": {"a": 0, "b": 1, "c": 1},
        "c": {"a": 0, "b": 0, "c": 1},
    }


def test_class_never_predicted_scores_zero() -> None:
    """Zero-division conventions: tp=fp=fn buckets yield 0.0, not NaN."""
    metrics = classification_metrics(
        [result("A", "B"), result("A", "B")],
    )
    a = metrics.per_class["a"]
    b = metrics.per_class["b"]
    assert (a.precision, a.recall, a.f1) == (0.0, 0.0, 0.0)
    assert (b.precision, b.recall, b.f1) == (0.0, 0.0, 0.0)
    assert a.support == 2
    assert b.support == 0
    assert metrics.accuracy == 0.0


def test_labels_are_normalized_and_lowercased() -> None:
    metrics = classification_metrics(
        [result("Card_Pin", "CARD_PIN", normalized="card_pin")],
    )
    assert list(metrics.per_class) == ["card_pin"]
    assert metrics.accuracy == 1.0
    assert metrics.confusion == {"card_pin": {"card_pin": 1}}


def test_empty_results_produce_zeroed_metrics() -> None:
    metrics = classification_metrics([])
    assert metrics.total_cases == 0
    assert metrics.accuracy == 0.0
    assert metrics.macro_f1 == 0.0
    assert metrics.weighted_f1 == 0.0
    assert metrics.per_class == {}
    assert metrics.confusion == {}


def test_build_report_populates_classification() -> None:
    report = ReportingService().build_report(
        scenario_a(), "v1", "test-model", "test-dataset",
    )
    assert report.classification is not None
    assert report.classification.accuracy == pytest.approx(2 / 3)
    assert report.aggregate_issues() == []


def test_inconsistent_classification_accuracy_is_rejected() -> None:
    report = ReportingService().build_report(
        scenario_a(), "v1", "test-model", "test-dataset",
    )
    assert report.classification is not None
    payload = report.model_dump(mode="json")
    payload["classification"]["accuracy"] = 0.99

    from promptguard.domain.models import EvaluationReport

    with pytest.raises(
        ValueError,
        match="classification.accuracy is inconsistent",
    ):
        EvaluationReport.model_validate(payload)


def test_older_reports_without_classification_still_load() -> None:
    report = ReportingService().build_report(
        scenario_a(), "v1", "test-model", "test-dataset",
    )
    payload = report.model_dump(mode="json")
    del payload["classification"]

    from promptguard.domain.models import EvaluationReport

    loaded = EvaluationReport.model_validate(payload)
    assert loaded.classification is None


def test_formatter_renders_classification_block() -> None:
    report = ReportingService().build_report(
        scenario_a(), "v1", "test-model", "test-dataset",
    )
    text = TextReportFormatter().format(report)
    assert "Classification metrics:" in text
    assert "Macro F1:" in text
    assert "Weighted F1:" in text
    assert "Accuracy (micro): 0.6667" in text
    assert "Confusion matrix (expected -> actual):" in text
    assert "    a | 2 | 1 | 0" in text


def test_formatter_omits_wide_confusion_matrices() -> None:
    labels = [f"class-{i}" for i in range(9)]
    results = [result(label, label) for label in labels]
    report = ReportingService().build_report(
        results, "v1", "test-model", "test-dataset",
    )
    text = TextReportFormatter().format(report)
    assert "omitted from console output" in text
    assert report.classification is not None
    assert len(report.classification.confusion) == 9
