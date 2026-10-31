"""Unit tests for Slack drift alerts.

All HTTP interaction is mocked; no real Slack message is ever sent.
"""

import urllib.error
import urllib.request
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.contracts import (
    EvaluationRunRepository,
    Notifier,
)
from promptguard.application.drift_alerts import drift_alert_message
from promptguard.application.drift_service import DriftService
from promptguard.application.reporting_service import ReportingService
from promptguard.application.run_service import EvaluationRunService
from promptguard.application.wiring import build_notifier
from promptguard.config import Settings
from promptguard.domain.models import (
    EvaluationResult,
    GoldenTestCase,
    SlackAlertRecord,
)
from promptguard.domain.regression import RegressionResult
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.infrastructure.notifications.slack import (
    NoopNotifier,
    SlackDeliveryError,
    SlackNotifier,
)
from promptguard.interfaces.api.app import slack_notification_status

WEBHOOK_URL = "https://hooks.slack.com/services/T000/B000/SECRET"

CATEGORY_A = "billing"
CATEGORY_B = "technical"
DRIFT_LIMIT = 0.2


def result(
    case_id: str,
    category: str,
    category_match: bool | None = None,
    expected_category: str = CATEGORY_A,
) -> EvaluationResult:
    """Build a one-case result with the requested normalized category."""
    if category_match is None:
        category_match = category == expected_category
    return EvaluationResult(
        test_case_id=case_id,
        expected_category=expected_category,
        actual_category=category,
        normalized_category=category,
        expected_summary="expected summary",
        actual_summary='summary with <script> & "quotes"',
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
        expected_category=CATEGORY_A,
        ideal_summary="ideal",
        expected_difficulty="easy",
    )


class FakeEvaluationService:
    """Deterministic evaluation service serving the next result batch."""

    def __init__(self, batches: list[list[EvaluationResult]]) -> None:
        self._batches = list(batches)

    async def evaluate_dataset(
        self, test_cases: list[GoldenTestCase], system_prompt: str,
    ) -> list[EvaluationResult]:
        return self._batches.pop(0)


class FakeRepository(EvaluationRunRepository):
    """In-memory repository recording every save call."""

    def __init__(self) -> None:
        self.saved: list = []

    @property
    def skipped_runs(self) -> list[str]:
        return []

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


