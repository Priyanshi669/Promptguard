"""Unit tests for comparison, thresholds, drift, and JSON persistence."""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.drift_service import DriftService
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import EvaluationReport, EvaluationResult
from promptguard.domain.thresholds import (
    RegressionThresholds,
    ThresholdViolation,
)
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.interfaces.cli import _exit_code_for


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

    def test_percentage_drop_thresholds_use_relative_change(self) -> None:
        baseline, current = report(results(10, 10)), report(results(8, 10))
        comparison = ComparisonService().compare(baseline, current)
        decision = RegressionService().evaluate(
            current,
            RegressionThresholds(maximum_category_accuracy_drop_pct=15),
            comparison=comparison,
        )
        assert not decision.passed
        violation = decision.violations[0]
        assert violation.metric == "category_accuracy_drop_pct"
        assert violation.actual_value == pytest.approx(20.0)
        assert violation.threshold == 15

    def test_percentage_drop_within_limit_passes(self) -> None:
        baseline, current = report(results(10, 10)), report(results(8, 10))
        comparison = ComparisonService().compare(baseline, current)
        decision = RegressionService().evaluate(
            current,
            RegressionThresholds(maximum_category_accuracy_drop_pct=25),
            comparison=comparison,
        )
        assert decision.passed

    def test_summary_percentage_drop_threshold(self) -> None:
        baseline = report(results(2, 2, score=4.0))
        current = report(results(2, 2, score=3.0))
        comparison = ComparisonService().compare(baseline, current)
        decision = RegressionService().evaluate(
            current,
            RegressionThresholds(maximum_summary_score_drop_pct=20),
            comparison=comparison,
        )
        assert not decision.passed
        violation = decision.violations[0]
        assert violation.metric == "summary_score_drop_pct"
        assert violation.actual_value == pytest.approx(25.0)

    def test_percentage_drop_handles_zero_baseline(self) -> None:
        baseline = report(results(0, 2))
        current = report(results(0, 2))
        comparison = ComparisonService().compare(baseline, current)
        decision = RegressionService().evaluate(
            current,
            RegressionThresholds(maximum_category_accuracy_drop_pct=1),
            comparison=comparison,
        )
        assert decision.passed

    def test_empty_report_passes_zero_thresholds(self) -> None:
        decision = RegressionService().evaluate(
            report([]), RegressionThresholds(),
        )
        assert decision.status == "INCONCLUSIVE"
        assert not decision.passed

    def test_empty_report_never_passes_configured_thresholds(self) -> None:
        """Zero-valued thresholds must not turn an empty run into evidence."""
        decision = RegressionService().evaluate(
            report([]),
            RegressionThresholds(
                minimum_category_accuracy=0.0,
                maximum_average_latency_ms=0.0,
            ),
        )
        assert decision.status == "INCONCLUSIVE"
        assert any("no evaluated cases" in reason
                   for reason in decision.incompatibility_reasons)

    def test_case_count_mismatch_is_inconclusive(self) -> None:
        """A report whose case_count disagrees with its results is malformed."""
        built = report(results(1, 1))
        built.case_count = 2
        decision = RegressionService().evaluate(
            built, RegressionThresholds(minimum_category_accuracy=0.0),
        )
        assert decision.status == "INCONCLUSIVE"
        assert any("case_count" in reason
                   for reason in decision.incompatibility_reasons)

    def test_duplicate_result_ids_are_inconclusive(self) -> None:
        """Duplicate evidence cannot produce a gate decision."""
        duplicate_results = [
            result.model_copy(update={"test_case_id": "same"})
            for result in results(2, 2)
        ]
        decision = RegressionService().evaluate(
            report(duplicate_results),
            RegressionThresholds(minimum_category_accuracy=0.0),
        )
        assert decision.status == "INCONCLUSIVE"
        assert any("duplicate" in reason
                   for reason in decision.incompatibility_reasons)

    def test_blank_result_ids_cannot_pass_gate(self) -> None:
        """Blank case identity cannot produce PASS or CLI exit code 0."""
        built = report(results(1, 1))
        built.results[0].test_case_id = "   "
        decision = RegressionService().evaluate(
            built,
            RegressionThresholds(minimum_category_accuracy=0.0),
        )
        built.regression = decision

        assert decision.status == "INCONCLUSIVE"
        assert any("blank" in reason
                   for reason in decision.incompatibility_reasons)
        assert _exit_code_for(built) == 2

    def test_mutated_blank_result_text_cannot_pass_gate(self) -> None:
        """Mutated result text cannot produce PASS or CLI exit code 0."""
        built = report(results(1, 1))
        built.results[0].actual_summary = "   "
        decision = RegressionService().evaluate(
            built,
            RegressionThresholds(minimum_category_accuracy=0.0),
        )
        built.regression = decision

        assert decision.status == "INCONCLUSIVE"
        assert any("actual_summary" in reason
                   for reason in decision.incompatibility_reasons)
        assert _exit_code_for(built) == 2

    def test_mutated_category_match_cannot_pass_gate(self) -> None:
        """A contradictory category match cannot produce PASS."""
        built = report(results(1, 1))
        built.results[0].category_match = False
        decision = RegressionService().evaluate(
            built,
            RegressionThresholds(minimum_category_accuracy=0.0),
        )
        built.regression = decision

        assert decision.status == "INCONCLUSIVE"
        assert any("category_match" in reason
                   for reason in decision.incompatibility_reasons)
        assert _exit_code_for(built) == 2

    def test_inconsistent_aggregate_is_inconclusive(self) -> None:
        """A mutated aggregate cannot satisfy a threshold by itself."""
        built = report(results(0, 1))
        built.category_accuracy = 1.0
        decision = RegressionService().evaluate(
            built,
            RegressionThresholds(minimum_category_accuracy=0.8),
        )
        assert decision.status == "INCONCLUSIVE"
        assert any(
            "category_accuracy" in reason
            for reason in decision.incompatibility_reasons
        )


