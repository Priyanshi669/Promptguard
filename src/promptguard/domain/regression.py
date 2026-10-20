"""Facts and decisions produced by evaluation comparison."""

from pydantic import BaseModel

from promptguard.domain.thresholds import ThresholdViolation


class EvaluationComparison(BaseModel):
    """Metric deltas between a baseline and a current evaluation run."""

    baseline_run_id: str
    current_run_id: str
    baseline_category_accuracy: float
    current_category_accuracy: float
    category_accuracy_delta: float
    baseline_summary_score: float
    current_summary_score: float
    summary_score_delta: float
    baseline_latency_ms: float
    current_latency_ms: float
    latency_delta_ms: float
    baseline_input_tokens: int
    current_input_tokens: int
    input_token_delta: int
    baseline_output_tokens: int
    current_output_tokens: int
    output_token_delta: int

    @property
    def quality_decreased(self) -> bool:
        """Return whether either quality metric declined."""
        return (
            self.category_accuracy_delta < 0
            or self.summary_score_delta < 0
        )


class RegressionResult(BaseModel):
    """Threshold decision and all violations for an evaluation run."""

    passed: bool
    violations: list[ThresholdViolation] = []
