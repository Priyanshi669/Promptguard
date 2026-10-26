"""Facts and decisions produced by evaluation comparison."""

import math
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from promptguard.domain.enums import QualityGateStatus
from promptguard.domain.thresholds import ThresholdViolation
from promptguard.domain.types import NonEmptyStr


class EvaluationComparison(BaseModel):
    """Metric deltas between a baseline and a current evaluation run."""

    baseline_run_id: NonEmptyStr
    current_run_id: NonEmptyStr
    baseline_category_accuracy: float = Field(
        ge=0, le=1, allow_inf_nan=False,
    )
    current_category_accuracy: float = Field(
        ge=0, le=1, allow_inf_nan=False,
    )
    category_accuracy_delta: float = Field(allow_inf_nan=False)
    baseline_summary_score: float = Field(
        ge=0, le=5, allow_inf_nan=False,
    )
    current_summary_score: float = Field(
        ge=0, le=5, allow_inf_nan=False,
    )
    summary_score_delta: float = Field(allow_inf_nan=False)
    baseline_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    current_latency_ms: float = Field(ge=0, allow_inf_nan=False)
    latency_delta_ms: float = Field(allow_inf_nan=False)
    baseline_input_tokens: int = Field(ge=0)
    current_input_tokens: int = Field(ge=0)
    input_token_delta: int
    baseline_output_tokens: int = Field(ge=0)
    current_output_tokens: int = Field(ge=0)
    output_token_delta: int

    @model_validator(mode="after")
    def _validate_deltas(self) -> "EvaluationComparison":
        """Reject comparisons whose deltas do not match their metrics."""
        checks = (
            (
                "category_accuracy_delta",
                self.category_accuracy_delta,
                self.current_category_accuracy
                - self.baseline_category_accuracy,
            ),
            (
                "summary_score_delta",
                self.summary_score_delta,
                self.current_summary_score - self.baseline_summary_score,
            ),
            (
                "latency_delta_ms",
                self.latency_delta_ms,
                self.current_latency_ms - self.baseline_latency_ms,
            ),
            (
                "input_token_delta",
                self.input_token_delta,
                self.current_input_tokens - self.baseline_input_tokens,
            ),
            (
                "output_token_delta",
                self.output_token_delta,
                self.current_output_tokens - self.baseline_output_tokens,
            ),
        )
        for name, actual, expected in checks:
            if not math.isclose(
                actual, expected, rel_tol=0.0, abs_tol=1e-12,
            ):
                raise ValueError(
                    f"{name} must equal current minus baseline"
                )
        return self

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
    violations: list[ThresholdViolation] = Field(default_factory=list)
    case_regressions: list[CaseRegressionStatus] = Field(
        default_factory=list,
    )
    critical_case_failure: bool = False
    incompatibility_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_status(self) -> "RegressionResult":
        """Reject decisions inconsistent with their recorded evidence."""
        if self.status == "PASS" and (
            self.violations
            or self.critical_case_failure
            or self.incompatibility_reasons
        ):
            raise ValueError(
                "PASS must not include violations, critical failures, "
                "or incompatibility reasons"
            )
        if self.status == "FAIL" and not (
            self.violations or self.critical_case_failure
        ):
            raise ValueError(
                "FAIL must include a violation or critical failure"
            )
        if self.status == "INCONCLUSIVE" and (
            self.violations or self.critical_case_failure
        ):
            raise ValueError(
                "INCONCLUSIVE must not include violations or critical failures"
            )
        return self

    @property
    def passed(self) -> bool:
        """Backward compatibility: True only if status is PASS."""
        return self.status == "PASS"