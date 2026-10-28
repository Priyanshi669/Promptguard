"""Unit tests for report formatting and notification adapters."""

import urllib.request
from unittest.mock import patch

import pytest

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.drift import CategoryDriftResult
from promptguard.domain.models import EvaluationReport, EvaluationResult
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.infrastructure.notifications.slack import NoopNotifier, SlackNotifier
from promptguard.infrastructure.reporting.html_renderer import TextReportFormatter


def result(category_match: bool = True, category: str = "technical") -> EvaluationResult:
    """Build a concise result fixture."""
    return EvaluationResult(
        test_case_id="case-1",
        expected_category="billing",
        actual_category="billing" if category_match else category,
        expected_summary="expected summary",
        actual_summary="actual summary",
        category_match=category_match,
        summary_score=4.0,
        latency_ms=20.0,
        input_tokens=3,
        output_tokens=2,
    )


def build_report() -> EvaluationReport:
    """Build a failing report with one violation and one mismatch."""
    report = ReportingService().build_report(
        [result(False)], "v1", "model-x", "golden-v1",
    )
    report.regression = RegressionService().evaluate(
        report,
        RegressionThresholds(minimum_category_accuracy=1.0),
    )
    return report


def test_formatter_shows_header_and_metrics() -> None:
    """The report should contain run metadata and every aggregate metric."""
    report = ReportingService().build_report([result()], "v2", "model-x", "golden-v1")
    text = TextReportFormatter().format(report)
    assert "PromptGuard Evaluation Report" in text
    assert f"Run ID: {report.run_id}" in text
    assert "Prompt version: v2" in text
    assert "Model: model-x" in text
    assert "Dataset: golden-v1" in text
    assert "Cases evaluated: 1" in text
    assert "Category accuracy: 100.00%" in text
    assert "Average summary score: 4.00/5" in text
    assert "Average latency: 20.00ms" in text
    assert "Input tokens: 3" in text
    assert "Output tokens: 2" in text


def test_formatter_shows_regression_status_and_violations() -> None:
    """Failing runs should list the regression status and each violation."""
    text = TextReportFormatter().format(build_report())
    assert "Regression status: FAIL" in text
    assert "Threshold violations:" in text
    assert "category_accuracy" in text


def test_formatter_shows_passing_regression() -> None:
    """A passing run should report PASS with no violations."""
    report = ReportingService().build_report([result()], "v1", "m", "d")
    report.regression = RegressionService().evaluate(
        report, RegressionThresholds(minimum_category_accuracy=0.0),
    )
    text = TextReportFormatter().format(report)
    assert "Regression status: PASS" in text
    assert "Threshold violations: none" in text


def test_formatter_reports_inconclusive_status_and_reasons() -> None:
    """An inconclusive gate should not be rendered as a failure."""
    report = ReportingService().build_report([result()], "v1", "m", "d")
    report.regression = RegressionService().evaluate(
        report, RegressionThresholds(),
    )
    text = TextReportFormatter().format(report)
    assert "Regression status: INCONCLUSIVE" in text
    assert "Incompatibility:" in text


def test_formatter_shows_eval_config_hash() -> None:
    """The reproducibility fingerprint should be visible in the report."""
    report = ReportingService().build_report(
        [result()], "v1", "m", "d", eval_config_hash="abc123",
    )
    text = TextReportFormatter().format(report)
    assert "Eval config hash: abc123" in text


def test_formatter_shows_gate_thresholds() -> None:
    """Persisted gate policy should be visible with the report."""
    thresholds = RegressionThresholds(minimum_category_accuracy=0.8)
    report = ReportingService().build_report(
        [result()], "v1", "m", "d", thresholds=thresholds,
    )
    text = TextReportFormatter().format(report)
    assert "Gate thresholds:" in text
    assert "minimum_category_accuracy: 0.8" in text


def test_formatter_reports_unevaluated_regression() -> None:
    """Without thresholds the report should say regression was not evaluated."""
    report = ReportingService().build_report([result()], "v1", "m", "d")
    text = TextReportFormatter().format(report)
    assert "Regression status: not evaluated" in text


def test_formatter_lists_failure_debug_details() -> None:
    """Each mismatch should expose expected vs actual for debugging."""
    text = TextReportFormatter().format(build_report())
    assert "Individual failures: 1" in text
    assert "Test case: case-1" in text
    assert "Expected category: billing" in text
    assert "Actual category: technical" in text
    assert "Expected summary: expected summary" in text
    assert "Actual summary: actual summary" in text
    assert "Summary score: 4.00" in text


def test_formatter_shows_drift_line() -> None:
    """Drift produced by the orchestrator should appear in the report."""
    report = ReportingService().build_report([result()], "v1", "m", "d")
    report.drift = CategoryDriftResult(
        sufficient_data=True, total_variation_distance=0.25,
    )
    text = TextReportFormatter().format(report)
    assert "Category drift" in text


def test_formatter_shows_baseline_run() -> None:
    """The baseline run id should be displayed when a comparison ran."""
    report = ReportingService().build_report([result()], "v1", "m", "d")
    report.baseline_run_id = "abc-123"
    text = TextReportFormatter().format(report)
    assert "Baseline run: abc-123" in text


def test_formatter_shows_persisted_comparison_deltas() -> None:
    """Comparison evidence should be visible after a baseline comparison."""
    baseline = ReportingService().build_report(
        [result()], "v1", "m", "d",
    )
    current = ReportingService().build_report(
        [result(False)], "v1", "m", "d",
    )
    current.baseline_run_id = baseline.run_id
    current.comparison = ComparisonService().compare(baseline, current)

    text = TextReportFormatter().format(current)

    assert "Comparison deltas:" in text
    assert "Category accuracy: -100.00%" in text
    assert "(baseline 100.00%, current 0.00%)" in text


@pytest.mark.asyncio
async def test_noop_notifier_accepts_messages() -> None:
    """The safe default notifier should never raise or send."""
    await NoopNotifier().notify("hello")


@pytest.mark.asyncio
async def test_slack_notifier_posts_to_webhook() -> None:
    """Slack delivery should POST JSON text to the configured webhook."""
    recorded = {}

    def fake_urlopen(request, timeout=None):
        recorded["url"] = request.full_url
        recorded["data"] = request.data
        recorded["timeout"] = timeout

        class _FakeResponse:
            def read(self) -> bytes:
                return b"ok"

            def __enter__(self) -> "_FakeResponse":  # noqa: PYI034
                return self

            def __exit__(self, *exc_types: object) -> bool:
                return False

        return _FakeResponse()

    with patch.object(urllib.request, "urlopen", fake_urlopen):
        await SlackNotifier("https://hooks.example.com/tok").notify("run done")

    assert recorded["url"].startswith("https://hooks.example.com/tok")
    assert b"run done" in recorded["data"]
    assert recorded["timeout"] == 10
