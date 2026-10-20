"""Unit tests for the evaluation run orchestrator."""

import pytest

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.contracts import (
    EvaluationRunRepository,
    Notifier,
    ReportFormatter,
)
from promptguard.application.drift_service import DriftService
from promptguard.application.reporting_service import ReportingService
from promptguard.application.run_service import (
    EvaluationRunOutcome,
    EvaluationRunService,
)
from promptguard.domain.models import EvaluationResult, GoldenTestCase
from promptguard.domain.thresholds import RegressionThresholds


def result(category_match: bool = True, category: str = "technical") -> EvaluationResult:
    """Build a concise result fixture."""
    return EvaluationResult(
        test_case_id="case",
        expected_category="billing",
        actual_category="billing" if category_match else category,
        expected_summary="expected",
        actual_summary="actual",
        category_match=category_match,
        summary_score=4.0,
        latency_ms=20.0,
        input_tokens=3,
        output_tokens=2,
    )


def golden_case() -> GoldenTestCase:
    """Build a golden test case fixture."""
    return GoldenTestCase(
        id="case",
        input="input",
        expected_category="billing",
        ideal_summary="ideal",
        expected_difficulty="easy",
    )


class FakeEvaluationService:
    """Deterministic evaluation service returning fixed results."""

    def __init__(self, results: list[EvaluationResult]) -> None:
        self._results = results
        self.calls = 0

    async def evaluate_dataset(
        self, test_cases: list[GoldenTestCase], system_prompt: str,
    ) -> list[EvaluationResult]:
        self.calls += 1
        return self._results


class FailingEvaluationService:
    """Evaluation service that raises on every call (provider failure)."""

    async def evaluate_dataset(
        self, test_cases: list[GoldenTestCase], system_prompt: str,
    ) -> list[EvaluationResult]:
        raise ConnectionError("provider unavailable")


class FakeRepository(EvaluationRunRepository):
    """In-memory repository standing in for JSON persistence."""

    def __init__(self) -> None:
        self.saved: list = []

    def save(self, report) -> None:
        self.saved.append(report)

    def get(self, run_id: str):
        for report in self.saved:
            if report.run_id == run_id:
                return report
        raise FileNotFoundError(run_id)

    def get_latest(self):
        return self.saved[-1] if self.saved else None

    def list_runs(self) -> list:
        return list(self.saved)


class RecordingFormatter(ReportFormatter):
    """Formatter that records the reports it rendered."""

    def __init__(self) -> None:
        self.formatted: list = []

    def format(self, report) -> str:
        self.formatted.append(report)
        return "formatted-report"


