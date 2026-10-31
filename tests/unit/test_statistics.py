"""Unit tests for seeded percentile-bootstrap intervals.

Point estimates and interval locations are verified against hand
calculations; reproducibility and invalid-input safeguards are exercised
directly.
"""

import math

import pytest
from pydantic import ValidationError

from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import EvaluationReport, EvaluationResult
from promptguard.domain.statistics import (
    BootstrapConfig,
    bootstrap_interval,
    confidence_intervals,
)
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.infrastructure.reporting.html_renderer import (
    TextReportFormatter,
)

_seq = iter(range(10000))


def config(
    seed: int = 20261026,
    resamples: int = 2000,
    confidence_level: float = 0.95,
) -> BootstrapConfig:
    """Return bootstrap parameters with overridable fields."""
    return BootstrapConfig(
        seed=seed, resamples=resamples, confidence_level=confidence_level,
    )


def result(
    expected: str,
    actual: str,
    summary_score: float,
) -> EvaluationResult:
    """Build one evaluation result with controlled case evidence."""
    return EvaluationResult(
        test_case_id=f"case-{next(_seq)}",
        expected_category=expected,
        actual_category=actual,
        normalized_category=actual,
        expected_summary="ideal summary",
        actual_summary="actual summary",
        category_match=actual.strip().lower() == expected.strip().lower(),
        summary_score=summary_score,
        latency_ms=100.0,
        input_tokens=1,
        output_tokens=1,
    )


def test_identical_inputs_and_seed_are_reproducible() -> None:
    values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]

    first = bootstrap_interval(values, config(seed=7, resamples=500))
    second = bootstrap_interval(values, config(seed=7, resamples=500))

    assert first == second


def test_different_seed_can_shift_bounds() -> None:
    values = [1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 1.0]

    first = bootstrap_interval(values, config(seed=7, resamples=200))
    second = bootstrap_interval(values, config(seed=8, resamples=200))

    assert (first.lower_bound, first.upper_bound) != (
        second.lower_bound, second.upper_bound,
    )


def test_empty_sample_is_unavailable_not_zero() -> None:
    assert bootstrap_interval([], config()) is None


def test_invalid_parameters_are_rejected() -> None:
    with pytest.raises(ValidationError):
        config(resamples=0)
    with pytest.raises(ValidationError):
        config(resamples=100001)
    with pytest.raises(ValidationError):
        config(confidence_level=0.0)
    with pytest.raises(ValidationError):
        config(confidence_level=1.0)

    with pytest.raises(ValueError, match="finite"):
        bootstrap_interval([1.0, float("nan")], config())
    with pytest.raises(ValueError, match="finite"):
        bootstrap_interval([1.0, float("inf")], config())


def test_single_case_yields_collapsed_interval() -> None:
    estimate = bootstrap_interval([4.2], config(seed=3, resamples=100))

    assert estimate is not None
    assert estimate.point_estimate == pytest.approx(4.2)
    assert estimate.lower_bound == pytest.approx(4.2)
    assert estimate.upper_bound == pytest.approx(4.2)
    assert estimate.sample_count == 1


def test_constant_sample_collapses_at_the_boundary() -> None:
    for constant in (1.0, 5.0):
        estimate = bootstrap_interval(
            [constant] * 10, config(seed=11, resamples=500),
        )

        assert estimate is not None
        assert estimate.point_estimate == pytest.approx(constant)
        assert estimate.lower_bound == pytest.approx(constant)
        assert estimate.upper_bound == pytest.approx(constant)


def test_bounds_are_ordered_and_within_sample_range() -> None:
    values = [1.0, 5.0, 2.5, 4.0, 1.5, 3.5, 2.0, 4.5, 3.0, 5.0, 1.0, 2.0]

    estimate = bootstrap_interval(values, config(seed=23, resamples=2000))

    assert estimate is not None
    assert estimate.lower_bound <= estimate.upper_bound
    assert min(values) <= estimate.lower_bound
    assert estimate.upper_bound <= max(values)
    assert estimate.point_estimate == pytest.approx(sum(values) / len(values))


def test_interval_narrows_as_sample_grows() -> None:
    small = bootstrap_interval([0.0, 1.0, 1.0, 0.0], config(seed=5))
    large = bootstrap_interval(
        [0.0, 1.0] * 100, config(seed=5),
    )

    assert small is not None and large is not None
    small_width = small.upper_bound - small.lower_bound
    large_width = large.upper_bound - large.lower_bound
    assert large_width < small_width


def _estimate(**overrides: float) -> object:
    """Construct a minimal valid estimate with overridable bounds."""
    from promptguard.domain.statistics import BootstrapEstimate

    fields = {
        "point_estimate": 0.5,
        "lower_bound": 0.4,
        "upper_bound": 0.6,
        "confidence_level": 0.95,
        "resamples": 2000,
        "seed": 1,
        "sample_count": 10,
    }
    fields.update(overrides)
    return BootstrapEstimate(**fields)


def test_bootstrap_estimate_rejects_inverted_or_non_finite_bounds() -> None:
    with pytest.raises(ValidationError):
        _estimate(lower_bound=0.6, upper_bound=0.4)
    with pytest.raises(ValidationError):
        _estimate(lower_bound=math.nan, upper_bound=0.4)


