"""End-to-end integration tests for the evaluation pipeline."""

from pathlib import Path
from typing import Any

import pytest

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.drift_service import DriftService
from promptguard.application.evaluation_service import EvaluationService
from promptguard.application.reporting_service import ReportingService
from promptguard.application.run_service import EvaluationRunService
from promptguard.domain.models import GoldenTestCase, LLMOutput
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.infrastructure.dataset.json_repository import JsonDatasetRepository
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.infrastructure.prompts.yaml_repository import PromptRepository
from promptguard.infrastructure.reporting.html_renderer import TextReportFormatter


def build_service(
    tmp_path: Path,
    categories: list[str],
    aliases: dict[str, str],
    client: Any,
    metric: Any,
    notifier: Any = None,
) -> EvaluationRunService:
    """Assemble the complete pipeline with real file-backed adapters."""
    evaluation_service = EvaluationService(
        llm_client=client,
        summary_metric=metric,
        allowed_categories=categories,
        category_aliases=aliases,
    )
    return EvaluationRunService(
        evaluation_service=evaluation_service,
        reporting_service=ReportingService(),
        comparison_service=ComparisonService(),
        regression_service=RegressionService(),
        drift_service=DriftService(),
        repository=JsonEvaluationRunRepository(tmp_path),
        formatter=TextReportFormatter(),
        notifier=notifier,
    )


def responses_for(
    cases: list[GoldenTestCase],
    overrides: dict[str, str] | None = None,
) -> dict[str, LLMOutput]:
    """Build scripted outputs, optionally overriding a case's category."""
    overrides = overrides or {}
    return {
        case.input: LLMOutput(
            category=overrides.get(case.id, case.expected_category),
            summary=case.ideal_summary,
        )
        for case in cases
    }


def test_real_repositories_load_v1_assets(project_root: Path) -> None:
    """The composition root's dataset and prompt adapters load real files."""
    cases = JsonDatasetRepository(
        project_root / "dataset" / "v1" / "golden_dataset.json",
    ).load()
    config = PromptRepository(project_root / "prompts").load("v1")

    assert len(cases) == 50
    assert cases[0].expected_category in config["categories"]
    assert config["category_aliases"]["Refund Status Inquiry"] == (
        "Refund_not_showing_up"
    )
    critical = [case for case in cases if case.critical]
    assert critical
    assert all(case.critical_rationale for case in critical)
    assert {"banking77-024", "banking77-043", "banking77-044"} <= {
        case.id for case in critical
    }


@pytest.mark.asyncio
async def test_complete_run_persists_formats_and_notifies(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
    recording_notifier: Any,
) -> None:
    """One run should evaluate, aggregate, persist, render, and notify."""
    cases = golden_cases[:5]
    client = scripted_client(responses_for(cases))
    service = build_service(
        tmp_path, categories, aliases, client, fixed_metric, recording_notifier,
    )

    outcome = await service.run(
        test_cases=cases,
        system_prompt=system_prompt,
        prompt_version="v1",
        model="test-model",
        dataset="golden-v1",
        thresholds=RegressionThresholds(
            minimum_category_accuracy=1.0,
            minimum_summary_score=4.0,
        ),
    )

    assert outcome.report.regression is not None
    assert outcome.report.regression.status == "PASS"
    assert outcome.report.eval_config_hash is not None
    assert outcome.report_text is not None
    assert "Regression status: PASS" in outcome.report_text
    assert "Eval config hash:" in outcome.report_text

    repository = JsonEvaluationRunRepository(tmp_path)
    saved = repository.get(outcome.report.run_id)
    assert saved == outcome.report
    assert (tmp_path / f"{outcome.report.run_id}.json").is_file()

    assert len(recording_notifier.messages) == 1
    assert "Regression status: PASS" in recording_notifier.messages[0]


