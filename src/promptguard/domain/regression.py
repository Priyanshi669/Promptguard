"""Facts and decisions produced by evaluation comparison."""

from typing import Literal

from pydantic import BaseModel

from promptguard.domain.enums import QualityGateStatus
from promptguard.domain.thresholds import ThresholdViolation
from promptguard.domain.types import NonEmptyStr


class EvaluationComparison(BaseModel):
    """Metric deltas between a baseline and a current evaluation run."""

    baseline_run_id: NonEmptyStr
    current_run_id: NonEmptyStr
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


class CaseRegressionStatus(BaseModel):
    """Per-case regression status for individual test cases."""

    test_case_id: NonEmptyStr
    baseline_category_match: bool | None = None
    current_category_match: bool | None = None
    status: Literal["new_failure", "resolved_failure", "unchanged_failure", "unchanged_pass", "unknown"]

    @property
    def is_regression(self) -> bool:
        """Return True if this case represents a regression (new failure or unresolved failure)."""
        return self.status in ("new_failure", "unchanged_failure")


class RegressionResult(BaseModel):
    """Threshold decision and all violations for an evaluation run."""

    status: QualityGateStatus = "INCONCLUSIVE"
    violations: list[ThresholdViolation] = []
    case_regressions: list[CaseRegressionStatus] = []
    critical_case_failure: bool = False
    incompatibility_reasons: list[str] = []

    @property
    def passed(self) -> bool:
        """Backward compatibility: True only if status is PASS."""
        return self.status == "PASS"