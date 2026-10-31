"""Seeded percentile-bootstrap uncertainty estimates.

These intervals quantify sampling uncertainty under the chosen resampling
scheme only. They neither prove that the golden dataset represents
production traffic nor that the judge agrees with human reviewers.
"""

from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator, model_validator

if TYPE_CHECKING:
    from promptguard.domain.models import EvaluationResult

DEFAULT_BOOTSTRAP_SEED = 20261026
DEFAULT_RESAMPLES = 2000
DEFAULT_CONFIDENCE_LEVEL = 0.95
SMALL_SAMPLE_THRESHOLD = 30


class BootstrapConfig(BaseModel):
    """Reproducible bootstrap parameters for one run."""

    seed: int = DEFAULT_BOOTSTRAP_SEED
    resamples: int = Field(default=DEFAULT_RESAMPLES, ge=1, le=100000)
    confidence_level: float = Field(
        default=DEFAULT_CONFIDENCE_LEVEL, gt=0, lt=1,
    )


class BootstrapEstimate(BaseModel):
    """A percentile-bootstrap interval for one case-level statistic."""

    point_estimate: float = Field(allow_inf_nan=False)
    lower_bound: float = Field(allow_inf_nan=False)
    upper_bound: float = Field(allow_inf_nan=False)
    confidence_level: float = Field(gt=0, lt=1)
    resamples: int = Field(ge=1)
    seed: int
    sample_count: int = Field(ge=1)
    method: str = "seeded-percentile-bootstrap"

    @field_validator("point_estimate", "lower_bound", "upper_bound")
    @classmethod
    def _require_finite(cls, value: float) -> float:
        """Reject non-finite interval evidence."""
        if not math.isfinite(value):
            raise ValueError("bootstrap bounds must be finite")
        return value

    @model_validator(mode="after")
    def _require_ordered_bounds(self) -> BootstrapEstimate:
        """Bounds must be ordered and must bracket the point estimate."""
        if self.lower_bound > self.upper_bound:
            raise ValueError(
                "bootstrap lower_bound must not exceed upper_bound"
            )
        return self


class ConfidenceIntervals(BaseModel):
    """Bootstrap uncertainty for the aggregate quality metrics."""

    accuracy_interval: BootstrapEstimate | None = None
    summary_score_interval: BootstrapEstimate | None = None
    method: str = "seeded-percentile-bootstrap"
    seed: int
    resamples: int = Field(ge=1)
    confidence_level: float = Field(gt=0, lt=1)
    sample_count: int = Field(ge=1)
    small_sample: bool = Field(
        description=(
            "True when the sample is too small for stable interval "
            "estimates; the interval is reported but unreliable."
        ),
    )


def bootstrap_interval(
    values: list[float],
    config: BootstrapConfig,
) -> BootstrapEstimate | None:
    """Return a seeded percentile-bootstrap interval for the sample mean.

    Returns ``None`` for an empty sample: an unavailable interval must not
    be represented as zero. Identical inputs, seeds, and parameters
    produce identical intervals.
    """
    if not values:
        return None
    if any(not math.isfinite(value) for value in values):
        raise ValueError("bootstrap input values must be finite")

    n = len(values)
    point_estimate = sum(values) / n
    rng = random.Random(config.seed)
    bootstrap_means: list[float] = []
    for _ in range(config.resamples):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        bootstrap_means.append(total / n)
    bootstrap_means.sort()

    alpha = 1 - config.confidence_level
    lower = _percentile(bootstrap_means, alpha / 2)
    upper = _percentile(bootstrap_means, 1 - alpha / 2)

    return BootstrapEstimate(
        point_estimate=point_estimate,
        lower_bound=lower,
        upper_bound=upper,
        confidence_level=config.confidence_level,
        resamples=config.resamples,
        seed=config.seed,
        sample_count=n,
    )


def confidence_intervals(
    results: list[EvaluationResult],
    config: BootstrapConfig,
) -> ConfidenceIntervals | None:
    """Bootstrap the accuracy and summary-score intervals for one run.

    Each case contributes one accuracy indicator (1.0 for a category
    match, else 0.0) and one summary score. Resampling is per case, so a
    case's expected and predicted labels always stay paired. Returns
    ``None`` when there are no cases.
    """
    if not results:
        return None
    accuracy_values = [
        1.0 if result.category_match else 0.0 for result in results
    ]
    summary_values = [result.summary_score for result in results]
    return ConfidenceIntervals(
        accuracy_interval=bootstrap_interval(accuracy_values, config),
        summary_score_interval=bootstrap_interval(summary_values, config),
        seed=config.seed,
        resamples=config.resamples,
        confidence_level=config.confidence_level,
        sample_count=len(results),
        small_sample=len(results) < SMALL_SAMPLE_THRESHOLD,
    )


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Return a linearly interpolated percentile of sorted values."""
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return sorted_values[lower_index]
    weight = position - lower_index
    return (
        sorted_values[lower_index] * (1 - weight)
        + sorted_values[upper_index] * weight
    )
