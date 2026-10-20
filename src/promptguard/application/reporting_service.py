"""Aggregate individual evaluation results into a complete report."""

from datetime import UTC, datetime
from uuid import uuid4

from promptguard.domain.models import EvaluationReport, EvaluationResult


class ReportingService:
    """Build aggregate, infrastructure-independent evaluation reports."""

    def build_report(
        self,
        results: list[EvaluationResult],
        prompt_version: str,
        model: str,
        dataset: str,
    ) -> EvaluationReport:
        """Aggregate a complete run, including an explicitly empty run."""
        case_count = len(results)

        return EvaluationReport(
            run_id=str(uuid4()),
            timestamp=datetime.now(UTC),
            prompt_version=prompt_version,
            model=model,
            dataset=dataset,
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
            results=results,
        )
