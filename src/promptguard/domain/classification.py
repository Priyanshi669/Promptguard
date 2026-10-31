"""Multi-class classification metrics computed from evaluation results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from promptguard.domain.models import EvaluationResult


def _label(result: EvaluationResult, expected: bool) -> str:
    """Return the normalized label for the expected or actual category."""
    if expected:
        return result.expected_category.strip().lower()
    normalized = result.normalized_category or result.actual_category
    return normalized.strip().lower()


class ClassMetrics(BaseModel):
    """Per-class precision, recall, F1, and support.

    Precision and recall are 0.0 when their denominators are zero; F1 is
    0.0 when precision and recall are both zero. This matches the common
    zero-division convention and never fabricates evidence.
    """

    support: int = Field(ge=0)
    tp: int = Field(ge=0)
    fp: int = Field(ge=0)
    fn: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)


class ClassificationMetrics(BaseModel):
    """Aggregated multi-class classification evidence for one run."""

    total_cases: int = Field(ge=0)
    accuracy: float = Field(ge=0, le=1)
    macro_precision: float = Field(ge=0, le=1)
    macro_recall: float = Field(ge=0, le=1)
    macro_f1: float = Field(ge=0, le=1)
    weighted_precision: float = Field(ge=0, le=1)
    weighted_recall: float = Field(ge=0, le=1)
    weighted_f1: float = Field(ge=0, le=1)
    per_class: dict[str, ClassMetrics] = Field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = Field(default_factory=dict)


def classification_metrics(
    results: list[EvaluationResult],
) -> ClassificationMetrics:
    """Compute precision, recall, F1, support, and the confusion matrix.

    Labels are the normalized expected and actual categories. Weighted
    aggregates use per-class support as weights; micro precision, recall,
    and F1 all equal accuracy for single-label classification and are
    intentionally not stored separately.
    """
    labels: set[str] = set()
    for result in results:
        labels.add(_label(result, expected=True))
        labels.add(_label(result, expected=False))

    tp: dict[str, int] = dict.fromkeys(labels, 0)
    fp: dict[str, int] = dict.fromkeys(labels, 0)
    fn: dict[str, int] = dict.fromkeys(labels, 0)
    confusion: dict[str, dict[str, int]] = {
        expected: dict.fromkeys(sorted(labels), 0) for expected in sorted(labels)
    }

    for result in results:
        expected_label = _label(result, expected=True)
        actual_label = _label(result, expected=False)
        confusion[expected_label][actual_label] += 1
        if expected_label == actual_label:
            tp[expected_label] += 1
        else:
            fp[actual_label] += 1
            fn[expected_label] += 1

    per_class: dict[str, ClassMetrics] = {}
    for label in sorted(labels):
        precision = (
            tp[label] / (tp[label] + fp[label]) if tp[label] + fp[label] else 0.0
        )
        recall = (
            tp[label] / (tp[label] + fn[label]) if tp[label] + fn[label] else 0.0
        )
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        per_class[label] = ClassMetrics(
            support=tp[label] + fn[label],
            tp=tp[label],
            fp=fp[label],
            fn=fn[label],
            precision=precision,
            recall=recall,
            f1=f1,
        )

    total = len(results)
    correct = sum(tp.values())
    accuracy = correct / total if total else 0.0

    macro_precision = (
        sum(metrics.precision for metrics in per_class.values()) / len(per_class)
        if per_class
        else 0.0
    )
    macro_recall = (
        sum(metrics.recall for metrics in per_class.values()) / len(per_class)
        if per_class
        else 0.0
    )
    macro_f1 = (
        sum(metrics.f1 for metrics in per_class.values()) / len(per_class)
        if per_class
        else 0.0
    )
    weighted_precision = (
        sum(
            metrics.precision * metrics.support
            for metrics in per_class.values()
        )
        / total
        if total
        else 0.0
    )
    weighted_recall = (
        sum(
            metrics.recall * metrics.support
            for metrics in per_class.values()
        )
        / total
        if total
        else 0.0
    )
    weighted_f1 = (
        sum(metrics.f1 * metrics.support for metrics in per_class.values())
        / total
        if total
        else 0.0
    )

    return ClassificationMetrics(
        total_cases=total,
        accuracy=accuracy,
        macro_precision=macro_precision,
        macro_recall=macro_recall,
        macro_f1=macro_f1,
        weighted_precision=weighted_precision,
        weighted_recall=weighted_recall,
        weighted_f1=weighted_f1,
        per_class=per_class,
        confusion=confusion,
    )
