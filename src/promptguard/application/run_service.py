"""Orchestration of complete evaluation runs."""

from pydantic import BaseModel

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
from promptguard.application.evaluation_service import EvaluationService
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import EvaluationReport, GoldenTestCase
from promptguard.domain.regression import EvaluationComparison
from promptguard.domain.thresholds import RegressionThresholds

LATEST_BASELINE = "latest"


class EvaluationRunOutcome(BaseModel):
    """Everything produced by one orchestrated evaluation run."""

    report: EvaluationReport
    comparison: EvaluationComparison | None = None
    report_text: str | None = None


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

    async def run(
        self,
        test_cases: list[GoldenTestCase],
        system_prompt: str,
        prompt_version: str,
        model: str,
        dataset: str,
        thresholds: RegressionThresholds | None = None,
        baseline_run_id: str | None = None,
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
        )

        baseline = self._load_baseline(baseline_run_id)
        comparison: EvaluationComparison | None = None
        if baseline is not None:
            comparison = self._comparison_service.compare(baseline, report)
            report.baseline_run_id = baseline.run_id
            report.drift = self._drift_service.compare(baseline, report)

        if thresholds is not None:
            report.regression = self._regression_service.evaluate(
                report=report,
                thresholds=thresholds,
                comparison=comparison,
            )

        if self._repository is not None:
            self._repository.save(report)

        report_text = self._formatter.format(report) if self._formatter else None

        if self._notifier is not None:
            await self._notifier.notify(self._notification_message(report))

        return EvaluationRunOutcome(
            report=report,
            comparison=comparison,
            report_text=report_text,
        )

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
        return self._repository.get(baseline_run_id)

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
        elif report.regression.passed:
            lines.append("Regression status: PASS")
        else:
            lines.append(
                "Regression status: FAIL "
                f"({len(report.regression.violations)} violations)"
            )
        return "\n".join(lines)