"""Unit tests for paired baseline analysis.

McNemar p-values are verified against hand calculations; alignment,
reproducibility, persistence, and gate-preservation behaviors are
exercised directly.
"""

import pytest
from pydantic import ValidationError

from promptguard.application.reporting_service import ReportingService
from promptguard.application.run_service import EvaluationRunService
from promptguard.domain.models import EvaluationReport, EvaluationResult
from promptguard.domain.paired import (
    PairedAnalysis,
    exact_mcnemar_p_value,
    paired_baseline_analysis,
)
from promptguard.domain.statistics import BootstrapConfig, bootstrap_interval
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.infrastructure.reporting.html_renderer import (
    TextReportFormatter,
)

_seq = iter(range(10000))


def mcnemar_config() -> BootstrapConfig:
    """Return compact bootstrap parameters for paired differences."""
    return BootstrapConfig(seed=31, resamples=500)


def result(
    case_id: str,
    correct: bool,
    score: float,
    expected: str = "billing",
) -> EvaluationResult:
    """Build one evaluation result with controlled paired evidence."""
    return EvaluationResult(
        test_case_id=case_id,
        expected_category=expected,
        actual_category=expected if correct else "technical",
        normalized_category=expected if correct else "technical",
        expected_summary="ideal summary",
        actual_summary="actual summary",
        category_match=correct,
        summary_score=score,
        latency_ms=100.0,
        input_tokens=1,
        output_tokens=1,
    )


def results(
    correctness: list[bool],
    scores: list[float],
) -> list[EvaluationResult]:
    """Build a candidate result list with per-case control."""
    return [
        result(f"case-{index + 1}", correct, score)
        for index, (correct, score) in enumerate(
            zip(correctness, scores, strict=True),
        )
    ]


def test_hand_calculated_exact_mcnemar_p_values() -> None:
    """Two-sided exact binomial tails, verified by hand."""
    assert exact_mcnemar_p_value(1, 3) == pytest.approx(0.625)
    assert exact_mcnemar_p_value(3, 1) == pytest.approx(0.625)
    assert exact_mcnemar_p_value(0, 5) == pytest.approx(0.0625)
    assert exact_mcnemar_p_value(5, 0) == pytest.approx(0.0625)
    assert exact_mcnemar_p_value(2, 2) == pytest.approx(1.0)
    assert exact_mcnemar_p_value(1, 1) == pytest.approx(1.0)


def test_mcnemar_without_discordant_cases_is_uninformative() -> None:
    assert exact_mcnemar_p_value(0, 0) is None


def test_equal_accuracy_runs_with_different_errors_are_distinguished() -> None:
    """Both runs are 50% accurate but disagree on two cases."""
    baseline = results(
        correctness=[True, True, False, False],
        scores=[4.0, 4.0, 2.0, 2.0],
    )
    candidate = results(
        correctness=[True, False, True, False],
        scores=[4.0, 2.0, 4.0, 2.0],
    )

    paired = paired_baseline_analysis(baseline, candidate, mcnemar_config())

    assert paired.matched_cases == 4
    assert paired.both_correct == 1
    assert paired.both_incorrect == 1
    assert paired.baseline_only_correct == 1
    assert paired.candidate_only_correct == 1
    assert paired.mcnemar_p_value == pytest.approx(1.0)
    assert not paired.mcnemar_uninformative
    assert paired.unavailable_reason is None


def test_one_sided_improvement_has_small_p_value() -> None:
    baseline = results(
        correctness=[False, False, True, True, False],
        scores=[2.0, 2.0, 4.0, 4.0, 2.0],
    )
    candidate = results(
        correctness=[True, True, True, True, True],
        scores=[4.0, 4.0, 4.0, 4.0, 4.0],
    )

    paired = paired_baseline_analysis(baseline, candidate, mcnemar_config())

    assert paired.baseline_only_correct == 0
    assert paired.candidate_only_correct == 3
    assert paired.both_correct == 2
    assert paired.mcnemar_p_value == pytest.approx(0.25)


def test_paired_bootstrap_matches_direct_difference_bootstrap() -> None:
    baseline_scores = [4.0, 2.0, 5.0, 1.0]
    candidate_scores = [5.0, 2.0, 3.0, 4.0]
    baseline = results(
        [True, True, False, False], baseline_scores,
    )
    candidate = results(
        [True, True, False, False], candidate_scores,
    )
    differences = [
        current - prior
        for prior, current in zip(
            baseline_scores, candidate_scores, strict=True,
        )
    ]

    paired = paired_baseline_analysis(baseline, candidate, mcnemar_config())
    direct = bootstrap_interval(differences, mcnemar_config())

    assert paired.summary_change_interval == direct
    assert paired.summary_change_interval is not None
    assert paired.summary_change_interval.point_estimate == pytest.approx(
        sum(differences) / len(differences),
    )


