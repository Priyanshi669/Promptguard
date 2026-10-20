"""Unit tests for report aggregation, comparison, drift, and persistence."""

from pathlib import Path

import pytest

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.drift_service import DriftService
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import EvaluationResult
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)


def result(category_match: bool = True, category: str = "billing") -> EvaluationResult:
    """Build a concise result fixture."""
    return EvaluationResult(test_case_id="case", expected_category="billing", actual_category=category, expected_summary="expected", actual_summary="actual", category_match=category_match, summary_score=4.0, latency_ms=20, input_tokens=3, output_tokens=2)


def report(results: list[EvaluationResult]):
    """Build a report with stable metadata."""
    return ReportingService().build_report(results, "v1", "model", "dataset")


def test_reporting_aggregates_and_handles_empty_results() -> None:
    """Aggregation should calculate every metric and explicitly support empty runs."""
    populated = report([result(), result(False, "technical")])
    empty = report([])
    assert populated.case_count == 2
    assert populated.category_accuracy == 0.5
    assert populated.average_summary_score == 4.0
    assert populated.average_latency_ms == 20
    assert populated.total_input_tokens == 6
    assert populated.total_output_tokens == 4
    assert (empty.case_count, empty.category_accuracy) == (0, 0.0)


def test_comparison_thresholds_and_drift() -> None:
    """Comparison, threshold violations, and category drift remain separate."""
    baseline = report([result(), result()])
    current = report([result(False, "technical"), result(False, "technical")])
    comparison = ComparisonService().compare(baseline, current)
    decision = RegressionService().evaluate(current, RegressionThresholds(minimum_category_accuracy=0.5, minimum_summary_score=4.5), comparison)
    drift = DriftService().compare(baseline, current)
    assert comparison.category_accuracy_delta == -1.0
    assert comparison.quality_decreased
    assert not decision.passed and len(decision.violations) == 2
    assert drift.sufficient_data and drift.total_variation_distance == 1.0


def test_drift_reports_insufficient_data() -> None:
    """No distribution claim is made when either run is empty."""
    assert not DriftService().compare(report([]), report([result()])).sufficient_data


def test_json_repository_round_trip_and_errors(tmp_path: Path) -> None:
    """JSON persistence should save, load, list, and reject bad data."""
    repository = JsonEvaluationRunRepository(tmp_path)
    saved = report([result()])
    repository.save(saved)
    assert repository.get(saved.run_id).run_id == saved.run_id
    assert repository.get_latest().run_id == saved.run_id  # type: ignore[union-attr]
    assert len(repository.list_runs()) == 1
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid evaluation run"):
        repository.list_runs()
    with pytest.raises(FileNotFoundError):
        repository.get("missing")