class TestCombinedThresholds:
    """Absolute and drop thresholds are all mandatory."""

    @staticmethod
    def _decide(
        current,
        thresholds,
        comparison=None,
    ):
        decision = RegressionService().evaluate(
            current, thresholds, comparison=comparison,
        )
        current.regression = decision
        return decision

    def test_absolute_only_pass_and_fail(self) -> None:
        passing = report(results(2, 2))
        pass_decision = self._decide(
            passing,
            RegressionThresholds(minimum_category_accuracy=1.0),
        )
        assert pass_decision.status == "PASS"
        assert pass_decision.violations == []
        assert _exit_code_for(passing) == 0

        failing = report(results(1, 2))
        fail_decision = self._decide(
            failing,
            RegressionThresholds(minimum_category_accuracy=0.9),
        )
        assert fail_decision.status == "FAIL"
        assert [v.metric for v in fail_decision.violations] == [
            "category_accuracy",
        ]
        assert _exit_code_for(failing) == 1

    def test_drop_only_pass_and_fail(self) -> None:
        baseline = report(results(2, 2))
        passing = report(results(2, 2))
        pass_decision = self._decide(
            passing,
            RegressionThresholds(maximum_category_accuracy_drop=0.0),
            comparison=ComparisonService().compare(baseline, passing),
        )
        assert pass_decision.status == "PASS"
        assert pass_decision.violations == []
        assert _exit_code_for(passing) == 0

        failing = report(results(1, 2))
        fail_decision = self._decide(
            failing,
            RegressionThresholds(maximum_category_accuracy_drop=0.0),
            comparison=ComparisonService().compare(baseline, failing),
        )
        assert fail_decision.status == "FAIL"
        assert [v.metric for v in fail_decision.violations] == [
            "category_accuracy_drop",
        ]
        assert _exit_code_for(failing) == 1

    def test_both_thresholds_pass(self) -> None:
        baseline = report(results(2, 2))
        current = report(results(2, 2))
        decision = self._decide(
            current,
            RegressionThresholds(
                minimum_category_accuracy=1.0,
                maximum_category_accuracy_drop=0.0,
            ),
            comparison=ComparisonService().compare(baseline, current),
        )
        assert decision.status == "PASS"
        assert decision.violations == []
        assert _exit_code_for(current) == 0

    def test_absolute_failure_is_not_hidden_by_passing_drop(self) -> None:
        baseline = report(results(1, 2))
        current = report(results(1, 2))
        decision = self._decide(
            current,
            RegressionThresholds(
                minimum_category_accuracy=0.9,
                maximum_category_accuracy_drop=0.0,
            ),
            comparison=ComparisonService().compare(baseline, current),
        )
        assert decision.status == "FAIL"
        assert [v.metric for v in decision.violations] == [
            "category_accuracy",
        ]
        assert _exit_code_for(current) == 1

    def test_drop_failure_is_not_hidden_by_passing_absolute(self) -> None:
        baseline = report(results(2, 2))
        current = report(results(1, 2))
        decision = self._decide(
            current,
            RegressionThresholds(
                minimum_category_accuracy=0.4,
                maximum_category_accuracy_drop=0.0,
            ),
            comparison=ComparisonService().compare(baseline, current),
        )
        assert decision.status == "FAIL"
        assert [v.metric for v in decision.violations] == [
            "category_accuracy_drop",
        ]
        assert _exit_code_for(current) == 1

    def test_both_thresholds_fail(self) -> None:
        baseline = report(results(2, 2))
        current = report(results(0, 2))
        decision = self._decide(
            current,
            RegressionThresholds(
                minimum_category_accuracy=0.9,
                maximum_category_accuracy_drop=0.0,
            ),
            comparison=ComparisonService().compare(baseline, current),
        )
        assert decision.status == "FAIL"
        assert {v.metric for v in decision.violations} == {
            "category_accuracy",
            "category_accuracy_drop",
        }
        assert _exit_code_for(current) == 1

    def test_absolute_pass_and_percentage_drop_fail(self) -> None:
        baseline = report(results(10, 10))
        current = report(results(8, 10))
        decision = self._decide(
            current,
            RegressionThresholds(
                minimum_category_accuracy=0.5,
                maximum_category_accuracy_drop_pct=10,
            ),
            comparison=ComparisonService().compare(baseline, current),
        )
        assert decision.status == "FAIL"
        assert [v.metric for v in decision.violations] == [
            "category_accuracy_drop_pct",
        ]
        assert _exit_code_for(current) == 1

    def test_combined_thresholds_without_baseline_are_inconclusive(
        self,
    ) -> None:
        current = report(results(2, 2))
        decision = self._decide(
            current,
            RegressionThresholds(
                minimum_category_accuracy=0.5,
                maximum_category_accuracy_drop=0.0,
            ),
            comparison=None,
        )
        assert decision.status == "INCONCLUSIVE"
        assert decision.violations == []
        assert any(
            "Drop thresholds configured but no baseline provided" in reason
            for reason in decision.incompatibility_reasons
        )
        assert _exit_code_for(current) == 2

    def test_absolute_failure_without_baseline_is_not_hidden(self) -> None:
        current = report(results(1, 2))
        decision = self._decide(
            current,
            RegressionThresholds(
                minimum_category_accuracy=0.9,
                maximum_category_accuracy_drop=0.0,
            ),
            comparison=None,
        )
        assert decision.status == "FAIL"
        assert [v.metric for v in decision.violations] == [
            "category_accuracy",
        ]
        assert _exit_code_for(current) == 1

    def test_drop_only_without_baseline_is_inconclusive(self) -> None:
        current = report(results(2, 2))
        decision = self._decide(
            current,
            RegressionThresholds(maximum_category_accuracy_drop=0.0),
            comparison=None,
        )
        assert decision.status == "INCONCLUSIVE"
        assert decision.violations == []
        assert any(
            "Drop thresholds configured but no baseline provided" in reason
            for reason in decision.incompatibility_reasons
        )
        assert _exit_code_for(current) == 2

    def test_exact_boundaries_pass(self) -> None:
        current = report(results(1, 2, latency=20.0))
        minimum_decision = self._decide(
            current,
            RegressionThresholds(minimum_category_accuracy=0.5),
        )
        assert minimum_decision.status == "PASS"

        maximum_decision = self._decide(
            current,
            RegressionThresholds(maximum_average_latency_ms=20.0),
        )
        assert maximum_decision.status == "PASS"

        baseline = report(results(2, 2))
        drop_decision = self._decide(
            current,
            RegressionThresholds(maximum_category_accuracy_drop=0.5),
            comparison=ComparisonService().compare(baseline, current),
        )
        assert drop_decision.status == "PASS"

        percentage_decision = self._decide(
            current,
            RegressionThresholds(maximum_category_accuracy_drop_pct=50),
            comparison=ComparisonService().compare(baseline, current),
        )
        assert percentage_decision.status == "PASS"

    def test_zero_baseline_and_zero_denominator_do_not_fail(self) -> None:
        baseline = report(results(0, 2))
        unchanged = report(results(0, 2))
        unchanged_decision = self._decide(
            unchanged,
            RegressionThresholds(maximum_category_accuracy_drop_pct=0),
            comparison=ComparisonService().compare(baseline, unchanged),
        )
        assert unchanged_decision.status == "PASS"

        improved = report(results(2, 2))
        improved_decision = self._decide(
            improved,
            RegressionThresholds(maximum_category_accuracy_drop_pct=0),
            comparison=ComparisonService().compare(baseline, improved),
        )
        assert improved_decision.status == "PASS"