class RecordingNotifier(Notifier):
    """Notifier that records messages instead of delivering them."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def notify(self, message: str) -> None:
        self.messages.append(message)


def build_service(
    results: list[EvaluationResult],
    repository: FakeRepository | None = None,
    formatter: RecordingFormatter | None = None,
    notifier: RecordingNotifier | None = None,
) -> tuple[EvaluationRunService, FakeEvaluationService]:
    """Assemble the orchestrator with fakes."""
    evaluation = FakeEvaluationService(results)
    service = EvaluationRunService(
        evaluation_service=evaluation,  # type: ignore[arg-type]
        reporting_service=ReportingService(),
        comparison_service=ComparisonService(),
        regression_service=RegressionService(),
        drift_service=DriftService(),
        repository=repository,
        formatter=formatter,
        notifier=notifier,
    )
    return service, evaluation


async def run(service: EvaluationRunService, **kwargs) -> EvaluationRunOutcome:
    """Invoke one orchestrated run with standard metadata."""
    return await service.run(
        test_cases=[golden_case()],
        system_prompt="prompt",
        prompt_version="v1",
        model="m",
        dataset="d",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_run_aggregates_and_returns_outcome() -> None:
    """Results should be aggregated into a complete report."""
    service, evaluation = build_service([result(), result(False)])
    outcome = await run(service)
    assert evaluation.calls == 1
    assert outcome.report.case_count == 2  # type: ignore[union-attr]
    assert outcome.report.category_accuracy == 0.5  # type: ignore[union-attr]
    assert outcome.report.regression is None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_run_compares_with_baseline_and_detects_drift() -> None:
    """A baseline run should produce comparison and drift data."""
    repository = FakeRepository()
    service, _ = build_service([result(False)], repository=repository)
    first = await run(service)
    second_service, _ = build_service([result(False)], repository=repository)
    outcome = await run(
        second_service, baseline_run_id=first.report.run_id,  # type: ignore[union-attr]
    )
    assert outcome.comparison is not None
    assert outcome.comparison.baseline_run_id == first.report.run_id  # type: ignore[union-attr]
    assert outcome.report.baseline_run_id == first.report.run_id  # type: ignore[union-attr]
    assert outcome.report.drift is not None  # type: ignore[union-attr]
    assert outcome.report.drift.sufficient_data  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_run_evaluates_thresholds() -> None:
    """Configured thresholds should attach a regression decision."""
    thresholds = RegressionThresholds(minimum_category_accuracy=0.9)
    service, _ = build_service([result(False)])
    outcome = await run(service, thresholds=thresholds)
    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert not regression.passed
    assert len(regression.violations) == 1


@pytest.mark.asyncio
async def test_run_persists_and_formats() -> None:
    """The report should be saved and rendered when collaborators exist."""
    repository = FakeRepository()
    formatter = RecordingFormatter()
    service, _ = build_service([result()], repository=repository, formatter=formatter)
    outcome = await run(service)
    assert repository.saved == [outcome.report]  # type: ignore[union-attr]
    assert formatter.formatted == [outcome.report]  # type: ignore[union-attr]
    assert outcome.report_text == "formatted-report"


@pytest.mark.asyncio
async def test_run_notifies_when_configured() -> None:
    """The notifier should receive exactly one post-run message."""
    notifier = RecordingNotifier()
    thresholds = RegressionThresholds(minimum_category_accuracy=1.0)
    service, _ = build_service([result(False)], notifier=notifier)
    await run(service, thresholds=thresholds)
    assert len(notifier.messages) == 1
    assert "FAIL" in notifier.messages[0]


@pytest.mark.asyncio
async def test_run_without_notifier_sends_nothing() -> None:
    """Notification is optional and skipped by default."""
    service, _ = build_service([result()])
    outcome = await run(service)
    assert outcome.report is not None  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_run_baseline_without_repository_raises() -> None:
    """Requesting a baseline without persistence is a configuration error."""
    service, _ = build_service([result()])
    with pytest.raises(ValueError, match="repository"):
        await run(service, baseline_run_id="missing")


@pytest.mark.asyncio
async def test_run_latest_baseline_uses_repository() -> None:
    """The 'latest' alias should resolve through the repository."""
    repository = FakeRepository()
    service, _ = build_service([result()], repository=repository)
    first = await run(service)
    second_service, _ = build_service([result()], repository=repository)
    outcome = await run(
        second_service,
        baseline_run_id="latest",  # type: ignore[arg-type]
    )
    assert outcome.comparison is not None
    assert (
        outcome.comparison.baseline_run_id
        == first.report.run_id  # type: ignore[union-attr]
    )


@pytest.mark.asyncio
async def test_run_provider_failure_propagates_without_persistence() -> None:
    """When evaluation fails, no partial report is saved, formatted, or notified."""
    repository = FakeRepository()
    formatter = RecordingFormatter()
    notifier = RecordingNotifier()
    service, _ = build_service(
        [], repository=repository, formatter=formatter, notifier=notifier,
    )
    # Replace with a failing service to simulate provider unavailability.
    service._evaluation_service = FailingEvaluationService()  # type: ignore[assignment]

    with pytest.raises(ConnectionError, match="provider unavailable"):
        await run(service)

    # No partial report persisted, formatted, or notified.
    assert repository.saved == []
    assert formatter.formatted == []
    assert notifier.messages == []

