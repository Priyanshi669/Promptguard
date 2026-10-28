"""Orchestration of complete evaluation runs."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.contracts import (
    EvaluationRunRepository,
    Notifier,
    ReportFormatter,
)
from promptguard.application.drift_alerts import drift_alert_message
from promptguard.application.drift_service import DriftService
from promptguard.application.evaluation_service import EvaluationService
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import (
    EvaluationReport,
    EvaluationResult,
    GoldenTestCase,
    SlackAlertRecord,
)
from promptguard.domain.paired import paired_baseline_analysis
from promptguard.domain.regression import EvaluationComparison
from promptguard.domain.statistics import BootstrapConfig
from promptguard.domain.thresholds import RegressionThresholds

LATEST_BASELINE = "latest"


class EvaluationRunOutcome(BaseModel):
    """Everything produced by one orchestrated evaluation run."""

    report: EvaluationReport
    comparison: EvaluationComparison | None = None
    report_text: str | None = None
    notification_error: str | None = None
    skipped_runs: list[str] = Field(default_factory=list)


class EvaluationRunService:
    """Coordinate evaluation, aggregation, comparison, and reporting.

    Each responsibility is delegated to its own component; this service
    only sequences them and carries the run's outcome.
    """

    def __init__(
        self,
        evaluation_service: EvaluationService,
        reporting_service: ReportingService | None = None,
        comparison_service: ComparisonService | None = None,
        regression_service: RegressionService | None = None,
        drift_service: DriftService | None = None,
        repository: EvaluationRunRepository | None = None,
        formatter: ReportFormatter | None = None,
        notifier: Notifier | None = None,
        drift_alert_url_prefix: str | None = None,
    ) -> None:
        """Initialize the orchestrator with its collaborators."""
        self._evaluation_service = evaluation_service
        self._reporting_service = reporting_service or ReportingService()
        self._comparison_service = comparison_service or ComparisonService()
        self._regression_service = regression_service or RegressionService()
        self._drift_service = drift_service or DriftService()
        self._repository = repository
        self._formatter = formatter
        self._notifier = notifier
        self._drift_alert_url_prefix = drift_alert_url_prefix

    @property
    def skipped_runs(self) -> list[str]:
        """Return diagnostics for persisted run files skipped by the repository."""
        if self._repository is None:
            return []
        return list(self._repository.skipped_runs)

    async def run(
        self,
        test_cases: list[GoldenTestCase],
        system_prompt: str,
        prompt_version: str,
        model: str,
        dataset: str,
        thresholds: RegressionThresholds | None = None,
        baseline_run_id: str | None = None,
        eval_config: dict[str, Any] | None = None,
        bootstrap: BootstrapConfig | None = None,
    ) -> EvaluationRunOutcome:
        """Execute the evaluation pipeline and return its outcome."""
        results = await self._evaluation_service.evaluate_dataset(
            test_cases=test_cases,
            system_prompt=system_prompt,
        )

        report = self._reporting_service.build_report(
            results=results,
            prompt_version=prompt_version,
            model=model,
            dataset=dataset,
            eval_config_hash=self._compute_eval_config_hash(
                test_cases=test_cases,
                system_prompt=system_prompt,
                prompt_version=prompt_version,
                model=model,
                dataset=dataset,
                eval_config=eval_config,
            ),
            evaluator=self._extract_evaluator(eval_config),
            thresholds=thresholds,
            bootstrap=bootstrap,
        )

        baseline_requested = baseline_run_id is not None
        baseline = self._load_baseline(baseline_run_id)
        skipped_runs = self.skipped_runs
        comparison: EvaluationComparison | None = None
        incompatibility_reasons: list[str] = []
        baseline_results_dict: dict[str, EvaluationResult] | None = None

        if baseline_requested and baseline is None:
            if baseline_run_id == LATEST_BASELINE and skipped_runs:
                incompatibility_reasons.append(
                    "Cannot select the latest baseline because persisted run "
                    "files could not be loaded: " + "; ".join(skipped_runs)
                )
            else:
                incompatibility_reasons.append(
                    "Baseline was requested but no baseline run was found."
                )

        if baseline is not None:
            incompatibility_reasons.extend(
                self._validate_compatibility(baseline, report)
            )
            if not incompatibility_reasons:
                comparison = self._comparison_service.compare(baseline, report)
                report.baseline_run_id = baseline.run_id
                report.comparison = comparison
                report.drift = self._drift_service.compare(baseline, report)
                report.paired_analysis = paired_baseline_analysis(
                    baseline_results=baseline.results,
                    candidate_results=report.results,
                    bootstrap=bootstrap,
                )
                baseline_results_dict = {
                    result.test_case_id: result for result in baseline.results
                }

        report.regression = self._regression_service.evaluate(
            report=report,
            thresholds=thresholds or RegressionThresholds(),
            comparison=comparison,
            baseline_results=baseline_results_dict,
        )
        if incompatibility_reasons:
            report.regression.incompatibility_reasons = [
                *report.regression.incompatibility_reasons,
                *incompatibility_reasons,
            ]
            if report.regression.status != "FAIL":
                report.regression.status = "INCONCLUSIVE"

        if self._repository is not None:
            self._repository.save(report)

        report_text = self._formatter.format(report) if self._formatter else None

        notification_error: str | None = None
        slack_status: Literal["delivered", "failed"] = "delivered"
        if self._notifier is not None:
            drift_message = drift_alert_message(
                report, self._drift_alert_url_prefix,
            )
            if drift_message is None:
                message = self._notification_message(report)
            else:
                # A confirmed drift alert replaces the generic completed
                # notification so each run delivers a single message.
                message = drift_message
            try:
                await self._notifier.notify(message)
                slack_status, drift_error = "delivered", None
            except Exception as exc:  # noqa: BLE001
                notification_error = (
                    "Notification delivery failed "
                    f"({type(exc).__name__})."
                )
                slack_status, drift_error = "failed", type(exc).__name__
            if (
                drift_message is not None
                and report.baseline_run_id is not None
            ):
                report.slack_alert = SlackAlertRecord(
                    baseline_run_id=report.baseline_run_id,
                    status=slack_status,
                    error=drift_error,
                    recorded_at=datetime.now(UTC),
                )
                if self._repository is not None:
                    # Re-save the run file with the delivery outcome only;
                    # the evaluation content itself is unchanged.
                    self._repository.save(report)

        return EvaluationRunOutcome(
            report=report,
            comparison=comparison,
            report_text=report_text,
            notification_error=notification_error,
            skipped_runs=skipped_runs,
        )

    @staticmethod
    def _extract_evaluator(
        eval_config: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Return the evaluator identity embedded in the eval configuration."""
        if not eval_config:
            return None
        evaluator = eval_config.get("evaluator")
        if not isinstance(evaluator, dict) or not evaluator:
            return None
        return dict(evaluator)

    @staticmethod
    def _compute_eval_config_hash(
        test_cases: list[GoldenTestCase],
        system_prompt: str,
        prompt_version: str,
        model: str,
        dataset: str,
        eval_config: dict[str, Any] | None,
    ) -> str:
        """Return a stable fingerprint of every input that shapes a run.

        Gate thresholds are deliberately excluded: changing a release policy
        must not invalidate an otherwise comparable baseline.
        """
        config: dict[str, Any] = {
            "prompt_version": prompt_version,
            "model": model,
            "dataset": dataset,
            "system_prompt": system_prompt,
            "test_cases": [
                test_case.model_dump(mode="json")
                for test_case in sorted(test_cases, key=lambda case: case.id)
            ],
        }
        if eval_config:
            config["eval_config"] = eval_config
        payload = json.dumps(
            config, sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _load_baseline(
        self,
        baseline_run_id: str | None,
    ) -> EvaluationReport | None:
        """Resolve the baseline report, supporting the 'latest' alias."""
        if baseline_run_id is None:
            return None
        if self._repository is None:
            raise ValueError(
                "A baseline was requested but no evaluation run "
                "repository is configured."
            )
        if baseline_run_id == LATEST_BASELINE:
            return self._repository.get_latest()
        try:
            return self._repository.get(baseline_run_id)
        except FileNotFoundError:
            return None

    def _validate_compatibility(
        self, baseline: EvaluationReport, current: EvaluationReport
    ) -> list[str]:
        """Validate that baseline and current runs are comparable."""
        reasons: list[str] = []
        if baseline.dataset != current.dataset:
            reasons.append(
                f"Dataset mismatch: baseline='{baseline.dataset}' vs current='{current.dataset}'"
            )
        if baseline.model != current.model:
            reasons.append(
                f"Model mismatch: baseline='{baseline.model}' vs current='{current.model}'"
            )
        if baseline.prompt_version != current.prompt_version:
            reasons.append(
                f"Prompt version mismatch: baseline='{baseline.prompt_version}' vs current='{current.prompt_version}'"
            )
        if baseline.eval_config_hash is None:
            reasons.append(
                "Baseline has no eval_config_hash; prompt and case "
                "compatibility cannot be verified."
            )
        elif current.eval_config_hash is None:
            reasons.append(
                "Current run has no eval_config_hash; prompt and case "
                "compatibility cannot be verified."
            )
        elif baseline.eval_config_hash != current.eval_config_hash:
            reasons.append(
                "Evaluation config mismatch: the prompt, model, dataset, "
                "sampling configuration, or test case set differs from the "
                "baseline."
            )
        if baseline.case_count != len(baseline.results):
            reasons.append(
                "Baseline case_count does not match its number of results."
            )
        if current.case_count != len(current.results):
            reasons.append(
                "Current run case_count does not match its number of results."
            )

        baseline_ids = [result.test_case_id for result in baseline.results]
        current_ids = [result.test_case_id for result in current.results]
        if len(baseline_ids) != len(set(baseline_ids)):
            reasons.append("Baseline contains duplicate test case ids.")
        if len(current_ids) != len(set(current_ids)):
            reasons.append("Current run contains duplicate test case ids.")
        if not baseline_ids:
            reasons.append("Baseline has no evaluated cases.")
        elif not current_ids:
            reasons.append("Current run has no evaluated cases.")
        elif set(baseline_ids) != set(current_ids):
            missing = sorted(set(baseline_ids) - set(current_ids))
            added = sorted(set(current_ids) - set(baseline_ids))
            reasons.append(
                "Case set mismatch: "
                f"missing from current={missing[:5]}, "
                f"new in current={added[:5]}."
            )

        baseline_critical = {
            result.test_case_id for result in baseline.results if result.critical
        }
        current_critical = {
            result.test_case_id for result in current.results if result.critical
        }
        if baseline_critical != current_critical:
            missing = sorted(baseline_critical - current_critical)
            added = sorted(current_critical - baseline_critical)
            reasons.append(
                "Critical case set mismatch: "
                f"missing from current={missing[:5]}, "
                f"new in current={added[:5]}."
            )
        return reasons

    @staticmethod
    def _notification_message(report: EvaluationReport) -> str:
        """Compose the concise post-run notification message."""
        lines = [
            f"PromptGuard run {report.run_id} completed.",
            (
                f"Prompt version: {report.prompt_version} | "
                f"Model: {report.model} | Dataset: {report.dataset}"
            ),
            f"Cases evaluated: {report.case_count}",
            f"Category accuracy: {report.category_accuracy:.2%}",
            f"Average summary score: {report.average_summary_score:.2f}/5",
        ]
        if report.regression is None:
            lines.append("Regression status: not evaluated")
        else:
            status = report.regression.status
            if status == "PASS":
                lines.append("Regression status: PASS")
            elif status == "FAIL":
                lines.append(
                    f"Regression status: FAIL "
                    f"({len(report.regression.violations)} violations)"
                )
            else:  # INCONCLUSIVE
                lines.append("Regression status: INCONCLUSIVE")
                if report.regression.incompatibility_reasons:
                    for reason in report.regression.incompatibility_reasons:
                        lines.append(f"  Incompatibility: {reason}")
        return "\n".join(lines)