class TestQualityGateStatus:
    """Quality gate status semantics."""

    def test_no_baseline_no_thresholds_inconclusive(self) -> None:
        """Missing baseline and no thresholds -> INCONCLUSIVE."""
        decision = RegressionService().evaluate(
            report(results(1, 1)), RegressionThresholds(),
            comparison=None,
            baseline_results=None,
        )
        assert decision.status == "INCONCLUSIVE"
        assert not decision.passed
        assert any("No thresholds configured and no baseline" in r
                   for r in decision.incompatibility_reasons)

    def test_baseline_no_thresholds_inconclusive(self) -> None:
        """Baseline provided but no thresholds -> INCONCLUSIVE."""
        baseline = report(results(1, 1))
        current = report(results(1, 1))
        comparison = ComparisonService().compare(baseline, current)
        baseline_results = {r.test_case_id: r for r in baseline.results}

        decision = RegressionService().evaluate(
            current, RegressionThresholds(),
            comparison=comparison,
            baseline_results=baseline_results,
        )
        assert decision.status == "INCONCLUSIVE"
        assert not decision.passed

    def test_thresholds_pass_gives_pass(self) -> None:
        """Thresholds met -> PASS."""
        decision = RegressionService().evaluate(
            report(results(2, 2)),
            RegressionThresholds(minimum_category_accuracy=0.5),
        )
        assert decision.status == "PASS"
        assert decision.passed

    def test_thresholds_fail_gives_fail(self) -> None:
        """Thresholds violated -> FAIL."""
        decision = RegressionService().evaluate(
            report(results(0, 2)),
            RegressionThresholds(minimum_category_accuracy=0.5),
        )
        assert decision.status == "FAIL"
        assert not decision.passed


