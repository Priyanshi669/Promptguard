"""Compare reports and evaluate explicit regression thresholds."""

from typing import Literal

from promptguard.domain.models import EvaluationReport, EvaluationResult
from promptguard.domain.regression import (
    CaseRegressionStatus,
    EvaluationComparison,
    RegressionResult,
)
from promptguard.domain.thresholds import RegressionThresholds, ThresholdViolation


class ComparisonService:
    """Calculate factual deltas between two complete evaluation reports."""

    def compare(
        self,
        baseline: EvaluationReport,
        current: EvaluationReport,
    ) -> EvaluationComparison:
        """Return current-minus-baseline metric deltas."""
        return EvaluationComparison(
            baseline_run_id=baseline.run_id,
            current_run_id=current.run_id,
            baseline_category_accuracy=baseline.category_accuracy,
            current_category_accuracy=current.category_accuracy,
            category_accuracy_delta=(
                current.category_accuracy - baseline.category_accuracy
            ),
            baseline_summary_score=baseline.average_summary_score,
            current_summary_score=current.average_summary_score,
            summary_score_delta=(
                current.average_summary_score - baseline.average_summary_score
            ),
            baseline_latency_ms=baseline.average_latency_ms,
            current_latency_ms=current.average_latency_ms,
            latency_delta_ms=current.average_latency_ms - baseline.average_latency_ms,
            baseline_input_tokens=baseline.total_input_tokens,
            current_input_tokens=current.total_input_tokens,
            input_token_delta=current.total_input_tokens - baseline.total_input_tokens,
            baseline_output_tokens=baseline.total_output_tokens,
            current_output_tokens=current.total_output_tokens,
            output_token_delta=(
                current.total_output_tokens - baseline.total_output_tokens
            ),
        )


