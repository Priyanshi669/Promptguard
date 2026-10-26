"""Domain models for judge calibration evidence."""

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field, model_validator

from promptguard.domain.types import NonEmptyStr

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class JudgeCalibrationExample(BaseModel):
    """One human-labelled summary pair used to calibrate the judge."""

    id: NonEmptyStr
    expected_summary: NonEmptyStr
    actual_summary: NonEmptyStr
    human_score: float = Field(
        ge=1, le=5, allow_inf_nan=False,
    )


class CalibrationDatasetMetadata(BaseModel):
    """Version and rubric identity required for calibration evidence."""

    version: NonEmptyStr
    rubric_version: NonEmptyStr
    rubric_sha256: str = Field(pattern=SHA256_PATTERN)


class CalibrationDatasetIdentity(BaseModel):
    """Content-addressed identity of one calibration dataset."""

    version: NonEmptyStr
    sha256: str = Field(pattern=SHA256_PATTERN)
    rubric_version: NonEmptyStr
    rubric_sha256: str = Field(pattern=SHA256_PATTERN)
    sample_count: int = Field(ge=1)


class CalibrationDataset(BaseModel):
    """Human-labelled examples with reproducibility metadata."""

    metadata: CalibrationDatasetMetadata
    examples: list[JudgeCalibrationExample]

    @model_validator(mode="after")
    def _validate_examples(self) -> "CalibrationDataset":
        """Require non-empty, uniquely identified examples."""
        if not self.examples:
            raise ValueError(
                "Calibration dataset must contain at least one example"
            )
        ids = [example.id for example in self.examples]
        if len(ids) != len(set(ids)):
            raise ValueError("Calibration example ids must be unique")
        return self

    def identity(self) -> CalibrationDatasetIdentity:
        """Return a stable content hash plus rubric and sample metadata."""
        payload = {
            "version": self.metadata.version,
            "rubric_version": self.metadata.rubric_version,
            "rubric_sha256": self.metadata.rubric_sha256,
            "examples": [
                example.model_dump(mode="json")
                for example in sorted(self.examples, key=lambda item: item.id)
            ],
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return CalibrationDatasetIdentity(
            version=self.metadata.version,
            sha256=digest,
            rubric_version=self.metadata.rubric_version,
            rubric_sha256=self.metadata.rubric_sha256,
            sample_count=len(self.examples),
        )


class CalibrationThresholds(BaseModel):
    """Acceptance limits for a judge calibration run."""

    minimum_samples: int = Field(default=20, ge=1)
    maximum_mae: float = Field(
        default=0.75, ge=0, le=4, allow_inf_nan=False,
    )
    minimum_pearson: float = Field(
        default=0.6, ge=-1, le=1, allow_inf_nan=False,
    )
    minimum_within_one_agreement: float = Field(
        default=0.8, ge=0, le=1, allow_inf_nan=False,
    )
    maximum_absolute_bias: float = Field(
        default=0.5, ge=0, le=4, allow_inf_nan=False,
    )


class CalibrationItem(BaseModel):
    """Human and judge scores for one calibration example."""

    test_case_id: NonEmptyStr
    human_score: float = Field(ge=1, le=5, allow_inf_nan=False)
    judge_score: float = Field(ge=1, le=5, allow_inf_nan=False)
    absolute_error: float = Field(ge=0, le=4, allow_inf_nan=False)

    @model_validator(mode="after")
    def _validate_absolute_error(self) -> "CalibrationItem":
        """Reject an absolute error that does not match its scores."""
        expected = abs(self.judge_score - self.human_score)
        if abs(self.absolute_error - expected) > 1e-9:
            raise ValueError(
                "absolute_error must equal abs(judge_score - human_score)"
            )
        return self


class CalibrationMetrics(BaseModel):
    """Agreement and bias statistics for a calibration run."""

    sample_count: int = Field(ge=0)
    mean_human_score: float = Field(ge=1, le=5, allow_inf_nan=False)
    mean_judge_score: float = Field(ge=1, le=5, allow_inf_nan=False)
    mean_absolute_error: float = Field(
        ge=0, le=4, allow_inf_nan=False,
    )
    root_mean_squared_error: float = Field(
        ge=0, le=4, allow_inf_nan=False,
    )
    pearson_correlation: float | None = Field(
        default=None, ge=-1, le=1, allow_inf_nan=False,
    )
    spearman_correlation: float | None = Field(
        default=None, ge=-1, le=1, allow_inf_nan=False,
    )
    exact_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    within_one_agreement: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_bias: float = Field(ge=-4, le=4, allow_inf_nan=False)


class JudgeCalibrationReport(BaseModel):
    """Complete, auditable result of one judge calibration run."""

    sample_count: int = Field(ge=0)
    metrics: CalibrationMetrics
    thresholds: CalibrationThresholds
    dataset: CalibrationDatasetIdentity
    evaluator: dict[str, Any] = Field(min_length=1)
    items: list[CalibrationItem]
    violations: list[str]
    passed: bool

    @model_validator(mode="after")
    def _validate_report_consistency(self) -> "JudgeCalibrationReport":
        """Reject internally inconsistent calibration evidence."""
        if self.sample_count != len(self.items):
            raise ValueError(
                "sample_count must equal the number of calibration items"
            )
        if self.metrics.sample_count != self.sample_count:
            raise ValueError(
                "metrics.sample_count must equal the report sample_count"
            )
        if self.dataset.sample_count != self.sample_count:
            raise ValueError(
                "dataset.sample_count must equal the report sample_count"
            )
        if (
            self.evaluator.get("judge_rubric_version")
            != self.dataset.rubric_version
        ):
            raise ValueError(
                "evaluator rubric version must match the dataset rubric version"
            )
        if (
            self.evaluator.get("judge_rubric_sha256")
            != self.dataset.rubric_sha256
        ):
            raise ValueError(
                "evaluator rubric hash must match the dataset rubric hash"
            )
        if self.passed != (not self.violations):
            raise ValueError(
                "passed must be true exactly when there are no violations"
            )
        return self
