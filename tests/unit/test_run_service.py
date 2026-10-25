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


def result(
    category_match: bool = True,
    category: str = "technical",
    case_id: str = "case",
    critical: bool = False,
) -> EvaluationResult:
    """Build a concise result fixture."""
    return EvaluationResult(
        test_case_id=case_id,
        expected_category="billing",
        actual_category="billing" if category_match else category,
        expected_summary="expected",
        actual_summary="actual",
        category_match=category_match,
        summary_score=4.0,
        latency_ms=20.0,
        input_tokens=3,
        output_tokens=2,
        critical=critical,
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


class FailingNotifier(Notifier):
    """Notifier that simulates a delivery failure."""

    async def notify(self, message: str) -> None:
        raise RuntimeError("webhook-secret-token")


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
    assert outcome.report.regression is not None  # type: ignore[union-attr]
    assert outcome.report.regression.status == "INCONCLUSIVE"  # type: ignore[union-attr]


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
    assert outcome.report.thresholds == thresholds  # type: ignore[union-attr]


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
async def test_run_records_deterministic_eval_config_hash() -> None:
    """Identical configuration should produce the same config fingerprint."""
    first_service, _ = build_service([result()])
    second_service, _ = build_service([result()])

    first = await run(
        first_service, eval_config={"temperature": 0.0, "seed": 7},
    )
    second = await run(
        second_service, eval_config={"temperature": 0.0, "seed": 7},
    )

    assert first.report.eval_config_hash  # type: ignore[union-attr]
    assert (
        first.report.eval_config_hash  # type: ignore[union-attr]
        == second.report.eval_config_hash  # type: ignore[union-attr]
    )


@pytest.mark.asyncio
async def test_eval_config_hash_changes_when_evaluation_inputs_change() -> None:
    """A different seed, model, prompt, or case set should change the fingerprint."""
    service, _ = build_service([result()])
    base = await run(service, eval_config={"seed": 1})
    other_seed = await run(service, eval_config={"seed": 2})
    other_model = await service.run(
        test_cases=[golden_case()],
        system_prompt="prompt",
        prompt_version="v1",
        model="different-model",
        dataset="d",
    )
    other_prompt = await service.run(
        test_cases=[golden_case()],
        system_prompt="different prompt",
        prompt_version="v1",
        model="m",
        dataset="d",
    )
    other_case = await service.run(
        test_cases=[golden_case().model_copy(update={"input": "other input"})],
        system_prompt="prompt",
        prompt_version="v1",
        model="m",
        dataset="d",
    )

    hashes = {
        base.report.eval_config_hash,  # type: ignore[union-attr]
        other_seed.report.eval_config_hash,  # type: ignore[union-attr]
        other_model.report.eval_config_hash,  # type: ignore[union-attr]
        other_prompt.report.eval_config_hash,  # type: ignore[union-attr]
        other_case.report.eval_config_hash,  # type: ignore[union-attr]
    }
    assert len(hashes) == 5


@pytest.mark.asyncio
async def test_eval_config_hash_ignores_threshold_policy_changes() -> None:
    """Changing gate thresholds should not invalidate a baseline fingerprint."""
    service, _ = build_service([result()])
    base = await run(service, eval_config={"seed": 1})
    with_threshold = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.5),
        eval_config={"seed": 1},
    )

    assert (
        with_threshold.report.eval_config_hash  # type: ignore[union-attr]
        == base.report.eval_config_hash  # type: ignore[union-attr]
    )


@pytest.mark.asyncio
async def test_run_incompatible_baseline_is_inconclusive() -> None:
    """A mismatched baseline should not be compared and must be reported."""
    repository = FakeRepository()
    service, _ = build_service([result()], repository=repository)
    first = await run(service)

    second_service, _ = build_service([result()], repository=repository)
    outcome = await second_service.run(
        test_cases=[golden_case()],
        system_prompt="prompt",
        prompt_version="v1",
        model="different-model",
        dataset="d",
        baseline_run_id=first.report.run_id,  # type: ignore[union-attr]
    )

    assert outcome.comparison is None
    assert outcome.report.regression is not None  # type: ignore[union-attr]
    assert outcome.report.regression.status == "INCONCLUSIVE"  # type: ignore[union-attr]
    reasons = outcome.report.regression.incompatibility_reasons  # type: ignore[union-attr]
    assert any("Model mismatch" in reason for reason in reasons)
    assert outcome.report.baseline_run_id is None  # type: ignore[union-attr]


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


@pytest.mark.asyncio
async def test_run_missing_baseline_is_inconclusive() -> None:
    """A requested baseline that cannot be found must not produce a gate."""
    repository = FakeRepository()
    service, _ = build_service([result()], repository=repository)
    outcome = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
        baseline_run_id="missing",
    )

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "INCONCLUSIVE"
    assert any("no baseline run was found" in reason
               for reason in regression.incompatibility_reasons)


@pytest.mark.asyncio
async def test_run_legacy_baseline_without_hash_is_inconclusive() -> None:
    """A baseline that cannot prove prompt/case identity is incompatible."""
    repository = FakeRepository()
    baseline = ReportingService().build_report(
        [result()], "v1", "m", "d",
    )
    repository.saved.append(baseline)
    service, _ = build_service([result()], repository=repository)

    outcome = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
        baseline_run_id=baseline.run_id,
    )

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "INCONCLUSIVE"
    assert any("no eval_config_hash" in reason
               for reason in regression.incompatibility_reasons)


