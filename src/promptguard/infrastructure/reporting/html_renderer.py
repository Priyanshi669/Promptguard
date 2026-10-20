"""Human-readable text report formatter."""

from promptguard.application.contracts import ReportFormatter
from promptguard.domain.models import EvaluationReport


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
        if report.baseline_run_id is not None:
            lines.append(f"Baseline run: {report.baseline_run_id}")
        if report.drift is not None:
            drift = report.drift
            lines.append(self._drift_line(drift.sufficient_data, drift.total_variation_distance))
        self._regression_lines(lines, report)
        self._failure_lines(lines, report)
        return "\n".join(lines)

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
        status = "PASS" if report.regression.passed else "FAIL"
        lines.append(f"Regression status: {status}")
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