def test_identical_inputs_and_seeds_are_reproducible() -> None:
    baseline = results([True, False, True], [4.0, 2.0, 5.0])
    candidate = results([True, False, True], [5.0, 3.0, 4.0])

    first = paired_baseline_analysis(baseline, candidate, mcnemar_config())
    second = paired_baseline_analysis(baseline, candidate, mcnemar_config())

    assert first == second


def test_paired_analysis_without_bootstrap_has_no_interval() -> None:
    baseline = results([True, False], [4.0, 2.0])
    candidate = results([True, False], [4.0, 2.0])

    paired = paired_baseline_analysis(baseline, candidate)

    assert paired.summary_change_interval is None
    assert paired.mcnemar_uninformative
    assert paired.mcnemar_p_value is None
    assert paired.baseline_only_correct == 0
    assert paired.candidate_only_correct == 0


def test_incompatible_results_are_explicitly_unavailable() -> None:
    baseline = results([True, False, True], [4.0, 2.0, 5.0])
    missing_candidate = results([True, False], [4.0, 2.0])
    mismatched = [
        result("case-1", True, 4.0),
        result("case-2", False, 2.0),
        result("case-3", True, 5.0, expected="refunds"),
    ]
    duplicated = [
        result("case-1", True, 4.0),
        result("case-1", False, 2.0),
    ]

    missing = paired_baseline_analysis(baseline, missing_candidate)
    mismatch = paired_baseline_analysis(baseline, mismatched)
    duplicate = paired_baseline_analysis(duplicated, baseline)
    empty = paired_baseline_analysis([], baseline)

    assert missing.unavailable_reason is not None
    assert "case id mismatch" in missing.unavailable_reason
    assert mismatch.unavailable_reason is not None
    assert "expected categories differ" in mismatch.unavailable_reason
    assert duplicate.unavailable_reason is not None
    assert "duplicate test case id" in duplicate.unavailable_reason
    assert empty.unavailable_reason is not None
    assert "no evaluated cases" in empty.unavailable_reason


def test_paired_counts_must_add_up() -> None:
    with pytest.raises(ValidationError, match="add up"):
        PairedAnalysis(
            matched_cases=10,
            both_correct=1,
            both_incorrect=1,
            baseline_only_correct=2,
            candidate_only_correct=2,
        )


def test_uninformative_flag_must_agree_with_p_value() -> None:
    with pytest.raises(ValidationError, match="mcnemar_uninformative"):
        PairedAnalysis(
            matched_cases=2,
            both_correct=2,
            both_incorrect=0,
            baseline_only_correct=0,
            candidate_only_correct=0,
            mcnemar_p_value=1.0,
            mcnemar_uninformative=True,
        )


def report_with(paired: PairedAnalysis | None) -> EvaluationReport:
    """Build a valid report carrying the given paired analysis."""
    report = ReportingService().build_report(
        results([True, False], [4.0, 2.0]), "v1", "m", "d",
    )
    return report.model_copy(update={"paired_analysis": paired})


def test_formatter_renders_paired_block() -> None:
    report = report_with(
        PairedAnalysis(
            matched_cases=4,
            both_correct=1,
            both_incorrect=1,
            baseline_only_correct=1,
            candidate_only_correct=1,
            mcnemar_p_value=1.0,
            summary_change_interval=bootstrap_interval(
                [0.0, 0.0, 0.5, -0.5], mcnemar_config(),
            ),
        ),
    )

    text = TextReportFormatter().format(report)

    assert "Paired baseline analysis (4 matched cases):" in text
    assert "Regressions (baseline only correct): 1" in text
    assert "Fixes (candidate only correct): 1" in text
    assert "Exact McNemar p-value: 1.0000" in text
    assert "Mean summary-score change:" in text
    assert "paired statistics are informational" in text


def test_formatter_renders_uninformative_and_unavailable_cases() -> None:
    informative = report_with(
        PairedAnalysis(
            matched_cases=2,
            both_correct=2,
            mcnemar_uninformative=True,
        ),
    )
    unavailable = report_with(PairedAnalysis.unavailable("case id mismatch"))

    informative_text = TextReportFormatter().format(informative)
    unavailable_text = TextReportFormatter().format(unavailable)

    assert (
        "Exact McNemar p-value: uninformative (no discordant cases)"
        in informative_text
    )
    assert "Mean summary-score change: unavailable" in informative_text
    assert (
        "Paired baseline analysis: unavailable (case id mismatch)"
        in unavailable_text
    )