class RecordingNotifier(Notifier):
    """Notifier that records messages instead of delivering them."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def notify(self, message: str) -> None:
        self.messages.append(message)


def build_run_service(
    batches: list[list[EvaluationResult]],
    repository: FakeRepository,
    notifier: Notifier | None,
) -> EvaluationRunService:
    """Assemble the orchestrator with the given collaborators."""

    from promptguard.application.contracts import ReportFormatter

    class RecordingFormatter(ReportFormatter):
        def format(self, report) -> str:
            return ""

    return EvaluationRunService(
        evaluation_service=FakeEvaluationService(batches),  # type: ignore[arg-type]
        reporting_service=ReportingService(),
        comparison_service=ComparisonService(),
        regression_service=RegressionService(),
        drift_service=DriftService(),
        repository=repository,  # type: ignore[arg-type]
        formatter=RecordingFormatter(),  # type: ignore[arg-type]
        notifier=notifier,
    )


async def run(service: EvaluationRunService, **kwargs):
    """Invoke one orchestrated run with standard metadata."""
    return await service.run(
        test_cases=[golden_case()],
        system_prompt="prompt",
        prompt_version="v1",
        model="m",
        dataset="golden-d",
        **kwargs,
    )


def drift_thresholds(drift: float | None = DRIFT_LIMIT) -> object:
    """Thresholds carrying only the opt-in drift condition."""
    return RegressionThresholds(maximum_category_drift=drift)


def attached_report(
    baseline_categories: list[str],
    candidate_categories: list[str],
    *,
    drift_limit: float | None = DRIFT_LIMIT,
):
    """Build a matched baseline/candidate pair with full drift evidence."""
    baseline = ReportingService().build_report(
        [
            result(f"case-{index}", category)
            for index, category in enumerate(baseline_categories, start=1)
        ],
        "v1", "m", "golden-d",
    )
    candidate = ReportingService().build_report(
        [
            result(f"case-{index}", category)
            for index, category in enumerate(candidate_categories, start=1)
        ],
        "v1", "m", "golden-d",
    )
    candidate.baseline_run_id = baseline.run_id
    candidate.comparison = ComparisonService().compare(baseline, candidate)
    candidate.drift = DriftService().compare(baseline, candidate)
    candidate.thresholds = RegressionThresholds(
        maximum_category_drift=drift_limit,
    )
    return baseline, candidate
    candidate.baseline_run_id = baseline.run_id
    candidate.comparison = ComparisonService().compare(baseline, candidate)
    candidate.drift = DriftService().compare(baseline, candidate)
    candidate.thresholds = RegressionThresholds(
        maximum_category_drift=drift_limit,
    )
    return baseline, candidate


@pytest.mark.asyncio
async def test_confirmed_drift_sends_structured_alert() -> None:
    """A confirmed drift condition sends exactly one structured alert."""
    repository = FakeRepository()
    notifier = RecordingNotifier()
    baseline_batch = [
        result("case-1", CATEGORY_A, True),
        result("case-2", CATEGORY_A, True),
        result("case-3", CATEGORY_A, True),
        result("case-4", CATEGORY_A, True),
    ]
    drifted_batch = [
        result("case-1", CATEGORY_A, True),
        result("case-2", CATEGORY_A, True),
        result("case-3", CATEGORY_B, False),
        result("case-4", CATEGORY_B, False),
    ]
    service = build_run_service(
        [baseline_batch, drifted_batch], repository, notifier,
    )
    baseline_outcome = await run(
        service, thresholds=RegressionThresholds(),
    )
    outcome = await run(
        service,
        baseline_run_id="latest",
        thresholds=drift_thresholds(DRIFT_LIMIT),  # type: ignore[arg-type]
    )
    drift = outcome.report.drift
    assert drift is not None and drift.sufficient_data
    assert drift.total_variation_distance == 0.5
    candidate_run_id = outcome.report.run_id
    baseline_run_id = outcome.report.baseline_run_id
    assert baseline_run_id == baseline_outcome.report.run_id
    assert len(notifier.messages) == 2
    drift_message = notifier.messages[-1]
    assert drift_message.startswith("PromptGuard drift alert")
    assert f"Candidate run: {candidate_run_id}" in drift_message
    assert f"Baseline run: {baseline_run_id}" in drift_message
    assert "exceeded the configured maximum (0.2000)" in drift_message
    assert f"- {CATEGORY_A}: 100.0% -> 50.0% (-50.0%)" in drift_message
    assert f"- {CATEGORY_B}: 0.0% -> 50.0% (+50.0%)" in drift_message
    assert "Report:" not in drift_message
    assert "hooks.slack.com" not in drift_message
    assert outcome.report.slack_alert is not None
    assert outcome.report.slack_alert.status == "delivered"
    assert outcome.report.slack_alert.error is None
    assert outcome.report.slack_alert.baseline_run_id == baseline_run_id
    matched = [r for r in repository.saved if r.run_id == candidate_run_id]
    assert len(matched) == 2
    assert matched[-1].slack_alert.status == "delivered"
    # The final persisted evaluation content is unchanged by delivery.
    assert matched[-1].category_accuracy == outcome.report.category_accuracy


@pytest.mark.asyncio
async def test_below_threshold_drift_sends_completed_notice_only() -> None:
    """A drift inside the configured limit sends no drift alert."""
    repository = FakeRepository()
    notifier = RecordingNotifier()
    baseline_batch = [
        result("case-1", CATEGORY_A, True),
        result("case-2", CATEGORY_A, True),
    ]
    small_drift_batch = [
        result("case-1", CATEGORY_A, True),
        result("case-2", CATEGORY_B, False),
    ]
    service = build_run_service(
        [baseline_batch, small_drift_batch], repository, notifier,
    )
    baseline_outcome = await run(
        service, thresholds=RegressionThresholds(),
    )
    outcome = await run(
        service,
        baseline_run_id=baseline_outcome.report.run_id,
        thresholds=drift_thresholds(0.6),  # type: ignore[arg-type]
    )
    drift = outcome.report.drift
    assert drift is not None and drift.total_variation_distance == 0.5
    decision = drift_alert_message(outcome.report, report_url_prefix=None)
    assert decision is None
    assert outcome.report.slack_alert is None
    assert len(notifier.messages) == 2
    assert notifier.messages[-1].startswith(
        f"PromptGuard run {outcome.report.run_id} completed.",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("baseline_exists", [False, True])
async def test_missing_or_incompatible_baseline_sends_no_drift_alert(
    baseline_exists: bool,
) -> None:
    """Missing and incompatible baselines never confirm drift."""
    repository = FakeRepository()
    notifier = RecordingNotifier()
    drifted_batch = [result("case-1", CATEGORY_B, False)]
    if baseline_exists:
        incompatible = ReportingService().build_report(
            [result("case-1", CATEGORY_A, True)], "v1", "judge-model",
            "golden-d",
        )
        repository.saved.append(incompatible)
        requested = incompatible.run_id
        batches = [
            [result("case-1", CATEGORY_A, True)], drifted_batch,
        ]
    else:
        requested = "missing"
        batches = [drifted_batch]
    service = build_run_service(batches, repository, notifier)
    for _ in batches:
        await run(
            service,
            baseline_run_id=requested,
            thresholds=drift_thresholds(DRIFT_LIMIT),  # type: ignore[arg-type]
        )
    assert all(
        "PromptGuard drift alert" not in message
        for message in notifier.messages
    )


def test_inconclusive_gate_does_not_block_drift_alert() -> None:
    """A gate INCONCLUSIVE from threshold-only config still alerts."""
    _, candidate = attached_report(
        [CATEGORY_A, CATEGORY_A], [CATEGORY_B, CATEGORY_B],
    )
    candidate.regression = RegressionResult(
        status="INCONCLUSIVE",
        incompatibility_reasons=[
            (
                "No thresholds configured (baseline provided but no drop "
                "thresholds set)"
            ),
        ],
    )
    assert drift_alert_message(candidate, None) is not None


def test_unconfigured_threshold_sends_no_drift_alert() -> None:
    """Without a configured drift condition no alert may ever fire."""
    _, candidate = attached_report(
        [CATEGORY_A, CATEGORY_A],
        [CATEGORY_B, CATEGORY_B],
        drift_limit=None,
    )
    assert drift_alert_message(candidate, None) is None


def test_no_baseline_comparison_sends_no_drift_alert() -> None:
    """Drift evidence without a comparison must not alert."""
    baseline, candidate = attached_report(
        [CATEGORY_A] * 4, [CATEGORY_A] * 4,
    )
    candidate.comparison = None
    candidate.baseline_run_id = None
    del baseline
    assert candidate.drift is not None
    assert drift_alert_message(candidate, None) is None


def test_repeated_processing_of_delivered_event_is_skipped() -> None:
    """Delivered events dedupe; failed attempts and other baselines retry."""
    _, candidate = attached_report([CATEGORY_A] * 4, [CATEGORY_B] * 4)
    baseline_id = candidate.baseline_run_id
    assert drift_alert_message(candidate, None) is not None
    candidate.slack_alert = SlackAlertRecord(
        baseline_run_id=baseline_id,
        status="delivered",
        recorded_at=datetime.now(UTC),
    )
    assert drift_alert_message(candidate, None) is None
    candidate.slack_alert = SlackAlertRecord(
        baseline_run_id=baseline_id,
        status="failed",
        error="SlackDeliveryError",
        recorded_at=datetime.now(UTC),
    )
    assert drift_alert_message(candidate, None) is not None
    other_baseline = ReportingService().build_report(
        [result("case-9", CATEGORY_A)], "v1", "m", "golden-d",
    )
    candidate.slack_alert = SlackAlertRecord(
        baseline_run_id=other_baseline.run_id,
        status="delivered",
        recorded_at=datetime.now(UTC),
    )
    candidate.baseline_run_id = other_baseline.run_id
    assert drift_alert_message(candidate, None) is None


def test_drift_alert_bounds_and_sanitizes_untrusted_labels() -> None:
    """Untrusted category labels are flattened, truncated, and inert."""
    noisy = "<script>alert(1)</script> " + "x" * 80
    _, candidate = attached_report([CATEGORY_A], [noisy])
    message = drift_alert_message(candidate, None)
    assert message is not None
    listed = [
        line for line in message.splitlines() if line.startswith("- ")
    ]
    assert len(listed) == 2
    noisy_line = next(
        line for line in listed if line != f"- {CATEGORY_A}: "
        "100.0% -> 0.0% (-100.0%)"
    )
    label = noisy_line[2:].split(":")[0]
    assert "\n" not in label
    assert len(label) <= 40
    assert label.startswith("<script>alert(1)</script>")
    assert label.endswith("…")


def test_drift_alert_includes_report_link_only_when_configured() -> None:
    """The report link requires a configured dashboard URL prefix."""
    _, candidate = attached_report([CATEGORY_A] * 2, [CATEGORY_A, CATEGORY_B])
    no_link = drift_alert_message(candidate, report_url_prefix=None)
    assert no_link is not None
    assert "Report:" not in no_link
    with_link = drift_alert_message(
        candidate, report_url_prefix="https://dash.example.com/",
    )
    assert with_link is not None
    assert (
        f"Report: https://dash.example.com/runs/{candidate.run_id}"
        in with_link
    )


def test_disabled_or_unconfigured_slack_keeps_runs_functional() -> None:
    """No webhook or an explicit disable yields the no-op notifier."""

    def settings(url: str | None, enabled: bool) -> Settings:
        return Settings(  # type: ignore[call-arg]
            nvidia_api_key="k",
            nvidia_model="m",
            slack_webhook_url=url,
            slack_notifications_enabled=enabled,
        )

    assert isinstance(build_notifier(settings(None, True)), NoopNotifier)
    assert isinstance(
        build_notifier(settings(WEBHOOK_URL, False)), NoopNotifier,
    )
    notifier = build_notifier(settings(WEBHOOK_URL, True))
    assert isinstance(notifier, SlackNotifier)
    assert notifier._timeout_seconds == 10.0
    assert notifier._max_attempts == 3


def test_slack_notification_status_never_exposes_secret() -> None:
    """Status vocabulary covers enabled/disabled/unconfigured only."""

    def settings(url: str | None, enabled: bool) -> Settings:
        return Settings(  # type: ignore[call-arg]
            nvidia_api_key="k",
            nvidia_model="m",
            slack_webhook_url=url,
            slack_notifications_enabled=enabled,
        )

    assert slack_notification_status(settings(None, True)) == "unconfigured"
    assert (
        slack_notification_status(settings(WEBHOOK_URL, False)) == "disabled"
    )
    assert slack_notification_status(settings(WEBHOOK_URL, True)) == "enabled"


@pytest.mark.asyncio
async def test_slack_notifier_posts_json_text_payload() -> None:
    """Delivery must POST JSON text with the configured timeout."""
    recorded: dict = {}

    def fake_urlopen(request, timeout=None):
        recorded["url"] = request.full_url
        recorded["data"] = request.data
        recorded["timeout"] = timeout
        return _FakeResponse()

    with patch.object(urllib.request, "urlopen", fake_urlopen):
        await SlackNotifier(
            WEBHOOK_URL, timeout_seconds=5, max_attempts=1,
        ).notify("drift happened")

    assert recorded["url"] == WEBHOOK_URL
    assert b"drift happened" in recorded["data"]
    assert recorded["timeout"] == 5


class _FakeResponse:
    """Minimal successful webhook response usable as a context manager."""

    def read(self) -> bytes:
        return b"ok"

    def __enter__(self) -> "_FakeResponse":  # noqa: PYI034
        return self

    def __exit__(self, *exc_types: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_slack_notifier_retries_transient_failures_then_succeeds() -> None:
    """429/5xx and network errors retry within the bounded attempt count."""
    calls: list[int] = []

    def flaky(_request, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(
                WEBHOOK_URL, 503, "unavailable", {}, None,
            )
        return _FakeResponse()

    with (
        patch.object(urllib.request, "urlopen", flaky),
        patch(
            "promptguard.infrastructure.notifications.slack.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        await SlackNotifier(WEBHOOK_URL, timeout_seconds=5).notify("m")

    assert len(calls) == 3


@pytest.mark.asyncio
async def test_slack_notifier_does_not_retry_permanent_rejections() -> None:
    """A non-retryable 4xx fails once without additional attempts."""
    calls: list[int] = []

    def rejected(_request, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError(WEBHOOK_URL, 400, "bad", {}, None)

    with (
        patch.object(urllib.request, "urlopen", rejected),
        patch(
            "promptguard.infrastructure.notifications.slack.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock,pytest.raises(SlackDeliveryError, match="status=400")
    ):
        await SlackNotifier(
            WEBHOOK_URL, timeout_seconds=5, max_attempts=3,
        ).notify("m")

    assert len(calls) == 1
    sleep_mock.assert_not_called()


@pytest.mark.asyncio
async def test_slack_notifier_gives_up_after_bounded_attempts() -> None:
    """Persistently down Slack yields a sanitized error, not a URL leak."""
    calls: list[int] = []
    timeouts: list[float] = []

    def always_down(_request, timeout=None):
        calls.append(1)
        timeouts.append(timeout)
        raise urllib.error.URLError("connection refused")

    with (
        patch.object(urllib.request, "urlopen", always_down),
        patch(
            "promptguard.infrastructure.notifications.slack.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        notifier = SlackNotifier(
            WEBHOOK_URL, timeout_seconds=5, max_attempts=3,
        )
        with pytest.raises(SlackDeliveryError) as excinfo:
            await notifier.notify("m")

    assert len(calls) == 3
    assert all(timeout == 5 for timeout in timeouts)
    failure_text = str(excinfo.value)
    assert "after 3 attempts" in failure_text
    assert WEBHOOK_URL not in failure_text
    assert "connection refused" not in failure_text


@pytest.mark.asyncio
async def test_delivery_failure_records_status_without_gate_change() -> None:
    """A Slack outage leaves the persisted evaluation result untouched."""
    repository = FakeRepository()
    notifier = SlackNotifier(
        WEBHOOK_URL, timeout_seconds=5, max_attempts=1,
    )

    def denied(_request, timeout=None):
        raise urllib.error.HTTPError(WEBHOOK_URL, 400, "bad", {}, None)

    with patch.object(urllib.request, "urlopen", denied):
        service = build_run_service(
            [[result("case-1", CATEGORY_A, True)]], repository, notifier,
        )
        outcome = await run(service)

    assert outcome.report.regression is not None
    assert outcome.report.regression.status == "INCONCLUSIVE"
    assert outcome.report.slack_alert is None
    assert outcome.notification_error == (
        "Notification delivery failed (SlackDeliveryError)."
    )
    assert WEBHOOK_URL not in outcome.notification_error
