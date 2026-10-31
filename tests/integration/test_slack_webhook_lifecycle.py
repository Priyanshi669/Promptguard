"""Slack lifecycle verification against a real local HTTP webhook stub.

This proves the actual notification seam (a webhook endpoint responding
over real HTTP) without ever contacting Slack: the stub listens on the
loopback interface and records every delivery attempt. No real Slack
message is sent; nothing outside the test's own port is contacted.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http import server as http_server
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.contracts import ReportFormatter
from promptguard.application.drift_alerts import drift_alert_message
from promptguard.application.drift_service import DriftService
from promptguard.application.reporting_service import ReportingService
from promptguard.application.run_service import EvaluationRunService
from promptguard.application.wiring import build_notifier
from promptguard.domain.models import EvaluationResult, GoldenTestCase
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.infrastructure.notifications.slack import (
    SlackDeliveryError,
    SlackNotifier,
)
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.interfaces.api.app import slack_notification_status

DRIFT_LIMIT = 0.2
CATEGORY_A = "billing"
CATEGORY_B = "technical"


@dataclass
class StubState:
    """Delivery attempts observed by the stub endpoint."""

    deliveries: list[dict[str, Any]] = field(default_factory=list)
    failures_before_success: int = 0
    always_status: int | None = None


class WebhookStubHandler(http_server.BaseHTTPRequestHandler):
    """Answer POSTs per the currently configured fail policy."""

    server_version = "PromptGuardWebhookStub/1.0"
    protocol_version = "HTTP/1.1"

    state: ClassVar[StubState] = StubState()
    url: ClassVar[str] = ""

    def do_POST(self) -> None:
        """Record one delivery attempt, then respond per the policy."""
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length)
        failed_so_far = sum(
            1
            for entry in WebhookStubHandler.state.deliveries
            if entry["status"] != 200
        )
        if WebhookStubHandler.state.always_status is not None:
            status_code = WebhookStubHandler.state.always_status
        elif failed_so_far < WebhookStubHandler.state.failures_before_success:
            status_code = 500
        else:
            status_code = 200
        entry: dict[str, Any] = {
            "body": body.decode("utf-8"),
            "text": None,
            "status": status_code,
        }
        try:
            entry["text"] = json.loads(entry["body"]).get("text")
        except json.JSONDecodeError:
            pass
        WebhookStubHandler.state.deliveries.append(entry)
        self.send_response(status_code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        """Silence request logging; the stub records everything."""


@pytest.fixture
def webhook_stub(monkeypatch):
    """Run the stub server on a free loopback port; bypass the proxy."""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    WebhookStubHandler.state = StubState()
    httpd = http_server.ThreadingHTTPServer(
        ("127.0.0.1", 0),  # any free loopback port
        WebhookStubHandler,
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    host, port = httpd.server_address
    url = f"http://{host}:{port}/"

    class _Stub:
        def __init__(self) -> None:
            self.url = url
            self.deliveries = WebhookStubHandler.state.deliveries

        def fail_first(self, count: int) -> None:
            WebhookStubHandler.state.failures_before_success = count

        def always(self, status: int) -> None:
            WebhookStubHandler.state.always_status = status

    stub = _Stub()
    try:
        yield stub
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture
def settings_with_stub(webhook_stub):
    """Settings-like object standing in for configured Slack settings."""
    return SimpleNamespace(
        slack_notifications_enabled=True,
        slack_webhook_url=webhook_stub.url,
        slack_timeout_seconds=5.0,
        slack_max_attempts=3,
    )


def result(
    case_id: str, category: str, expected_category: str = CATEGORY_A,
) -> EvaluationResult:
    """One valid evaluation result for the drift pair."""
    return EvaluationResult(
        test_case_id=case_id,
        expected_category=expected_category,
        actual_category=category,
        normalized_category=category,
        expected_summary="expected summary",
        actual_summary="actual summary",
        category_match=category == expected_category,
        summary_score=4.0,
        latency_ms=20.0,
        input_tokens=3,
        output_tokens=2,
    )


def batches_for(series: list[str]) -> list[EvaluationResult]:
    """Build one case per entry in the category sequence."""
    return [
        result(f"case-{index + 1}", category)
        for index, category in enumerate(series)
    ]


class RecordingFormatter(ReportFormatter):
    def format(self, report: object) -> str:
        return ""


class QueueingEvaluationService:
    """Serve one result batch per orchestrated run."""

    def __init__(self) -> None:
        self.batches: list[list[EvaluationResult]] = []

    async def evaluate_dataset(
        self, test_cases: list[GoldenTestCase], system_prompt: str,
    ) -> list[EvaluationResult]:
        return self.batches.pop(0)


class ServiceHarness:
    """Orchestrator + queued batches + shared repository access."""

    def __init__(self, tmp_path: Any, notifier: Any) -> None:
        self.repository = JsonEvaluationRunRepository(tmp_path)
        self.evaluation = QueueingEvaluationService()
        self.service = EvaluationRunService(
            evaluation_service=self.evaluation,
            reporting_service=ReportingService(),
            comparison_service=ComparisonService(),
            regression_service=RegressionService(),
            drift_service=DriftService(),
            repository=self.repository,
            formatter=RecordingFormatter(),
            notifier=notifier,
        )
        self.case = GoldenTestCase(
            id="case",
            input="input",
            expected_category=CATEGORY_A,
            ideal_summary="ideal",
            expected_difficulty="easy",
        )

    async def run(self, series: list[str], **kwargs: Any):
        self.evaluation.batches.append(batches_for(series))
        return await self.service.run(
            test_cases=[self.case],
            system_prompt="prompt",
            prompt_version="v1",
            model="m",
            dataset="golden-d",
            **kwargs,
        )


@pytest.mark.asyncio
async def test_wiring_and_status_enumerate_all_states(
    settings_with_stub,
) -> None:
    """Wiring maps settings to the notifier; status stays honest."""
    notifier = build_notifier(settings_with_stub)
    assert isinstance(notifier, SlackNotifier)
    assert slack_notification_status(settings_with_stub) == "enabled"

    unconfigured = SimpleNamespace(
        slack_notifications_enabled=True,
        slack_webhook_url=None,
        slack_timeout_seconds=5.0,
        slack_max_attempts=3,
    )
    from promptguard.infrastructure.notifications.slack import NoopNotifier

    assert isinstance(
        build_notifier(unconfigured),  # type: ignore[arg-type]
        NoopNotifier,
    )
    assert slack_notification_status(unconfigured) == "unconfigured"

    disabled = SimpleNamespace(
        slack_notifications_enabled=False,
        slack_webhook_url=settings_with_stub.slack_webhook_url,
        slack_timeout_seconds=5.0,
        slack_max_attempts=3,
    )
    assert slack_notification_status(disabled) == "disabled"


async def _run_pair(harness: ServiceHarness):
    """Baseline run then a drifted candidate (TVD 0.5 > 0.2 limit)."""
    baseline = await harness.run(
        [CATEGORY_A, CATEGORY_A, CATEGORY_A, CATEGORY_A],
        thresholds=RegressionThresholds(),
    )
    candidate = await harness.run(
        [CATEGORY_A, CATEGORY_A, CATEGORY_B, CATEGORY_B],
        baseline_run_id="latest",
        thresholds=RegressionThresholds(
            maximum_category_drift=DRIFT_LIMIT,
        ),
    )
    return baseline, candidate


@pytest.mark.asyncio
async def test_drift_pair_delivers_single_alert_over_real_http(
    tmp_path, settings_with_stub, webhook_stub,
) -> None:
    """One baseline, one drift crossing -> exactly one HTTP alert body."""
    harness = ServiceHarness(tmp_path, build_notifier(settings_with_stub))
    baseline, candidate = await _run_pair(harness)

    assert len(webhook_stub.deliveries) == 2
    generic, alert = webhook_stub.deliveries
    assert generic["status"] == 200
    assert not generic["text"].startswith("PromptGuard drift alert")
    assert alert["status"] == 200
    assert alert["text"].startswith("PromptGuard drift alert")
    assert candidate.comparison is not None
    assert f"Candidate run: {candidate.report.run_id}" in alert["text"]
    assert (
        f"Baseline run: {baseline.report.run_id}" in alert["text"]
    )

    persisted = harness.repository.get(candidate.report.run_id)
    assert persisted.slack_alert is not None
    assert persisted.slack_alert.status == "delivered"
    assert persisted.slack_alert.error is None
    assert persisted.slack_alert.baseline_run_id == baseline.report.run_id


@pytest.mark.asyncio
async def test_replay_of_delivered_event_never_resends_and_new_events_fire(
    tmp_path, settings_with_stub, webhook_stub,
) -> None:
    """The persisted delivered record suppresses resend on replay, while
    a genuinely new drift event against the same baseline still fires."""
    harness = ServiceHarness(tmp_path, build_notifier(settings_with_stub))
    _baseline, candidate = await _run_pair(harness)
    assert len(webhook_stub.deliveries) == 2

    persisted = harness.repository.get(candidate.report.run_id)
    # Replay of the exact same event: no resend decision, no extra POST.
    assert drift_alert_message(persisted) is None
    assert len(webhook_stub.deliveries) == 2

    # A genuinely new candidate drifting against the same baseline may
    # alert again; dedupe is per event, not per baseline.
    second = await harness.run(
        [CATEGORY_A, CATEGORY_B, CATEGORY_A, CATEGORY_B],
        baseline_run_id=persisted.slack_alert.baseline_run_id,
        thresholds=RegressionThresholds(
            maximum_category_drift=DRIFT_LIMIT,
        ),
    )
    assert second.report.drift.total_variation_distance == 0.5
    assert len(webhook_stub.deliveries) == 3
    assert webhook_stub.deliveries[-1]["text"].startswith(
        "PromptGuard drift alert",
    )


@pytest.mark.asyncio
async def test_transient_5xx_retries_then_delivers(
    webhook_stub, monkeypatch,
) -> None:
    """Bound retries recover from transient 500s; payload preserved."""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    webhook_stub.fail_first(2)
    notifier = SlackNotifier(
        webhook_stub.url,
        timeout_seconds=5.0,
        max_attempts=3,
    )
    await notifier.notify("PromptGuard retry case")
    assert len(webhook_stub.deliveries) == 3
    assert [entry["status"] for entry in webhook_stub.deliveries] == [
        500, 500, 200,
    ]
    assert webhook_stub.deliveries[-1]["text"] == "PromptGuard retry case"


@pytest.mark.asyncio
async def test_permanent_4xx_fails_without_retries(
    webhook_stub, monkeypatch,
) -> None:
    """A non-retryable 4xx fails immediately with a sanitized error."""
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    webhook_stub.always(400)
    notifier = SlackNotifier(
        webhook_stub.url,
        timeout_seconds=5.0,
        max_attempts=3,
    )
    with pytest.raises(SlackDeliveryError) as excinfo:
        await notifier.notify("PromptGuard 4xx case")
    assert len(webhook_stub.deliveries) == 1
    assert "400" in str(excinfo.value)
    assert webhook_stub.url not in str(excinfo.value)