def test_tampered_matched_cases_is_rejected() -> None:
    paired = PairedAnalysis(
        matched_cases=2,
        both_correct=2,
        mcnemar_uninformative=True,
    )
    report = report_with(paired)
    payload = report.model_dump(mode="json")
    payload["paired_analysis"]["matched_cases"] = 99

    with pytest.raises(ValidationError):
        EvaluationReport.model_validate(payload)


def test_older_reports_without_paired_analysis_still_load() -> None:
    report = report_with(None)
    payload = report.model_dump(mode="json")
    del payload["paired_analysis"]

    loaded = EvaluationReport.model_validate(payload)

    assert loaded.paired_analysis is None


@pytest.mark.asyncio
async def test_run_service_populates_paired_analysis(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A compared baseline must produce paired analysis; gates unchanged."""
    from promptguard.application.comparison_service import (
        ComparisonService,
        RegressionService,
    )
    from promptguard.application.drift_service import DriftService
    from promptguard.application.reporting_service import ReportingService

    class FixedEvaluationService:
        async def evaluate_dataset(self, test_cases, system_prompt):  # type: ignore[no-untyped-def]
            return results([True, False, True], [4.0, 2.0, 5.0])

    class ImprovedEvaluationService:
        async def evaluate_dataset(self, test_cases, system_prompt):  # type: ignore[no-untyped-def]
            return results([True, True, True], [5.0, 5.0, 5.0])

    def build(evaluation_service) -> EvaluationRunService:  # type: ignore[no-untyped-def]
        return EvaluationRunService(
            evaluation_service=evaluation_service,  # type: ignore[arg-type]
            reporting_service=ReportingService(),
            comparison_service=ComparisonService(),
            regression_service=RegressionService(),
            drift_service=DriftService(),
            repository=JsonEvaluationRunRepository(tmp_path),
        )

    def golden_cases() -> list:  # type: ignore[no-untyped-def]
        from promptguard.domain.models import GoldenTestCase

        return [
            GoldenTestCase(
                id=f"case-{index}",
                input="input",
                expected_category="billing",
                ideal_summary="ideal",
                expected_difficulty="easy",
            )
            for index in range(1, 4)
        ]

    baseline_service = build(FixedEvaluationService())
    baseline = await baseline_service.run(
        test_cases=golden_cases(),
        system_prompt="prompt",
        prompt_version="v1",
        model="m",
        dataset="d",
    )

    candidate_service = build(ImprovedEvaluationService())
    outcome = await candidate_service.run(
        test_cases=golden_cases(),
        system_prompt="prompt",
        prompt_version="v1",
        model="m",
        dataset="d",
        baseline_run_id=baseline.report.run_id,
        bootstrap=BootstrapConfig(seed=31, resamples=500),
    )

    paired = outcome.report.paired_analysis
    assert paired is not None
    assert paired.matched_cases == 3
    assert paired.baseline_only_correct == 0
    assert paired.candidate_only_correct == 1
    assert paired.both_correct == 2
    assert paired.both_incorrect == 0
    assert paired.mcnemar_p_value == pytest.approx(1.0)
    assert paired.summary_change_interval is not None
    assert (
        paired.summary_change_interval.point_estimate == pytest.approx(4.0 / 3)
    )
    assert outcome.report.aggregate_issues() == []
    assert outcome.report.regression is not None
    assert outcome.report.regression.status in {"PASS", "FAIL", "INCONCLUSIVE"}


@pytest.mark.asyncio
async def test_run_service_without_baseline_has_no_paired_analysis(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    from promptguard.application.comparison_service import (
        ComparisonService,
        RegressionService,
    )
    from promptguard.application.drift_service import DriftService
    from promptguard.application.reporting_service import ReportingService

    class FixedEvaluationService:
        async def evaluate_dataset(self, test_cases, system_prompt):  # type: ignore[no-untyped-def]
            return results([True], [4.0])

    service = EvaluationRunService(
        evaluation_service=FixedEvaluationService(),  # type: ignore[arg-type]
        reporting_service=ReportingService(),
        comparison_service=ComparisonService(),
        regression_service=RegressionService(),
        drift_service=DriftService(),
        repository=JsonEvaluationRunRepository(tmp_path),
    )
    outcome = await service.run(
        test_cases=[],
        system_prompt="prompt",
        prompt_version="v1",
        model="m",
        dataset="d",
    )

    assert outcome.report.paired_analysis is None
