"""Compare reports and evaluate explicit regression thresholds."""

from promptguard.domain.models import EvaluationReport
from promptguard.domain.regression import EvaluationComparison, RegressionResult
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
    ) -> RegressionResult:
        """Return every applicable threshold violation."""
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
        if comparison:
            self._maximum(
                violations, "category_accuracy_drop",
                -comparison.category_accuracy_delta,
                thresholds.maximum_category_accuracy_drop,
            )
            self._maximum(
                violations, "summary_score_drop", -comparison.summary_score_delta,
                thresholds.maximum_summary_score_drop,
            )
        return RegressionResult(passed=not violations, violations=violations)

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