@pytest.mark.asyncio
async def test_baseline_regression_detects_new_failure_and_drift(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """A degraded candidate should fail its drop gate and report drift."""
    cases = golden_cases[:5]
    baseline_client = scripted_client(responses_for(cases))
    baseline_service = build_service(
        tmp_path, categories, aliases, baseline_client, fixed_metric,
    )
    baseline = await baseline_service.run(
        test_cases=cases,
        system_prompt=system_prompt,
        prompt_version="v1",
        model="test-model",
        dataset="golden-v1",
        thresholds=RegressionThresholds(minimum_category_accuracy=0.5),
    )

    degraded_client = scripted_client(
        responses_for(cases, {cases[0].id: "technical"}),
    )
    degraded_service = build_service(
        tmp_path, categories, aliases, degraded_client, fixed_metric,
    )
    outcome = await degraded_service.run(
        test_cases=cases,
        system_prompt=system_prompt,
        prompt_version="v1",
        model="test-model",
        dataset="golden-v1",
        thresholds=RegressionThresholds(
            maximum_category_accuracy_drop_pct=10,
        ),
        baseline_run_id=baseline.report.run_id,
    )

    assert outcome.comparison is not None
    assert outcome.report.comparison == outcome.comparison
    assert outcome.comparison.category_accuracy_delta == pytest.approx(-0.2)
    assert outcome.report.drift is not None
    assert outcome.report.drift.sufficient_data
    assert outcome.report.drift.total_variation_distance == pytest.approx(0.2)

    assert outcome.report.regression is not None
    assert outcome.report.regression.status == "FAIL"
    assert any(
        violation.metric == "category_accuracy_drop_pct"
        for violation in outcome.report.regression.violations
    )
    assert any(
        regression.test_case_id == cases[0].id
        and regression.status == "new_failure"
        for regression in outcome.report.regression.case_regressions
    )


@pytest.mark.asyncio
async def test_critical_case_failure_vetoes_aggregate_pass(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """A failed critical case should force FAIL even when accuracy passes."""
    cases = [
        golden_cases[0].model_copy(update={
            "critical": True,
            "critical_rationale": "Test case exercises the critical veto.",
        }),
        golden_cases[1],
        golden_cases[2],
    ]
    baseline_client = scripted_client(responses_for(cases))
    baseline_service = build_service(
        tmp_path, categories, aliases, baseline_client, fixed_metric,
    )
    baseline = await baseline_service.run(
        test_cases=cases,
        system_prompt=system_prompt,
        prompt_version="v1",
        model="test-model",
        dataset="golden-v1",
        thresholds=RegressionThresholds(minimum_category_accuracy=0.5),
    )

    degraded_client = scripted_client(
        responses_for(cases, {cases[0].id: "technical"}),
    )
    degraded_service = build_service(
        tmp_path, categories, aliases, degraded_client, fixed_metric,
    )
    outcome = await degraded_service.run(
        test_cases=cases,
        system_prompt=system_prompt,
        prompt_version="v1",
        model="test-model",
        dataset="golden-v1",
        thresholds=RegressionThresholds(minimum_category_accuracy=0.5),
        baseline_run_id=baseline.report.run_id,
    )

    assert outcome.report.regression is not None
    assert outcome.report.regression.critical_case_failure
    assert outcome.report.regression.status == "FAIL"
    assert any(
        violation.metric == "critical_case_failure"
        for violation in outcome.report.regression.violations
    )


@pytest.mark.asyncio
async def test_incompatible_baseline_is_inconclusive(
    tmp_path: Path,
    golden_cases: list[GoldenTestCase],
    system_prompt: str,
    categories: list[str],
    aliases: dict[str, str],
    scripted_client: Any,
    fixed_metric: Any,
) -> None:
    """A model mismatch should skip comparison and surface the reason."""
    cases = golden_cases[:3]
    first_client = scripted_client(responses_for(cases))
    first_service = build_service(
        tmp_path, categories, aliases, first_client, fixed_metric,
    )
    await first_service.run(
        test_cases=cases,
        system_prompt=system_prompt,
        prompt_version="v1",
        model="model-a",
        dataset="golden-v1",
    )

    second_client = scripted_client(responses_for(cases))
    second_service = build_service(
        tmp_path, categories, aliases, second_client, fixed_metric,
    )
    outcome = await second_service.run(
        test_cases=cases,
        system_prompt=system_prompt,
        prompt_version="v1",
        model="model-b",
        dataset="golden-v1",
        baseline_run_id="latest",
    )

    assert outcome.comparison is None
    assert outcome.report.baseline_run_id is None
    assert outcome.report.regression is not None
    assert outcome.report.regression.status == "INCONCLUSIVE"
    assert any(
        "Model mismatch" in reason
        for reason in outcome.report.regression.incompatibility_reasons
    )