class TestPerCaseRegressionTracking:
    """Per-case regression tracking."""

    def test_new_failure_detected(self) -> None:
        """New failure in current run -> new_failure."""
        baseline = report(results(2, 2))
        current = report(results(1, 2))
        comparison = ComparisonService().compare(baseline, current)
        baseline_results = {r.test_case_id: r for r in baseline.results}

        decision = RegressionService().evaluate(
            current, RegressionThresholds(),
            comparison=comparison,
            baseline_results=baseline_results,
        )

        new_failures = [cr for cr in decision.case_regressions if cr.status == "new_failure"]
        assert len(new_failures) == 1

    def test_resolved_failure_detected(self) -> None:
        """Failure fixed in current run -> resolved_failure."""
        baseline = report(results(1, 2))
        current = report(results(2, 2))
        comparison = ComparisonService().compare(baseline, current)
        baseline_results = {r.test_case_id: r for r in baseline.results}

        decision = RegressionService().evaluate(
            current, RegressionThresholds(),
            comparison=comparison,
            baseline_results=baseline_results,
        )

        resolved = [cr for cr in decision.case_regressions if cr.status == "resolved_failure"]
        assert len(resolved) == 1

    def test_unchanged_failure_tracked(self) -> None:
        """Persistent failure -> unchanged_failure."""
        baseline = report(results(0, 2))
        current = report(results(0, 2))
        comparison = ComparisonService().compare(baseline, current)
        baseline_results = {r.test_case_id: r for r in baseline.results}

        decision = RegressionService().evaluate(
            current, RegressionThresholds(),
            comparison=comparison,
            baseline_results=baseline_results,
        )

        unchanged = [cr for cr in decision.case_regressions if cr.status == "unchanged_failure"]
        assert len(unchanged) == 2

    def test_unchanged_pass_tracked(self) -> None:
        """Persistent pass -> unchanged_pass."""
        baseline = report(results(2, 2))
        current = report(results(2, 2))
        comparison = ComparisonService().compare(baseline, current)
        baseline_results = {r.test_case_id: r for r in baseline.results}

        decision = RegressionService().evaluate(
            current, RegressionThresholds(),
            comparison=comparison,
            baseline_results=baseline_results,
        )

        unchanged_pass = [cr for cr in decision.case_regressions if cr.status == "unchanged_pass"]
        assert len(unchanged_pass) == 2


