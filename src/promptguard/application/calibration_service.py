"""Application service for calibrating the summary judge."""

import math
from typing import Any

from promptguard.domain.calibration import (
    CalibrationDataset,
    CalibrationItem,
    CalibrationMetrics,
    CalibrationThresholds,
    JudgeCalibrationReport,
)
from promptguard.domain.metrics import SummaryMetric


class JudgeCalibrationService:
    """Measure how closely judge scores agree with human labels."""

    async def calibrate(
        self,
        dataset: CalibrationDataset,
        summary_metric: SummaryMetric,
        evaluator: dict[str, Any],
        thresholds: CalibrationThresholds | None = None,
    ) -> JudgeCalibrationReport:
        """Score every example and return agreement and bias evidence."""
        if not evaluator:
            raise ValueError("Calibration requires evaluator identity.")
        if (
            evaluator.get("judge_rubric_version")
            != dataset.metadata.rubric_version
        ):
            raise ValueError(
                "Calibration dataset rubric version does not match the "
                "evaluator rubric version."
            )
        if (
            evaluator.get("judge_rubric_sha256")
            != dataset.metadata.rubric_sha256
        ):
            raise ValueError(
                "Calibration dataset rubric hash does not match the "
                "evaluator rubric hash."
            )

        selected_thresholds = thresholds or CalibrationThresholds()
        items: list[CalibrationItem] = []
        for example in dataset.examples:
            judge_score = await summary_metric.score(
                expected_summary=example.expected_summary,
                actual_summary=example.actual_summary,
            )
            if (
                not isinstance(judge_score, (int, float))
                or not math.isfinite(judge_score)
                or not 1 <= judge_score <= 5
            ):
                raise ValueError(
                    "Judge returned an invalid score for calibration case "
                    f"'{example.id}': {judge_score!r}"
                )

            items.append(
                CalibrationItem(
                    test_case_id=example.id,
                    human_score=example.human_score,
                    judge_score=float(judge_score),
                    absolute_error=abs(float(judge_score) - example.human_score),
                )
            )

        metrics = self._metrics(items)
        violations = self._violations(metrics, selected_thresholds)
        return JudgeCalibrationReport(
            sample_count=len(items),
            metrics=metrics,
            thresholds=selected_thresholds,
            dataset=dataset.identity(),
            evaluator=dict(evaluator),
            items=items,
            violations=violations,
            passed=not violations,
        )

    @classmethod
    def _metrics(cls, items: list[CalibrationItem]) -> CalibrationMetrics:
        """Compute agreement, correlation, and bias statistics."""
        human_scores = [item.human_score for item in items]
        judge_scores = [item.judge_score for item in items]
        differences = [
            judge_score - human_score
            for judge_score, human_score in zip(
                judge_scores, human_scores, strict=True,
            )
        ]
        sample_count = len(items)

        return CalibrationMetrics(
            sample_count=sample_count,
            mean_human_score=sum(human_scores) / sample_count,
            mean_judge_score=sum(judge_scores) / sample_count,
            mean_absolute_error=(
                sum(abs(difference) for difference in differences)
                / sample_count
            ),
            root_mean_squared_error=math.sqrt(
                sum(difference**2 for difference in differences)
                / sample_count
            ),
            pearson_correlation=cls._pearson(human_scores, judge_scores),
            spearman_correlation=cls._pearson(
                cls._ranks(human_scores),
                cls._ranks(judge_scores),
            ),
            exact_agreement=(
                sum(
                    cls._round_score(judge_score)
                    == cls._round_score(human_score)
                    for judge_score, human_score in zip(
                        judge_scores, human_scores, strict=True,
                    )
                )
                / sample_count
            ),
            within_one_agreement=(
                sum(abs(difference) <= 1.0 for difference in differences)
                / sample_count
            ),
            mean_bias=sum(differences) / sample_count,
        )

    @staticmethod
    def _violations(
        metrics: CalibrationMetrics,
        thresholds: CalibrationThresholds,
    ) -> list[str]:
        """Return every calibration acceptance failure."""
        violations: list[str] = []
        if metrics.sample_count < thresholds.minimum_samples:
            violations.append(
                f"Calibration requires at least {thresholds.minimum_samples} "
                f"examples; found {metrics.sample_count}."
            )
        if metrics.mean_absolute_error > thresholds.maximum_mae:
            violations.append(
                f"Mean absolute error {metrics.mean_absolute_error:.4f} "
                f"exceeds {thresholds.maximum_mae:.4f}."
            )
        if metrics.pearson_correlation is None:
            violations.append(
                "Pearson correlation is undefined because the scores have "
                "no variance."
            )
        elif metrics.pearson_correlation < thresholds.minimum_pearson:
            violations.append(
                f"Pearson correlation {metrics.pearson_correlation:.4f} "
                f"is below {thresholds.minimum_pearson:.4f}."
            )
        if (
            metrics.within_one_agreement
            < thresholds.minimum_within_one_agreement
        ):
            violations.append(
                f"Within-one agreement {metrics.within_one_agreement:.4f} "
                f"is below {thresholds.minimum_within_one_agreement:.4f}."
            )
        if abs(metrics.mean_bias) > thresholds.maximum_absolute_bias:
            violations.append(
                f"Absolute mean bias {abs(metrics.mean_bias):.4f} "
                f"exceeds {thresholds.maximum_absolute_bias:.4f}."
            )
        return violations

    @staticmethod
    def _round_score(score: float) -> int:
        """Round a 1-5 score to the nearest integer for agreement checks."""
        return math.floor(score + 0.5)

    @staticmethod
    def _ranks(values: list[float]) -> list[float]:
        """Return average ranks, assigning equal ranks to tied values."""
        order = sorted(range(len(values)), key=lambda index: values[index])
        ranks = [0.0] * len(values)
        start = 0
        while start < len(order):
            end = start + 1
            while (
                end < len(order)
                and values[order[end]] == values[order[start]]
            ):
                end += 1
            average_rank = (start + 1 + end) / 2.0
            for index in order[start:end]:
                ranks[index] = average_rank
            start = end
        return ranks

    @staticmethod
    def _pearson(xs: list[float], ys: list[float]) -> float | None:
        """Return Pearson correlation, or None when it is undefined."""
        if len(xs) < 2 or len(xs) != len(ys):
            return None
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        covariance = sum(
            (x - mean_x) * (y - mean_y)
            for x, y in zip(xs, ys, strict=True)
        )
        variance_x = sum((x - mean_x) ** 2 for x in xs)
        variance_y = sum((y - mean_y) ** 2 for y in ys)
        denominator = math.sqrt(variance_x * variance_y)
        if denominator == 0:
            return None
        return max(-1.0, min(1.0, covariance / denominator))
