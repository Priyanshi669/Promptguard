"""Aggregate individual evaluation results into a complete report."""

from datetime import UTC, datetime
from uuid import uuid4

from promptguard.domain.classification import classification_metrics
from promptguard.domain.models import (
    EvaluationReport,
    EvaluationResult,
)
from promptguard.domain.statistics import (
    BootstrapConfig,
    confidence_intervals,
)
from promptguard.domain.thresholds import RegressionThresholds


class ReportingService:
    """Build aggregate, infrastructure-independent evaluation reports."""

    def build_report(
        self,
        results: list[EvaluationResult],
        prompt_version: str,
        model: str,
        dataset: str,
        eval_config_hash: str | None = None,
        thresholds: RegressionThresholds | None = None,
        evaluator: dict[str, object] | None = None,
        bootstrap: BootstrapConfig | None = None,
    ) -> EvaluationReport:
        """Aggregate a complete run, including an explicitly empty run."""
        case_count = len(results)

        return EvaluationReport(
            run_id=str(uuid4()),
            timestamp=datetime.now(UTC),
            prompt_version=prompt_version,
            model=model,
            dataset=dataset,
            eval_config_hash=eval_config_hash,
            evaluator=evaluator,
            thresholds=thresholds,
            case_count=case_count,
            category_accuracy=(
                sum(result.category_match for result in results) / case_count
                if case_count
                else 0.0
            ),
            average_summary_score=(
                sum(result.summary_score for result in results) / case_count
                if case_count
                else 0.0
            ),
            average_latency_ms=(
                sum(result.latency_ms for result in results) / case_count
                if case_count
                else 0.0
            ),
            total_input_tokens=sum(result.input_tokens for result in results),
            total_output_tokens=sum(result.output_tokens for result in results),
            total_cost_usd=self._aggregate_cost_usd(results),
            classification=classification_metrics(results),
            confidence_intervals=(
                confidence_intervals(results, bootstrap) if bootstrap else None
            ),
            results=results,
        )

    @staticmethod
    def _aggregate_cost_usd(
        results: list[EvaluationResult],
    ) -> float | None:
        """Sum provider-reported costs, or None when any case lacks one."""
        if not results or any(
            result.cost_usd is None for result in results
        ):
            return None
        return sum(result.cost_usd or 0.0 for result in results)
