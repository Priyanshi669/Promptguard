"""Configurable threshold definitions for evaluation runs."""

from typing import Literal

from pydantic import BaseModel, Field


class RegressionThresholds(BaseModel):
    """Optional limits used to evaluate a run against quality goals."""

    minimum_category_accuracy: float | None = Field(default=None, ge=0, le=1)
    minimum_summary_score: float | None = Field(default=None, ge=1, le=5)
    maximum_average_latency_ms: float | None = Field(default=None, ge=0)
    maximum_category_accuracy_drop: float | None = Field(default=None, ge=0, le=1)
    maximum_summary_score_drop: float | None = Field(default=None, ge=0, le=4)
    maximum_category_accuracy_drop_pct: float | None = Field(
        default=None, ge=0, le=100,
    )
    maximum_summary_score_drop_pct: float | None = Field(
        default=None, ge=0, le=100,
    )


class ThresholdViolation(BaseModel):
    """A structured record of one violated threshold."""

    metric: str
    actual_value: float
    threshold: float
    direction: Literal["below", "above"]
    message: str
