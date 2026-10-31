"""Human-readable text report formatter."""

from promptguard.application.contracts import ReportFormatter
from promptguard.domain.classification import ClassificationMetrics
from promptguard.domain.models import EvaluationReport
from promptguard.domain.paired import PairedAnalysis
from promptguard.domain.regression import EvaluationComparison
from promptguard.domain.statistics import ConfidenceIntervals


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
        if report.total_cost_usd is not None:
            lines.append(f"Total cost: ${report.total_cost_usd:.4f}")
        if report.classification is not None:
            self._classification_lines(lines, report.classification)
        if report.confidence_intervals is not None:
            self._confidence_lines(lines, report.confidence_intervals)
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
        if report.paired_analysis is not None:
            self._paired_lines(lines, report.paired_analysis)
        if report.drift is not None:
            drift = report.drift
            lines.append(self._drift_line(drift.sufficient_data, drift.total_variation_distance))
        self._regression_lines(lines, report)
        self._failure_lines(lines, report)
        return "\n".join(lines)

    MAX_RENDERED_CONFUSION_LABELS = 8

    @classmethod
    def _classification_lines(
        cls,
        lines: list[str],
        classification: "ClassificationMetrics",
    ) -> None:
        """Append per-class evidence; wide matrices stay in the JSON report."""
        lines.extend([
            "Classification metrics:",
            (
                f"  Macro F1: {classification.macro_f1:.4f} "
                f"(precision {classification.macro_precision:.4f}, "
                f"recall {classification.macro_recall:.4f})"
            ),
            (
                f"  Weighted F1: {classification.weighted_f1:.4f} "
                f"(precision {classification.weighted_precision:.4f}, "
                f"recall {classification.weighted_recall:.4f})"
            ),
            f"  Accuracy (micro): {classification.accuracy:.4f}",
        ])
        labels = sorted(classification.per_class)
        if labels:
            lines.append("  Per-class (precision/recall/F1/support):")
            for label in labels:
                metrics = classification.per_class[label]
                lines.append(
                    f"    {label}: {metrics.precision:.4f}/"
                    f"{metrics.recall:.4f}/{metrics.f1:.4f}/"
                    f"{metrics.support}"
                )
        if 0 < len(labels) <= cls.MAX_RENDERED_CONFUSION_LABELS:
            lines.append("  Confusion matrix (expected -> actual):")
            header = "    " + " | ".join(
                [""] + list(classification.confusion[labels[0]])
            )
            lines.append(header)
            for expected in labels:
                row = " | ".join(
                    str(classification.confusion[expected][actual])
                    for actual in labels
                )
                lines.append(f"    {expected} | {row}")
        elif len(labels) > cls.MAX_RENDERED_CONFUSION_LABELS:
            lines.append(
                "  Confusion matrix: omitted from console output "
                f"({len(labels)} labels); see the persisted JSON report."
            )

    @staticmethod
    def _confidence_lines(
        lines: list[str],
        intervals: ConfidenceIntervals,
    ) -> None:
        """Append bootstrap uncertainty; gates never consume these values."""
        lines.extend([
            (
                "Statistical confidence "
                f"({intervals.method}, {intervals.confidence_level:.0%}, "
                f"{intervals.resamples} resamples, seed {intervals.seed}):"
            ),
        ])
        accuracy = intervals.accuracy_interval
        if accuracy is not None:
            lines.append(
                f"  Category accuracy: {accuracy.point_estimate:.2%} "
                f"(interval {accuracy.lower_bound:.2%}-"
                f"{accuracy.upper_bound:.2%})"
            )
        summary = intervals.summary_score_interval
        if summary is not None:
            lines.append(
                f"  Mean summary score: {summary.point_estimate:.2f} "
                f"(interval {summary.lower_bound:.2f}-"
                f"{summary.upper_bound:.2f})"
            )
        if intervals.small_sample:
            lines.append(
                "  Warning: small sample; these intervals are uncertain."
            )
        lines.append(
            "  Note: intervals quantify sampling uncertainty only; they do "
            "not validate dataset representativeness or judge agreement."
        )

    @staticmethod
    def _paired_lines(
        lines: list[str],
        paired: PairedAnalysis,
    ) -> None:
        """Append paired baseline evidence; gates never consume it."""
        if paired.unavailable_reason is not None:
            lines.append(
                f"Paired baseline analysis: unavailable "
                f"({paired.unavailable_reason})"
            )
            return
        lines.extend([
            (
                f"Paired baseline analysis ({paired.matched_cases} "
                "matched cases):"
            ),
            (
                f"  Regressions (baseline only correct): "
                f"{paired.baseline_only_correct}"
            ),
            (
                f"  Fixes (candidate only correct): "
                f"{paired.candidate_only_correct}"
            ),
            (
                f"  Both correct: {paired.both_correct} | "
                f"Both incorrect: {paired.both_incorrect}"
            ),
        ])
        if paired.mcnemar_p_value is None:
            lines.append(
                "  Exact McNemar p-value: uninformative "
                "(no discordant cases)"
            )
        else:
            lines.append(
                f"  Exact McNemar p-value: "
                f"{paired.mcnemar_p_value:.4f}"
            )
        interval = paired.summary_change_interval
        if interval is not None:
            sign = "+" if interval.point_estimate >= 0 else ""
            lower_sign = "+" if interval.lower_bound >= 0 else ""
            upper_sign = "+" if interval.upper_bound >= 0 else ""
            lines.append(
                f"  Mean summary-score change: {sign}"
                f"{interval.point_estimate:.2f} "
                f"(95% interval {lower_sign}{interval.lower_bound:.2f} to "
                f"{upper_sign}{interval.upper_bound:.2f})"
            )
        else:
            lines.append(
                "  Mean summary-score change: unavailable "
                "(no bootstrap configuration)"
            )
        lines.append(
            "  Note: paired statistics are informational; release gates "
            "are unaffected."
        )

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