class TestCriticalCaseVeto:
    """Critical case veto overrides aggregate metrics."""

    def test_critical_case_failure_overrides_pass(self) -> None:
        """Critical case regression forces FAIL even if thresholds pass."""
        from promptguard.domain.models import EvaluationResult

        # Create baseline with critical case passing
        baseline_results = [
            EvaluationResult(
                test_case_id="critical-case",
                expected_category="billing",
                actual_category="billing",
                expected_summary="expected",
                actual_summary="actual",
                category_match=True,
                summary_score=5.0,
                latency_ms=10.0,
                input_tokens=10,
                output_tokens=5,
                critical=True,
            ),
            EvaluationResult(
                test_case_id="normal-case",
                expected_category="billing",
                actual_category="billing",
                expected_summary="expected",
                actual_summary="actual",
                category_match=True,
                summary_score=5.0,
                latency_ms=10.0,
                input_tokens=10,
                output_tokens=5,
                critical=False,
            ),
        ]
        baseline = ReportingService().build_report(baseline_results, "v1", "model", "dataset")

        # Current run: critical case fails, normal case passes
        current_results = [
            EvaluationResult(
                test_case_id="critical-case",
                expected_category="billing",
                actual_category="technical",
                expected_summary="expected",
                actual_summary="actual",
                category_match=False,
                summary_score=5.0,
                latency_ms=10.0,
                input_tokens=10,
                output_tokens=5,
                critical=True,
            ),
            EvaluationResult(
                test_case_id="normal-case",
                expected_category="billing",
                actual_category="billing",
                expected_summary="expected",
                actual_summary="actual",
                category_match=True,
                summary_score=5.0,
                latency_ms=10.0,
                input_tokens=10,
                output_tokens=5,
                critical=False,
            ),
        ]
        current = ReportingService().build_report(current_results, "v1", "model", "dataset")

        comparison = ComparisonService().compare(baseline, current)
        baseline_results_dict = {r.test_case_id: r for r in baseline.results}

        decision = RegressionService().evaluate(
            current, RegressionThresholds(minimum_category_accuracy=0.0),
            comparison=comparison,
            baseline_results=baseline_results_dict,
        )

        assert decision.status == "FAIL"
        assert decision.critical_case_failure
        # Check that critical_case_failure violation was added
        critical_violations = [v for v in decision.violations if v.metric == "critical_case_failure"]
        assert len(critical_violations) == 1

    def test_critical_failure_fails_without_a_baseline(self) -> None:
        """Critical failures must not depend on baseline regression tracking."""
        critical_failure = results(1, 1)[0].model_copy(update={
            "test_case_id": "critical-case",
            "actual_category": "technical",
            "normalized_category": "technical",
            "category_match": False,
            "critical": True,
        })
        decision = RegressionService().evaluate(
            report([critical_failure]),
            RegressionThresholds(minimum_category_accuracy=0.0),
        )
        assert decision.status == "FAIL"
        assert decision.critical_case_failure
        assert any(
            violation.metric == "critical_case_failure"
            for violation in decision.violations
        )

    def test_critical_failure_fails_without_any_thresholds(self) -> None:
        """A critical failure is itself sufficient evidence for FAIL."""
        critical_failure = results(1, 1)[0].model_copy(update={
            "test_case_id": "critical-case",
            "actual_category": "technical",
            "normalized_category": "technical",
            "category_match": False,
            "critical": True,
        })
        decision = RegressionService().evaluate(
            report([critical_failure]), RegressionThresholds(),
        )
        assert decision.status == "FAIL"
        assert decision.critical_case_failure
        assert decision.violations[0].metric == "critical_case_failure"


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

    def test_get_rejects_parent_directory_traversal(
        self, tmp_path: Path,
    ) -> None:
        """A run ID must not read outside the configured runs directory."""
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        outside = tmp_path / "outside.json"
        outside.write_text(
            report(results(1, 1)).model_dump_json(), encoding="utf-8",
        )
        repository = JsonEvaluationRunRepository(runs_dir)

        with pytest.raises(ValueError, match="evaluation run ID"):
            repository.get("../outside")

    def test_save_rejects_parent_directory_traversal(
        self, tmp_path: Path,
    ) -> None:
        """A report ID must not write outside the configured runs directory."""
        runs_dir = tmp_path / "runs"
        repository = JsonEvaluationRunRepository(runs_dir)
        saved = report(results(1, 1))
        saved.run_id = "../outside"

        with pytest.raises(ValueError, match="evaluation run ID"):
            repository.save(saved)

        assert not (tmp_path / "outside.json").exists()

    def test_save_rejects_mutated_invalid_report(
        self, tmp_path: Path,
    ) -> None:
        """Persistence must revalidate report evidence before writing."""
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        saved.results[0].actual_summary = "   "

        with pytest.raises(ValidationError):
            repository.save(saved)

        assert not (tmp_path / f"{saved.run_id}.json").exists()

    def test_get_rejects_blank_persisted_run_id(
        self, tmp_path: Path,
    ) -> None:
        """A persisted blank report identifier cannot be loaded."""
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        data = saved.model_dump(mode="json")
        data["run_id"] = "   "
        (tmp_path / "blank.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

        with pytest.raises(ValueError, match="Invalid evaluation run"):
            repository.get("blank")

    def test_get_rejects_embedded_run_id_mismatch(
        self, tmp_path: Path,
    ) -> None:
        """A report's identity must match the filename used to load it."""
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)
        path = tmp_path / f"{saved.run_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["run_id"] = "different-id"
        path.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(ValueError, match="Invalid evaluation run"):
            repository.get(saved.run_id)

    def test_save_rejects_tampered_comparison(
        self, tmp_path: Path,
    ) -> None:
        """Persistence must reject comparison arithmetic that was altered."""
        repository = JsonEvaluationRunRepository(tmp_path)
        baseline = report(results(1, 1))
        current = report(results(1, 1))
        current.baseline_run_id = baseline.run_id
        comparison = ComparisonService().compare(baseline, current)
        comparison.category_accuracy_delta = 0.99
        current.comparison = comparison

        with pytest.raises(ValidationError):
            repository.save(current)

        assert not (tmp_path / f"{current.run_id}.json").exists()

    def test_save_rejects_contradictory_regression(
        self, tmp_path: Path,
    ) -> None:
        """Persistence must reject PASS decisions with failure evidence."""
        repository = JsonEvaluationRunRepository(tmp_path)
        current = report(results(1, 1))
        decision = RegressionService().evaluate(
            current,
            RegressionThresholds(minimum_category_accuracy=0.0),
        )
        decision.violations.append(
            ThresholdViolation(
                metric="category_accuracy",
                actual_value=0.0,
                threshold=1.0,
                direction="below",
                message="bad",
            )
        )
        current.regression = decision

        with pytest.raises(ValidationError):
            repository.save(current)

        assert not (tmp_path / f"{current.run_id}.json").exists()

    def test_corrupted_json_is_skipped_by_list_runs(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        assert repository.list_runs() == []
        assert repository.skipped_runs
        with pytest.raises(ValueError, match="Invalid evaluation run"):
            repository.get("bad")

    def test_save_persists_eval_config_hash(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        saved.eval_config_hash = "fingerprint"
        repository.save(saved)
        loaded = repository.get(saved.run_id)
        assert loaded.eval_config_hash == "fingerprint"

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

    def test_save_persists_gate_thresholds(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        thresholds = RegressionThresholds(minimum_category_accuracy=0.8)
        saved = ReportingService().build_report(
            results(1, 1), "v1", "model", "dataset", thresholds=thresholds,
        )
        repository.save(saved)
        loaded = repository.get(saved.run_id)
        assert loaded.thresholds == thresholds

    def test_legacy_regression_without_status_loads_as_inconclusive(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        data = saved.model_dump(mode="json")
        data["regression"] = {"passed": True, "violations": []}
        (tmp_path / f"{saved.run_id}.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

        loaded = repository.get(saved.run_id)
        assert loaded.regression is not None
        assert loaded.regression.status == "INCONCLUSIVE"
        assert not loaded.regression.passed

    def test_list_runs_skips_malformed_files_and_reports_them(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

        assert [item.run_id for item in repository.list_runs()] == [saved.run_id]
        assert repository.skipped_runs

    def test_get_latest_refuses_malformed_files(self, tmp_path: Path) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

        assert repository.get_latest() is None
        assert any("bad.json" in item for item in repository.skipped_runs)

    def test_save_and_load_preserves_comparison_even_when_pass(
        self, tmp_path: Path,
    ) -> None:
        """Comparison evidence must survive reload even for a passing gate."""
        repository = JsonEvaluationRunRepository(tmp_path)
        baseline = report(results(2, 2))
        current = report(results(2, 2))
        comparison = ComparisonService().compare(baseline, current)
        thresholds = RegressionThresholds(
            minimum_category_accuracy=1.0,
            maximum_category_accuracy_drop=0.0,
        )
        current.baseline_run_id = baseline.run_id
        current.comparison = comparison
        current.thresholds = thresholds
        current.regression = RegressionService().evaluate(
            current, thresholds, comparison=comparison,
        )
        assert current.regression.status == "PASS"

        repository.save(current)
        loaded = repository.get(current.run_id)

        assert loaded.comparison == comparison
        assert loaded.comparison is not None
        assert loaded.comparison.baseline_run_id == baseline.run_id
        assert loaded.comparison.current_run_id == current.run_id
        assert loaded.comparison.category_accuracy_delta == 0.0
        assert loaded.thresholds == thresholds
        assert loaded.regression == current.regression

    def test_legacy_report_without_comparison_loads_none(
        self, tmp_path: Path,
    ) -> None:
        """Older reports must not invent comparison evidence."""
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)

        loaded = repository.get(saved.run_id)

        assert loaded.comparison is None


class TestTimestampPolicy:
    """Timestamp normalization and latest-baseline selection."""

    def test_aware_timestamp_is_normalized_to_utc(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        saved.timestamp = datetime(
            2026, 1, 1, 12, 0,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        )
        repository.save(saved)

        loaded = repository.get(saved.run_id)

        assert loaded.timestamp == datetime(2026, 1, 1, 6, 30, tzinfo=UTC)
        assert loaded.timestamp.utcoffset() == timedelta(0)

    def test_naive_timestamp_is_rejected(self) -> None:
        payload = report(results(1, 1)).model_dump()
        payload["timestamp"] = datetime.fromisoformat("2026-01-01T12:00:00")

        with pytest.raises(ValidationError, match="timezone"):
            EvaluationReport.model_validate(payload)

    def test_missing_timestamp_is_skipped_by_selection(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)
        data = saved.model_dump(mode="json")
        data.pop("timestamp")
        (tmp_path / "missing.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

        assert [item.run_id for item in repository.list_runs()] == [
            saved.run_id,
        ]
        assert any("missing.json" in item for item in repository.skipped_runs)
        assert repository.get_latest() is None

    def test_malformed_timestamp_is_skipped_by_selection(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)
        data = saved.model_dump(mode="json")
        data["timestamp"] = "not-a-timestamp"
        (tmp_path / "malformed.json").write_text(
            json.dumps(data), encoding="utf-8",
        )

        assert [item.run_id for item in repository.list_runs()] == [
            saved.run_id,
        ]
        assert any(
            "malformed.json" in item for item in repository.skipped_runs
        )
        assert repository.get_latest() is None

    def test_equal_timestamps_use_run_id_as_tiebreaker(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        first = report(results(1, 1))
        second = report(results(1, 1))
        timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        first.run_id = "a"
        second.run_id = "b"
        first.timestamp = timestamp
        second.timestamp = timestamp
        repository.save(first)
        repository.save(second)

        latest = repository.get_latest()

        assert latest is not None
        assert latest.run_id == "b"

    def test_different_utc_offsets_sort_after_normalization(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        earlier = report(results(1, 1))
        later = report(results(1, 1))
        earlier.run_id = "earlier"
        later.run_id = "later"
        earlier.timestamp = datetime(
            2026, 1, 1, 10, 0,
            tzinfo=timezone(timedelta(hours=5, minutes=30)),
        )
        later.timestamp = datetime(2026, 1, 1, 5, 0, tzinfo=UTC)
        repository.save(earlier)
        repository.save(later)

        latest = repository.get_latest()

        assert latest is not None
        assert latest.run_id == "later"

    def test_mixed_legacy_naive_and_current_aware_skips_naive(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        current = report(results(1, 1))
        current.timestamp = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        repository.save(current)
        legacy_data = report(results(1, 1)).model_dump(mode="json")
        legacy_data["timestamp"] = "2026-01-02T12:00:00"
        (tmp_path / "legacy.json").write_text(
            json.dumps(legacy_data), encoding="utf-8",
        )

        runs = repository.list_runs()

        assert [item.run_id for item in runs] == [current.run_id]
        assert any("legacy.json" in item for item in repository.skipped_runs)
        assert repository.get_latest() is None


class TestLatestBaselineIntegrity:
    """Latest-baseline selection must not hide corrupted persisted runs."""

    def test_latest_refuses_mixed_valid_and_invalid_runs(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

        assert repository.get_latest() is None
        assert any("bad.json" in item for item in repository.skipped_runs)

    def test_latest_returns_none_when_no_valid_run_exists(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")

        assert repository.get_latest() is None
        assert any("bad.json" in item for item in repository.skipped_runs)

    def test_explicit_id_loads_valid_report_with_invalid_sibling(
        self, tmp_path: Path,
    ) -> None:
        repository = JsonEvaluationRunRepository(tmp_path)
        saved = report(results(1, 1))
        repository.save(saved)
        (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
        repository.list_runs()

        loaded = repository.get(saved.run_id)

        assert loaded.run_id == saved.run_id
