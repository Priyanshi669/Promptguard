"""Drift-alert decision and message rendering.

This module only consumes drift evidence that the existing
``DriftService`` already produced and stored on the report. It computes
no drift itself. A confirmed drift alert requires an explicitly
configured condition (``thresholds.maximum_category_drift``); without a
configured threshold no drift alert is ever sent — that capability is
simply absent, and this is reported honestly in the code and docs.

Quality-gate failures are deliberately NOT treated as drift.
"""

from promptguard.domain.drift import CategoryDriftResult
from promptguard.domain.models import EvaluationReport
from promptguard.domain.thresholds import RegressionThresholds

MAX_LISTED_CATEGORIES = 8
MAX_CATEGORY_NAME_LENGTH = 40


def drift_alert_message(
    report: EvaluationReport,
    report_url_prefix: str | None = None,
) -> str | None:
    """Return the drift-alert text, or None when no alert must be sent.

    A confirmed drift alert requires all of the following evidence:

    - a baseline was found and its evaluation config matched, so the
      runner recorded a comparison (when the baseline is incompatible
      the runner leaves ``report.drift`` unset, so no alert can be
      sent);
    - the stored ``CategoryDriftResult`` has sufficient data on both
      sides;
    - the run carried ``thresholds.maximum_category_drift`` — the only
      configured drift condition that exists;
    - the observed total-variation distance strictly exceeds that
      configured limit;
    - no delivery record for this baseline already exists (replay
      dedupe: a repeated processing of the same drift event must not
      send a second alert).

    The decision is deliberately independent of the release gate: the
    regression status is reported in the alert body, but a gate that is
    inconclusive (for example because only a drift threshold was
    configured) or passing does not suppress or create a drift alert.
    """
    thresholds = report.thresholds
    if thresholds is None or thresholds.maximum_category_drift is None:
        return None
    drift = report.drift
    if drift is None or not drift.sufficient_data:
        return None
    distance = drift.total_variation_distance
    if distance is None:
        return None
    if report.comparison is None:
        return None
    if report.baseline_run_id is None:
        return None
    if distance <= thresholds.maximum_category_drift:
        return None
    alert = report.slack_alert
    if (
        alert is not None
        and alert.status == "delivered"
        and alert.baseline_run_id == report.baseline_run_id
    ):
        return None
    return _render(
        report, distance, drift, thresholds, report_url_prefix,
    )


def _render(
    report: EvaluationReport,
    distance: float,
    drift: CategoryDriftResult,
    thresholds: RegressionThresholds,
    report_url_prefix: str | None,
) -> str:
    """Render the concise, bounded, plain-text drift alert."""
    baseline_distribution = dict(drift.baseline_distribution)
    current_distribution = dict(drift.current_distribution)
    categories = baseline_distribution.keys() | current_distribution.keys()
    changes = sorted(
        (
            (category, current_distribution.get(category, 0.0)
             - baseline_distribution.get(category, 0.0))
            for category in categories
        ),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    lines: list[str] = [
        "PromptGuard drift alert",
        f"Candidate run: {report.run_id}",
        f"Baseline run: {report.baseline_run_id}",
        (
            "Trigger: category-distribution drift "
            f"(total variation {distance:.4f}) exceeded the configured "
            f"maximum ({thresholds.maximum_category_drift:.4f})."
        ),
        "Affected categories (baseline -> current share):",
    ]
    for category, delta in changes[:MAX_LISTED_CATEGORIES]:
        baseline_share = baseline_distribution.get(category, 0.0)
        current_share = current_distribution.get(category, 0.0)
        lines.append(
            f"- {_sanitize(category)}: "
            f"{baseline_share:.1%} -> {current_share:.1%} ({delta:+.1%})"
        )
    if len(changes) > MAX_LISTED_CATEGORIES:
        lines.append(
            f"+{len(changes) - MAX_LISTED_CATEGORIES} more changed "
            "categories not listed"
        )
    comparison = report.comparison
    if comparison is not None:
        lines.append(
            "Comparison context: "
            f"category accuracy {comparison.baseline_category_accuracy:.1%}"
            f" -> {comparison.current_category_accuracy:.1%}, "
            f"summary score {comparison.baseline_summary_score:.2f}"
            f" -> {comparison.current_summary_score:.2f}."
        )
    if report.regression is not None:
        lines.append(f"Quality-gate status: {report.regression.status}")
    link = _report_link(report, report_url_prefix)
    if link is not None:
        lines.append(f"Report: {link}")
    return "\n".join(lines)


def _report_link(
    report: EvaluationReport, report_url_prefix: str | None,
) -> str | None:
    """Return a report URL only when an application URL was provided."""
    if not report_url_prefix or not report_url_prefix.strip():
        return None
    base = report_url_prefix.strip().rstrip("/")
    return f"{base}/runs/{report.run_id}"


def _sanitize(text: str) -> str:
    """Bound and flatten untrusted labels for safe single-line output."""
    flat = " ".join(str(text).split())
    if len(flat) > MAX_CATEGORY_NAME_LENGTH:
        flat = flat[: MAX_CATEGORY_NAME_LENGTH - 1] + "…"
    return flat
