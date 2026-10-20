"""Evaluation-output drift records."""

from pydantic import BaseModel, Field


class CategoryDriftResult(BaseModel):
    """Category-distribution change between two evaluation runs."""

    sufficient_data: bool
    total_variation_distance: float | None = Field(default=None, ge=0, le=1)
    baseline_distribution: dict[str, float] = {}
    current_distribution: dict[str, float] = {}