@pytest.mark.asyncio
async def test_run_empty_baseline_is_inconclusive() -> None:
    """An empty baseline cannot establish a comparison or drop gate."""
    repository = FakeRepository()
    service, _ = build_service([result()], repository=repository)
    probe = await run(service)
    baseline = ReportingService().build_report([], "v1", "m", "d")
    baseline.eval_config_hash = probe.report.eval_config_hash  # type: ignore[union-attr]
    repository.saved.append(baseline)

    outcome = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
        baseline_run_id=baseline.run_id,
    )

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "INCONCLUSIVE"
    assert any("Baseline has no evaluated cases" in reason
               for reason in regression.incompatibility_reasons)


@pytest.mark.asyncio
async def test_run_prompt_change_with_same_version_is_inconclusive() -> None:
    """Prompt content changes must be detected even without a version bump."""
    repository = FakeRepository()
    service, _ = build_service([result()], repository=repository)
    baseline = await run(service)

    second_service, _ = build_service([result()], repository=repository)
    outcome = await second_service.run(
        test_cases=[golden_case()],
        system_prompt="different prompt",
        prompt_version="v1",
        model="m",
        dataset="d",
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
        baseline_run_id=baseline.report.run_id,  # type: ignore[union-attr]
    )

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "INCONCLUSIVE"
    assert any("Evaluation config mismatch" in reason
               for reason in regression.incompatibility_reasons)


@pytest.mark.asyncio
async def test_run_case_set_mismatch_is_inconclusive() -> None:
    """A limited or changed baseline case set cannot be compared."""
    repository = FakeRepository()
    service, _ = build_service([result()], repository=repository)
    probe = await run(service)
    baseline = ReportingService().build_report(
        [result(case_id="baseline-1"), result(case_id="baseline-2")],
        "v1", "m", "d",
    )
    baseline.eval_config_hash = probe.report.eval_config_hash  # type: ignore[union-attr]
    repository.saved.append(baseline)

    outcome = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
        baseline_run_id=baseline.run_id,
    )

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "INCONCLUSIVE"
    assert any("Case set mismatch" in reason
               for reason in regression.incompatibility_reasons)


@pytest.mark.asyncio
async def test_run_critical_set_mismatch_is_inconclusive() -> None:
    """Changing which cases are critical makes a baseline incomparable."""
    repository = FakeRepository()
    service, _ = build_service([result()], repository=repository)
    probe = await run(service)
    baseline = ReportingService().build_report(
        [result(critical=True)], "v1", "m", "d",
    )
    baseline.eval_config_hash = probe.report.eval_config_hash  # type: ignore[union-attr]
    repository.saved.append(baseline)

    outcome = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
        baseline_run_id=baseline.run_id,
    )

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "INCONCLUSIVE"
    assert any("Critical case set mismatch" in reason
               for reason in regression.incompatibility_reasons)


@pytest.mark.asyncio
async def test_run_empty_current_is_inconclusive_even_with_zero_thresholds() -> None:
    """A limited-to-nothing run must never satisfy a quality gate."""
    service, _ = build_service([])
    outcome = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
    )

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "INCONCLUSIVE"
    assert any("no evaluated cases" in reason
               for reason in regression.incompatibility_reasons)


@pytest.mark.asyncio
async def test_run_critical_failure_without_thresholds_fails() -> None:
    """A critical failure is a gate decision even without configured limits."""
    service, _ = build_service([
        result(category_match=False, case_id="critical-case", critical=True),
    ])
    outcome = await run(service)

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "FAIL"
    assert regression.critical_case_failure


@pytest.mark.asyncio
async def test_run_critical_failure_remains_fail_with_incompatible_baseline() -> None:
    """Critical evidence is decisive even when comparison is unavailable."""
    repository = FakeRepository()
    baseline = ReportingService().build_report(
        [result()], "v1", "other-model", "d",
    )
    repository.saved.append(baseline)
    service, _ = build_service(
        [result(category_match=False, case_id="critical-case", critical=True)],
        repository=repository,
    )

    outcome = await run(service, baseline_run_id=baseline.run_id)

    regression = outcome.report.regression  # type: ignore[union-attr]
    assert regression is not None
    assert regression.status == "FAIL"
    assert regression.critical_case_failure
    assert regression.incompatibility_reasons


@pytest.mark.asyncio
async def test_run_notification_failure_is_recorded_without_changing_gate() -> None:
    """Delivery failure is observable but does not alter the quality decision."""
    service, _ = build_service([result()], notifier=FailingNotifier())
    outcome = await run(
        service,
        thresholds=RegressionThresholds(minimum_category_accuracy=0.0),
    )

    assert outcome.report.regression is not None  # type: ignore[union-attr]
    assert outcome.report.regression.status == "PASS"  # type: ignore[union-attr]
    assert outcome.notification_error == (
        "Notification delivery failed (RuntimeError)."
    )
    assert "webhook-secret-token" not in outcome.notification_error