def test_confidence_intervals_preserve_case_pairing() -> None:
    results = [
        result("refund", "refund", 4.0),
        result("refund", "cancel", 2.0),
        result("cancel", "cancel", 5.0),
        result("cancel", "atm", 1.0),
    ]

    intervals = confidence_intervals(results, config(seed=42, resamples=500))

    assert intervals is not None
    assert intervals.sample_count == 4
    assert intervals.accuracy_interval is not None
    assert intervals.summary_score_interval is not None
    assert intervals.accuracy_interval.point_estimate == pytest.approx(0.5)
    assert intervals.summary_score_interval.point_estimate == pytest.approx(3.0)
    assert intervals.accuracy_interval.lower_bound <= (
        intervals.accuracy_interval.upper_bound
    )
    assert intervals.summary_score_interval.lower_bound <= (
        intervals.summary_score_interval.upper_bound
    )


def test_confidence_intervals_are_reproducible() -> None:
    results = [
        result("refund", "refund", 4.0),
        result("refund", "cancel", 2.0),
        result("cancel", "cancel", 5.0),
        result("cancel", "atm", 1.0),
    ]

    first = confidence_intervals(results, config(seed=9, resamples=300))
    second = confidence_intervals(results, config(seed=9, resamples=300))

    assert first == second


def test_confidence_intervals_empty_results_are_unavailable() -> None:
    assert confidence_intervals([], config()) is None


def test_small_sample_flag_follows_threshold() -> None:
    few = [result("a", "a", 4.0) for _ in range(10)]
    many = [result("a", "a", 4.0) for _ in range(30)]

    small = confidence_intervals(few, config())
    large = confidence_intervals(many, config())

    assert small is not None and small.small_sample
    assert large is not None and not large.small_sample


def test_build_report_populates_confidence_intervals() -> None:
    results = [
        result("a", "a", 4.0),
        result("a", "b", 2.0),
        result("b", "b", 5.0),
        result("b", "a", 1.0),
    ]

    report = ReportingService().build_report(
        results, "v1", "test-model", "test-dataset", bootstrap=config(seed=3),
    )

    intervals = report.confidence_intervals
    assert intervals is not None
    assert intervals.accuracy_interval is not None
    assert intervals.summary_score_interval is not None
    assert intervals.accuracy_interval.point_estimate == pytest.approx(0.5)
    assert (
        intervals.summary_score_interval.point_estimate
        == pytest.approx(3.0)
    )
    assert intervals.sample_count == 4
    assert report.aggregate_issues() == []


def test_build_report_without_bootstrap_keeps_intervals_unavailable() -> None:
    report = ReportingService().build_report(
        [result("a", "a", 4.0)], "v1", "test-model", "test-dataset",
    )

    assert report.confidence_intervals is None
    assert report.aggregate_issues() == []


def test_persisted_report_round_trips_intervals(tmp_path) -> None:  # type: ignore[no-untyped-def]
    report = ReportingService().build_report(
        [
            result("a", "a", 4.0),
            result("a", "b", 2.0),
            result("b", "b", 5.0),
        ],
        "v1",
        "test-model",
        "test-dataset",
        bootstrap=config(seed=3),
    )
    repository = JsonEvaluationRunRepository(tmp_path)

    repository.save(report)
    loaded = repository.get(report.run_id)

    assert loaded.confidence_intervals == report.confidence_intervals


def test_older_reports_without_intervals_still_load() -> None:
    report = ReportingService().build_report(
        [result("a", "a", 4.0)], "v1", "test-model", "test-dataset",
    )
    payload = report.model_dump(mode="json")
    del payload["confidence_intervals"]

    loaded = EvaluationReport.model_validate(payload)

    assert loaded.confidence_intervals is None


def test_tampered_interval_point_estimate_is_rejected() -> None:
    report = ReportingService().build_report(
        [result("a", "a", 4.0)], "v1", "test-model", "test-dataset",
        bootstrap=config(seed=3),
    )
    payload = report.model_dump(mode="json")
    payload["confidence_intervals"]["accuracy_interval"][
        "point_estimate"
    ] = 0.99

    with pytest.raises(
        ValueError,
        match="accuracy_interval point estimate is inconsistent",
    ):
        EvaluationReport.model_validate(payload)


def test_tampered_interval_sample_count_is_rejected() -> None:
    report = ReportingService().build_report(
        [result("a", "a", 4.0)], "v1", "test-model", "test-dataset",
        bootstrap=config(seed=3),
    )
    payload = report.model_dump(mode="json")
    payload["confidence_intervals"]["sample_count"] = 99

    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(payload)


def test_formatter_renders_confidence_block() -> None:
    report = ReportingService().build_report(
        [result("a", "a", 4.0), result("a", "b", 2.0)],
        "v1", "test-model", "test-dataset", bootstrap=config(seed=3),
    )

    text = TextReportFormatter().format(report)

    assert "Statistical confidence (seeded-percentile-bootstrap" in text
    assert "Category accuracy:" in text
    assert "Mean summary score:" in text
    assert "Warning: small sample" in text
    assert "sampling uncertainty only" in text


def test_formatter_omits_confidence_block_when_unavailable() -> None:
    report = ReportingService().build_report(
        [result("a", "a", 4.0)], "v1", "test-model", "test-dataset",
    )

    text = TextReportFormatter().format(report)

    assert "Statistical confidence" not in text
