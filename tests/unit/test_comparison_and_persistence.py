"""Unit tests for comparison, thresholds, drift, and JSON persistence."""

from datetime import timedelta
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


def results(
    matches: int,
    total: int,
    score: float = 4.0,
    latency: float = 20.0,
    tokens_in: int = 3,
    tokens_out: int = 2,
) -> list[EvaluationResult]:
    """Build a result list with the requested pass count and metrics."""
    return [
        EvaluationResult(
            test_case_id=f"case-{index}",
            expected_category="billing",
            actual_category="billing" if index < matches else "technical",
            expected_summary="expected",
            actual_summary="actual",
            category_match=index < matches,
            summary_score=score,
            latency_ms=latency,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
        )
        for index in range(total)
    ]


def report(results_list: list[EvaluationResult]):
    """Build a report with stable metadata."""
    return ReportingService().build_report(
        results_list, "v1", "model", "dataset",
    )


class TestAggregation:
    """Report aggregation arithmetic."""

    def test_empty_results(self) -> None:
        built = report([])
        assert built.case_count == 0
        assert built.category_accuracy == 0.0
        assert built.average_summary_score == 0.0
        assert built.average_latency_ms == 0.0
        assert built.total_input_tokens == 0
        assert built.total_output_tokens == 0
        assert built.results == []

    def test_single_result(self) -> None:
        built = report(results(1, 1))
        assert built.case_count == 1
        assert built.category_accuracy == 1.0
        assert built.average_summary_score == 4.0
        assert built.average_latency_ms == 20.0
        assert built.total_input_tokens == 3

    def test_multiple_results_accuracy(self) -> None:
        built = report(results(3, 4))
        assert built.case_count == 4
        assert built.category_accuracy == 0.75

    def test_average_summary_score(self) -> None:
        built = report([
            EvaluationResult(
                test_case_id="c", expected_category="b", actual_category="b",
                expected_summary="e", actual_summary="a", category_match=True,
                summary_score=score, latency_ms=1, input_tokens=1,
                output_tokens=1,
            )
            for score in (2.0, 5.0)
        ])
        assert built.average_summary_score == 3.5

    def test_average_latency(self) -> None:
        built = report(results(1, 2, latency=30.0))
        assert built.average_latency_ms == 30.0

    def test_token_totals(self) -> None:
        built = report(results(2, 2, tokens_in=7, tokens_out=5))
        assert built.total_input_tokens == 14
        assert built.total_output_tokens == 10

    def test_failed_cases_are_retained(self) -> None:
        built = report(results(1, 4))
        assert len(built.results) == 4
        assert sum(not item.category_match for item in built.results) == 3


class TestComparison:
    """Factual deltas between two reports."""

    def test_identical_runs_produce_zero_deltas(self) -> None:
        comparison = ComparisonService().compare(
            report(results(2, 2)), report(results(2, 2)),
        )
        assert comparison.category_accuracy_delta == 0.0
        assert comparison.summary_score_delta == 0.0
        assert comparison.latency_delta_ms == 0.0
        assert comparison.input_token_delta == 0
        assert comparison.output_token_delta == 0
        assert not comparison.quality_decreased

    def test_category_accuracy_decrease(self) -> None:
        comparison = ComparisonService().compare(
            report(results(2, 2)), report(results(1, 2)),
        )
        assert comparison.category_accuracy_delta == -0.5
        assert comparison.quality_decreased

    def test_summary_score_decrease(self) -> None:
        comparison = ComparisonService().compare(
            report(results(2, 2, score=4.0)),
            report(results(2, 2, score=3.0)),
        )
        assert comparison.summary_score_delta == -1.0
        assert comparison.quality_decreased

    def test_latency_increase(self) -> None:
        comparison = ComparisonService().compare(
            report(results(1, 1, latency=10.0)),
            report(results(1, 1, latency=25.0)),
        )
        assert comparison.latency_delta_ms == 15.0
        assert not comparison.quality_decreased

    def test_token_increase(self) -> None:
        comparison = ComparisonService().compare(
            report(results(1, 1, tokens_in=5, tokens_out=4)),
            report(results(1, 1, tokens_in=9, tokens_out=6)),
        )
        assert comparison.input_token_delta == 4
        assert comparison.output_token_delta == 2


