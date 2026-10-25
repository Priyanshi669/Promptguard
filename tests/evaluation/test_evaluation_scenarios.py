"""Acceptance-style tests for evaluation semantics and quality decisions."""

from typing import Any

import pytest

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.drift_service import DriftService
from promptguard.application.evaluation_service import EvaluationService
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import GoldenTestCase, LLMOutput
from promptguard.domain.thresholds import RegressionThresholds


def build_service(
    categories: list[str],
    aliases: dict[str, str],
    client: Any,
    metric: Any,
) -> EvaluationService:
    """Build the real evaluation service with deterministic fakes."""
    return EvaluationService(
        llm_client=client,
        summary_metric=metric,
        allowed_categories=categories,
        category_aliases=aliases,
    )


def responses_for(
    cases: list[GoldenTestCase],
    overrides: dict[str, tuple[str, str]] | None = None,
) -> dict[str, LLMOutput]:
    """Build scripted outputs, optionally overriding category and summary."""
    overrides = overrides or {}
    responses: dict[str, LLMOutput] = {}
    for case in cases:
        category, summary = overrides.get(
            case.id, (case.expected_category, case.ideal_summary),
        )
        responses[case.input] = LLMOutput(category=category, summary=summary)
    return responses


@pytest.mark.asyncio
async def test_alias_maps_to_canonical_category(
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """A human-readable alias should match the canonical category."""
    case = golden_cases[0]
    client = scripted_client({
        case.input: LLMOutput(
            category="Refund Status Inquiry",
            summary=case.ideal_summary,
        ),
    })
    service = build_service(categories, aliases, client, fixed_metric)

    result = await service.evaluate(case, system_prompt)

    assert result.actual_category == "Refund Status Inquiry"
    assert result.normalized_category == "Refund_not_showing_up"
    assert result.normalization_applied
    assert result.category_match


@pytest.mark.asyncio
async def test_unknown_category_is_preserved_and_mismatched(
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """Unknown labels must never be silently remapped to a category."""
    case = golden_cases[0]
    client = scripted_client({
        case.input: LLMOutput(
            category="Mystery Category",
            summary=case.ideal_summary,
        ),
    })
    service = build_service(categories, aliases, client, fixed_metric)

    result = await service.evaluate(case, system_prompt)

    assert result.normalized_category == "Mystery Category"
    assert not result.normalization_applied
    assert not result.category_match


@pytest.mark.asyncio
async def test_case_insensitive_canonical_match(
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """Canonical categories should match regardless of casing."""
    case = golden_cases[0]
    client = scripted_client({
        case.input: LLMOutput(
            category=case.expected_category.upper(),
            summary=case.ideal_summary,
        ),
    })
    service = build_service(categories, aliases, client, fixed_metric)

    result = await service.evaluate(case, system_prompt)

    assert result.normalized_category == case.expected_category
    assert result.normalization_applied
    assert result.category_match


@pytest.mark.asyncio
async def test_aggregate_metrics_reflect_scripted_outcomes(
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    sequence_metric: Any,
) -> None:
    """Accuracy and summary averages should match the scripted results."""
    cases = golden_cases[:4]
    client = scripted_client(
        responses_for(cases, {cases[3].id: ("technical", cases[3].ideal_summary)}),
    )
    metric = sequence_metric([5.0, 4.0, 3.0, 2.0])
    service = build_service(categories, aliases, client, metric)

    results = await service.evaluate_dataset(cases, system_prompt)
    report = ReportingService().build_report(
        results, "v1", "test-model", "golden-v1",
    )

    assert report.case_count == 4
    assert report.category_accuracy == pytest.approx(0.75)
    assert report.average_summary_score == pytest.approx(3.5)


@pytest.mark.asyncio
async def test_degraded_candidate_fails_relative_drop_gate(
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """A 20% accuracy drop should fail a 10% relative-drop gate."""
    cases = golden_cases[:10]
    baseline_client = scripted_client(responses_for(cases))
    baseline_service = build_service(
        categories, aliases, baseline_client, fixed_metric,
    )
    baseline_results = await baseline_service.evaluate_dataset(cases, system_prompt)

    degraded_client = scripted_client(
        responses_for(
            cases,
            {
                cases[0].id: ("technical", cases[0].ideal_summary),
                cases[1].id: ("technical", cases[1].ideal_summary),
            },
        ),
    )
    degraded_service = build_service(
        categories, aliases, degraded_client, fixed_metric,
    )
    current_results = await degraded_service.evaluate_dataset(cases, system_prompt)

    baseline_report = ReportingService().build_report(
        baseline_results, "v1", "test-model", "golden-v1",
    )
    current_report = ReportingService().build_report(
        current_results, "v1", "test-model", "golden-v1",
    )
    comparison = ComparisonService().compare(baseline_report, current_report)
    decision = RegressionService().evaluate(
        current_report,
        RegressionThresholds(maximum_category_accuracy_drop_pct=10),
        comparison=comparison,
        baseline_results={result.test_case_id: result for result in baseline_results},
    )

    assert decision.status == "FAIL"
    assert any(
        violation.metric == "category_accuracy_drop_pct"
        for violation in decision.violations
    )
    new_failures = {
        regression.test_case_id
        for regression in decision.case_regressions
        if regression.status == "new_failure"
    }
    assert new_failures == {cases[0].id, cases[1].id}


@pytest.mark.asyncio
async def test_drift_uses_normalized_categories(
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """Alias and canonical outputs of the same category should not drift."""
    cases = golden_cases[:3]
    baseline_client = scripted_client(responses_for(cases))
    baseline_service = build_service(
        categories, aliases, baseline_client, fixed_metric,
    )
    baseline_results = await baseline_service.evaluate_dataset(cases, system_prompt)

    current_client = scripted_client(
        responses_for(
            cases,
            {cases[0].id: ("Refund Status Inquiry", cases[0].ideal_summary)},
        ),
    )
    current_service = build_service(
        categories, aliases, current_client, fixed_metric,
    )
    current_results = await current_service.evaluate_dataset(cases, system_prompt)

    baseline_report = ReportingService().build_report(
        baseline_results, "v1", "test-model", "golden-v1",
    )
    current_report = ReportingService().build_report(
        current_results, "v1", "test-model", "golden-v1",
    )
    drift = DriftService().compare(baseline_report, current_report)

    assert drift.sufficient_data
    assert drift.total_variation_distance == pytest.approx(0.0)
    assert drift.baseline_distribution == drift.current_distribution