class RegressionService:
    """Evaluate configured quality and latency thresholds."""

    def evaluate(
        self,
        report: EvaluationReport,
        thresholds: RegressionThresholds,
        comparison: EvaluationComparison | None = None,
        baseline_results: dict[str, EvaluationResult] | None = None,
    ) -> RegressionResult:
        """Return every applicable threshold violation."""
        evidence_reasons = self._evidence_reasons(report)
        if evidence_reasons:
            return RegressionResult(
                status="INCONCLUSIVE",
                violations=[],
                case_regressions=[],
                incompatibility_reasons=evidence_reasons,
            )

        # Check if any absolute thresholds are configured
        has_absolute_thresholds = (
            thresholds.minimum_category_accuracy is not None
            or thresholds.minimum_summary_score is not None
            or thresholds.maximum_average_latency_ms is not None
        )

        # Check if any drop thresholds are configured (require comparison)
        has_drop_thresholds = (
            thresholds.maximum_category_accuracy_drop is not None
            or thresholds.maximum_summary_score_drop is not None
            or thresholds.maximum_category_accuracy_drop_pct is not None
            or thresholds.maximum_summary_score_drop_pct is not None
        )

        # Compute per-case regressions if baseline results provided
        case_regressions: list[CaseRegressionStatus] = []
        if baseline_results is not None:
            case_regressions = self._compute_case_regressions(
                baseline_results, report.results
            )

        critical_case_failure = any(
            result.critical and not result.category_match
            for result in report.results
        )

        if not has_absolute_thresholds and not has_drop_thresholds:
            if critical_case_failure:
                return RegressionResult(
                    status="FAIL",
                    violations=[self._critical_violation()],
                    case_regressions=case_regressions,
                    critical_case_failure=True,
                )
            reason = "No thresholds configured"
            if comparison is None:
                reason += " and no baseline provided"
            else:
                reason += " (baseline provided but no drop thresholds set)"
            return RegressionResult(
                status="INCONCLUSIVE",
                violations=[],
                case_regressions=case_regressions,
                incompatibility_reasons=[reason],
            )

        violations: list[ThresholdViolation] = []
        self._minimum(
            violations, "category_accuracy", report.category_accuracy,
            thresholds.minimum_category_accuracy,
        )
        self._minimum(
            violations, "average_summary_score", report.average_summary_score,
            thresholds.minimum_summary_score,
        )
        self._maximum(
            violations, "average_latency_ms", report.average_latency_ms,
            thresholds.maximum_average_latency_ms,
        )
        if comparison and has_drop_thresholds:
            self._maximum(
                violations, "category_accuracy_drop",
                -comparison.category_accuracy_delta,
                thresholds.maximum_category_accuracy_drop,
            )
            self._maximum(
                violations, "summary_score_drop", -comparison.summary_score_delta,
                thresholds.maximum_summary_score_drop,
            )
            self._maximum(
                violations, "category_accuracy_drop_pct",
                self._relative_drop(
                    comparison.baseline_category_accuracy,
                    comparison.current_category_accuracy,
                ) * 100,
                thresholds.maximum_category_accuracy_drop_pct,
            )
            self._maximum(
                violations, "summary_score_drop_pct",
                self._relative_drop(
                    comparison.baseline_summary_score,
                    comparison.current_summary_score,
                ) * 100,
                thresholds.maximum_summary_score_drop_pct,
            )

        if critical_case_failure:
            violations.append(self._critical_violation())

        if violations:
            return RegressionResult(
                status="FAIL",
                violations=violations,
                case_regressions=case_regressions,
                critical_case_failure=critical_case_failure,
            )

        if has_drop_thresholds and comparison is None:
            return RegressionResult(
                status="INCONCLUSIVE",
                violations=[],
                case_regressions=case_regressions,
                incompatibility_reasons=[
                    "Drop thresholds configured but no baseline provided"
                ],
            )

        return RegressionResult(
            status="PASS",
            violations=[],
            case_regressions=case_regressions,
            critical_case_failure=critical_case_failure,
        )

    def _compute_case_regressions(
        self,
        baseline_results: dict[str, EvaluationResult],
        current_results: list[EvaluationResult],
    ) -> list[CaseRegressionStatus]:
        """Compute per-case regression status by comparing baseline and current results."""
        current_by_id = {r.test_case_id: r for r in current_results}
        regressions: list[CaseRegressionStatus] = []

        for test_case_id, baseline_result in baseline_results.items():
            current_result = current_by_id.get(test_case_id)
            if current_result is None:
                # Case missing in current run
                regressions.append(CaseRegressionStatus(
                    test_case_id=test_case_id,
                    baseline_category_match=baseline_result.category_match,
                    current_category_match=None,
                    status="unknown",
                ))
                continue

            baseline_match = baseline_result.category_match
            current_match = current_result.category_match

            status: Literal["new_failure", "resolved_failure", "unchanged_failure", "unchanged_pass", "unknown"]
            if baseline_match and not current_match:
                status = "new_failure"
            elif not baseline_match and current_match:
                status = "resolved_failure"
            elif not baseline_match and not current_match:
                status = "unchanged_failure"
            else:
                status = "unchanged_pass"

            regressions.append(CaseRegressionStatus(
                test_case_id=test_case_id,
                baseline_category_match=baseline_match,
                current_category_match=current_match,
                status=status,
            ))

        return regressions

    @staticmethod
    def _evidence_reasons(report: EvaluationReport) -> list[str]:
        """Return evidence problems using the report's domain invariants."""
        return report.evidence_issues()

    @staticmethod
    def _critical_violation() -> ThresholdViolation:
        """Return the violation recorded for any critical-case failure."""
        return ThresholdViolation(
            metric="critical_case_failure",
            actual_value=1.0,
            threshold=0.0,
            direction="above",
            message="Critical test case failure detected.",
        )

    @staticmethod
    def _relative_drop(baseline: float, current: float) -> float:
        """Return the proportional drop from baseline as a 0-1 fraction."""
        if baseline <= 0:
            return 0.0
        return max(0.0, (baseline - current) / baseline)

    @staticmethod
    def _minimum(
        violations: list[ThresholdViolation], metric: str, actual: float,
        threshold: float | None,
    ) -> None:
        if threshold is not None and actual < threshold:
            violations.append(ThresholdViolation(
                metric=metric, actual_value=actual, threshold=threshold,
                direction="below", message=f"{metric} is below its minimum.",
            ))

    @staticmethod
    def _maximum(
        violations: list[ThresholdViolation], metric: str, actual: float,
        threshold: float | None,
    ) -> None:
        if threshold is not None and actual > threshold:
            violations.append(ThresholdViolation(
                metric=metric, actual_value=actual, threshold=threshold,
                direction="above", message=f"{metric} exceeds its maximum.",
            ))