class TestThresholds:
    """Configurable regression decisions."""

    def test_all_thresholds_pass(self) -> None:
        decision = RegressionService().evaluate(
            report(results(2, 2)),
            RegressionThresholds(
                minimum_category_accuracy=0.5, minimum_summary_score=3.0,
            ),
        )
        assert decision.passed
        assert decision.violations == []

    def test_category_threshold_violation(self) -> None:
        decision = RegressionService().evaluate(
            report(results(1, 2)),
            RegressionThresholds(minimum_category_accuracy=0.9),
        )
        assert not decision.passed
        violation = decision.violations[0]
        assert violation.metric == "category_accuracy"
        assert violation.direction == "below"
        assert violation.actual_value == 0.5
        assert violation.threshold == 0.9

    def test_summary_threshold_violation(self) -> None:
        decision = RegressionService().evaluate(
            report(results(2, 2, score=3.0)),
            RegressionThresholds(minimum_summary_score=4.0),
        )
        assert not decision.passed
        assert decision.violations[0].metric == "average_summary_score"

    def test_latency_threshold_violation(self) -> None:
        decision = RegressionService().evaluate(
            report(results(1, 1, latency=200.0)),
            RegressionThresholds(maximum_average_latency_ms=100.0),
        )
        assert not decision.passed
        violation = decision.violations[0]
        assert violation.metric == "average_latency_ms"
        assert violation.direction == "above"

    def test_multiple_simultaneous_violations(self) -> None:
        decision = RegressionService().evaluate(
            report(results(0, 2, score=1.0, latency=500.0)),
            RegressionThresholds(
                minimum_category_accuracy=0.9,
                minimum_summary_score=4.0,
                maximum_average_latency_ms=100.0,
            ),
        )
        assert not decision.passed
        assert len(decision.violations) == 3

    def test_drop_thresholds_use_comparison(self) -> None:
        baseline, current = report(results(2, 2)), report(results(0, 2))
        comparison = ComparisonService().compare(baseline, current)
        decision = RegressionService().evaluate(
            current,
            RegressionThresholds(
                maximum_category_accuracy_drop=0.25,
                maximum_summary_score_drop=0.5,
            ),
            comparison=comparison,
        )
        assert not decision.passed
        metrics = {violation.metric for violation in decision.violations}
        assert metrics == {"category_accuracy_drop"}

    def test_empty_report_passes_zero_thresholds(self) -> None:
        assert RegressionService().evaluate(
            report([]), RegressionThresholds(),
        ).passed


class TestDrift:
    """Category-distribution drift between runs."""

    def test_identical_distributions(self) -> None:
        drift = DriftService().compare(
            report(results(2, 2)), report(results(2, 2)),
        )
        assert drift.sufficient_data
        assert drift.total_variation_distance == 0.0

    def test_changed_category_distribution(self) -> None:
        drift = DriftService().compare(
            report(results(2, 2)), report(results(0, 2)),
        )
        assert drift.sufficient_data
        assert drift.total_variation_distance == 1.0
        assert drift.current_distribution == {"technical": 1.0}

    def test_insufficient_data_when_either_run_empty(self) -> None:
        assert not DriftService().compare(
            report([]), report(results(1, 1)),
        ).sufficient_data
        assert not DriftService().compare(
            report(results(1, 1)), report([]),
        ).sufficient_data


class TestJsonPersistence:
    """Complete-run persistence on the filesystem."""

    def test_save_and_load_round_trip(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(2, 3))
        repository.save(saved)
        loaded = repository.get(saved.run_id)
        assert loaded == saved
        assert len(loaded.results) == 3

    def test_get_latest(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        assert repository.get_latest() is None
        first = report(results(1, 1))
        second = report(results(1, 1))
        second.timestamp = first.timestamp + timedelta(seconds=1)
        repository.save(first)
        repository.save(second)
        latest = repository.get_latest()
        assert latest is not None
        assert latest.run_id == second.run_id

    def test_list_runs_returns_all_saved_reports(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        first, second = report(results(1, 1)), report(results(1, 1))
        repository.save(first)
        repository.save(second)
        run_ids = {item.run_id for item in repository.list_runs()}
        assert run_ids == {first.run_id, second.run_id}

    def test_list_runs_empty_directory(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        assert repository.list_runs() == []

    def test_missing_report_raises(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        with pytest.raises(FileNotFoundError):
            repository.get("no-such-run")

    def test_corrupted_json_raises(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid evaluation run"):
            repository.list_runs()

    def test_save_persists_regression_data(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(0, 2))
        saved.regression = RegressionService().evaluate(
            saved, RegressionThresholds(minimum_category_accuracy=1.0),
        )
        repository.save(saved)
        loaded = repository.get(saved.run_id)
        assert loaded.regression is not None
        assert not loaded.regression.passed
