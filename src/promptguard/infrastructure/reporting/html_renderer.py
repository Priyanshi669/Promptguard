"""Human-readable text report formatter."""

from promptguard.application.contracts import ReportFormatter
from promptguard.domain.models import EvaluationReport
from promptguard.domain.regression import EvaluationComparison


class TextReportFormatter(ReportFormatter):
    """Render a complete evaluation report for console output."""

    def format(self, report: EvaluationReport) -> str:
        """Return metrics, threshold status, drift, and individual failures."""
        lines = [
            "PromptGuard Evaluation Report",
            f"Run ID: {report.run_id}",
            f"Timestamp: {report.timestamp.isoformat()}",
            f"Prompt version: {report.prompt_version}",
            f"Model: {report.model}",
            f"Dataset: {report.dataset}",
            f"Cases evaluated: {report.case_count}",
            f"Category accuracy: {report.category_accuracy:.2%}",
            f"Average summary score: {report.average_summary_score:.2f}/5",
            f"Average latency: {report.average_latency_ms:.2f}ms",
            f"Input tokens: {report.total_input_tokens}",
            f"Output tokens: {report.total_output_tokens}",
        ]
        if report.eval_config_hash is not None:
            lines.append(f"Eval config hash: {report.eval_config_hash}")
        if report.thresholds is not None:
            lines.append("Gate thresholds:")
            for name, value in report.thresholds.model_dump(
                exclude_none=True,
            ).items():
                lines.append(f"  {name}: {value}")
        if report.baseline_run_id is not None:
            lines.append(f"Baseline run: {report.baseline_run_id}")
        if report.comparison is not None:
            self._comparison_lines(lines, report.comparison)
        if report.drift is not None:
            drift = report.drift
            lines.append(self._drift_line(drift.sufficient_data, drift.total_variation_distance))
        self._regression_lines(lines, report)
        self._failure_lines(lines, report)
        return "\n".join(lines)

    @staticmethod
    def _comparison_lines(
        lines: list[str], comparison: EvaluationComparison,
    ) -> None:
        """Append persisted baseline/candidate deltas."""
        lines.extend([
            "Comparison deltas:",
            (
                f"  Category accuracy: {comparison.category_accuracy_delta:+.2%} "
                f"(baseline {comparison.baseline_category_accuracy:.2%}, "
                f"current {comparison.current_category_accuracy:.2%})"
            ),
            (
                f"  Average summary score: {comparison.summary_score_delta:+.2f} "
                f"(baseline {comparison.baseline_summary_score:.2f}, "
                f"current {comparison.current_summary_score:.2f})"
            ),
            (
                f"  Average latency: {comparison.latency_delta_ms:+.2f}ms "
                f"(baseline {comparison.baseline_latency_ms:.2f}ms, "
                f"current {comparison.current_latency_ms:.2f}ms)"
            ),
            (
                f"  Input tokens: {comparison.input_token_delta:+d} "
                f"(baseline {comparison.baseline_input_tokens}, "
                f"current {comparison.current_input_tokens})"
            ),
            (
                f"  Output tokens: {comparison.output_token_delta:+d} "
                f"(baseline {comparison.baseline_output_tokens}, "
                f"current {comparison.current_output_tokens})"
            ),
        ])

    @staticmethod
    def _drift_line(sufficient_data: bool, distance: float | None) -> str:
        """Describe the observed category drift between runs."""
        if not sufficient_data or distance is None:
            return "Category drift: insufficient data"
        return f"Category drift (total variation): {distance:.4f}"

    @staticmethod
    def _regression_lines(lines: list[str], report: EvaluationReport) -> None:
        """Append the regression decision and any threshold violations."""
        if report.regression is None:
            lines.append("Regression status: not evaluated")
            return
        lines.append(f"Regression status: {report.regression.status}")
        if report.regression.incompatibility_reasons:
            for reason in report.regression.incompatibility_reasons:
                lines.append(f"  Incompatibility: {reason}")
        if not report.regression.violations:
            lines.append("Threshold violations: none")
            return
        lines.append("Threshold violations:")
        for violation in report.regression.violations:
            lines.append(
                f"  {violation.metric}: {violation.message} "
                f"(actual={violation.actual_value:.4f}, "
                f"threshold={violation.threshold:.4f})"
            )

    @staticmethod
    def _failure_lines(lines: list[str], report: EvaluationReport) -> None:
        """Append enough detail to debug every category mismatch."""
        failures = [
            result for result in report.results if not result.category_match
        ]
        lines.append(f"Individual failures: {len(failures)}")
        for result in failures:
            lines.extend([
                f"  Test case: {result.test_case_id}",
                f"    Expected category: {result.expected_category}",
                f"    Actual category: {result.actual_category}",
                f"    Expected summary: {result.expected_summary}",
                f"    Actual summary: {result.actual_summary}",
                f"    Summary score: {result.summary_score:.2f}",
            ])
