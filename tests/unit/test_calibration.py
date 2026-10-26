"""Unit tests for judge calibration and calibration-data loading."""

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from pydantic import ValidationError

from promptguard.application.calibration_service import JudgeCalibrationService
from promptguard.domain.calibration import (
    CalibrationDataset,
    CalibrationDatasetMetadata,
    CalibrationThresholds,
    JudgeCalibrationExample,
    JudgeCalibrationReport,
)
from promptguard.domain.metrics import SummaryMetric
from promptguard.infrastructure.calibration.json_repository import (
    load_calibration_dataset,
)
from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_rubric_sha256,
)


class SequenceMetric(SummaryMetric):
    """Return a predetermined sequence of judge scores."""

    def __init__(self, scores: list[float]) -> None:
        """Store scores to return in order."""
        self._scores = iter(scores)

    async def score(
        self,
        expected_summary: str,
        actual_summary: str,
    ) -> float:
        """Return the next score."""
        return next(self._scores)


def example(case_id: str, human_score: float) -> JudgeCalibrationExample:
    """Build one calibration example."""
    return JudgeCalibrationExample(
        id=case_id,
        expected_summary="Expected summary",
        actual_summary="Actual summary",
        human_score=human_score,
    )


def metadata(
    version: str = "v1",
    rubric_version: str = SUMMARY_QUALITY_RUBRIC_VERSION,
    rubric_sha256: str | None = None,
) -> CalibrationDatasetMetadata:
    """Build calibration metadata matching the current rubric."""
    return CalibrationDatasetMetadata(
        version=version,
        rubric_version=rubric_version,
        rubric_sha256=rubric_sha256 or summary_quality_rubric_sha256(),
    )


def dataset(
    examples: list[JudgeCalibrationExample],
    *,
    version: str = "v1",
) -> CalibrationDataset:
    """Build a calibration dataset with valid metadata."""
    return CalibrationDataset(
        metadata=metadata(version=version),
        examples=examples,
    )


def evaluator(
    *,
    rubric_version: str = SUMMARY_QUALITY_RUBRIC_VERSION,
    rubric_sha256: str | None = None,
) -> dict[str, Any]:
    """Build the non-secret evaluator identity used in tests."""
    return {
        "judge_model": "judge",
        "judge_rubric_version": rubric_version,
        "judge_rubric_sha256": (
            rubric_sha256 or summary_quality_rubric_sha256()
        ),
    }


def strict_thresholds(sample_count: int) -> CalibrationThresholds:
    """Return thresholds that require perfect agreement for a small set."""
    return CalibrationThresholds(
        minimum_samples=sample_count,
        maximum_mae=0.0,
        minimum_pearson=1.0,
        minimum_within_one_agreement=1.0,
        maximum_absolute_bias=0.0,
    )


@pytest.mark.asyncio
async def test_perfect_agreement_passes_calibration() -> None:
    """Perfect judge agreement should produce perfect metrics."""
    examples = [example("a", 3.0), example("b", 4.0), example("c", 5.0)]

    report = await JudgeCalibrationService().calibrate(
        dataset=dataset(examples),
        summary_metric=SequenceMetric([3.0, 4.0, 5.0]),
        evaluator=evaluator(),
        thresholds=strict_thresholds(3),
    )

    assert report.passed
    assert report.violations == []
    assert report.metrics.mean_absolute_error == 0.0
    assert report.metrics.root_mean_squared_error == 0.0
    assert report.metrics.pearson_correlation == 1.0
    assert report.metrics.spearman_correlation == 1.0
    assert report.metrics.exact_agreement == 1.0
    assert report.metrics.within_one_agreement == 1.0
    assert report.metrics.mean_bias == 0.0


@pytest.mark.asyncio
async def test_calibration_reports_bias_and_agreement_failures() -> None:
    """Systematically low scores should fail every configured quality limit."""
    examples = [example(f"case-{index}", 5.0) for index in range(4)]

    report = await JudgeCalibrationService().calibrate(
        dataset=dataset(examples),
        summary_metric=SequenceMetric([3.0, 3.0, 3.0, 3.0]),
        evaluator=evaluator(),
        thresholds=strict_thresholds(4),
    )

    assert not report.passed
    assert report.metrics.mean_absolute_error == 2.0
    assert report.metrics.mean_bias == -2.0
    assert report.metrics.within_one_agreement == 0.0
    assert report.metrics.pearson_correlation is None
    assert len(report.violations) == 4


@pytest.mark.asyncio
async def test_constant_human_scores_do_not_produce_correlation() -> None:
    """A constant human label has no variance, so correlation is undefined."""
    examples = [example("a", 4.0), example("b", 4.0), example("c", 4.0)]

    report = await JudgeCalibrationService().calibrate(
        dataset=dataset(examples),
        summary_metric=SequenceMetric([3.0, 4.0, 5.0]),
        evaluator=evaluator(),
        thresholds=CalibrationThresholds(
            minimum_samples=3,
            maximum_mae=4.0,
            minimum_pearson=0.6,
            minimum_within_one_agreement=0.0,
            maximum_absolute_bias=4.0,
        ),
    )

    assert report.metrics.pearson_correlation is None
    assert report.metrics.spearman_correlation is None
    assert not report.passed
    assert any(
        "Pearson correlation is undefined" in violation
        for violation in report.violations
    )


@pytest.mark.asyncio
async def test_constant_judge_scores_do_not_produce_correlation() -> None:
    """A constant judge score has no variance, so correlation is undefined."""
    examples = [example("a", 3.0), example("b", 4.0), example("c", 5.0)]

    report = await JudgeCalibrationService().calibrate(
        dataset=dataset(examples),
        summary_metric=SequenceMetric([4.0, 4.0, 4.0]),
        evaluator=evaluator(),
        thresholds=CalibrationThresholds(
            minimum_samples=3,
            maximum_mae=4.0,
            minimum_pearson=0.6,
            minimum_within_one_agreement=0.0,
            maximum_absolute_bias=4.0,
        ),
    )

    assert report.metrics.pearson_correlation is None
    assert report.metrics.spearman_correlation is None
    assert not report.passed


@pytest.mark.asyncio
async def test_high_correlation_does_not_hide_systematic_bias() -> None:
    """A well-ranked but biased judge must still fail calibration limits."""
    examples = [
        example("a", 1.0),
        example("b", 2.0),
        example("c", 3.0),
        example("d", 4.0),
        example("e", 5.0),
    ]

    report = await JudgeCalibrationService().calibrate(
        dataset=dataset(examples),
        summary_metric=SequenceMetric([2.0, 3.0, 4.0, 5.0, 5.0]),
        evaluator=evaluator(),
        thresholds=CalibrationThresholds(
            minimum_samples=5,
            maximum_mae=0.75,
            minimum_pearson=0.6,
            minimum_within_one_agreement=0.8,
            maximum_absolute_bias=0.5,
        ),
    )

    assert report.metrics.pearson_correlation is not None
    assert report.metrics.pearson_correlation > 0.9
    assert report.metrics.mean_absolute_error > 0.75
    assert report.metrics.mean_bias > 0.5
    assert not report.passed
    assert any("Mean absolute error" in item for item in report.violations)
    assert any("mean bias" in item for item in report.violations)


@pytest.mark.asyncio
async def test_calibration_fails_when_sample_count_is_too_small() -> None:
    """A calibration set smaller than the configured minimum must fail."""
    report = await JudgeCalibrationService().calibrate(
        dataset=dataset([example("a", 4.0), example("b", 5.0)]),
        summary_metric=SequenceMetric([4.0, 5.0]),
        evaluator=evaluator(),
        thresholds=CalibrationThresholds(minimum_samples=3),
    )

    assert not report.passed
    assert any("at least 3" in violation for violation in report.violations)


@pytest.mark.asyncio
async def test_calibration_rejects_invalid_judge_scores() -> None:
    """A judge score outside the 1-5 scale must not enter calibration."""
    with pytest.raises(ValueError, match="invalid score"):
        await JudgeCalibrationService().calibrate(
            dataset=dataset([example("a", 4.0)]),
            summary_metric=SequenceMetric([6.0]),
            evaluator=evaluator(),
        )


@pytest.mark.asyncio
async def test_calibration_requires_evaluator_and_matching_rubric() -> None:
    """Missing or mismatched evaluator rubric identity is a configuration error."""
    service = JudgeCalibrationService()
    calibration_dataset = dataset([example("a", 4.0)])

    with pytest.raises(ValueError, match="evaluator identity"):
        await service.calibrate(
            dataset=calibration_dataset,
            summary_metric=SequenceMetric([4.0]),
            evaluator={},
        )
    with pytest.raises(ValueError, match="rubric version"):
        await service.calibrate(
            dataset=calibration_dataset,
            summary_metric=SequenceMetric([4.0]),
            evaluator=evaluator(rubric_version="v0"),
        )
    with pytest.raises(ValueError, match="rubric hash"):
        await service.calibrate(
            dataset=calibration_dataset,
            summary_metric=SequenceMetric([4.0]),
            evaluator=evaluator(rubric_sha256="0" * 64),
        )


@pytest.mark.asyncio
async def test_report_preserves_dataset_and_evaluator_identity() -> None:
    """Calibration evidence must retain every reproducibility input."""
    examples = [example("a", 3.0), example("b", 4.0), example("c", 5.0)]
    calibration_dataset = dataset(examples, version="v7")
    identity = evaluator()

    report = await JudgeCalibrationService().calibrate(
        dataset=calibration_dataset,
        summary_metric=SequenceMetric([3.0, 4.0, 5.0]),
        evaluator=identity,
        thresholds=strict_thresholds(3),
    )

    assert report.sample_count == 3
    assert report.dataset.version == "v7"
    assert len(report.dataset.sha256) == 64
    assert report.dataset.rubric_version == SUMMARY_QUALITY_RUBRIC_VERSION
    assert report.dataset.rubric_sha256 == summary_quality_rubric_sha256()
    assert report.dataset.sample_count == 3
    assert report.evaluator == identity


@pytest.mark.asyncio
async def test_report_rejects_mismatched_rubric_identity() -> None:
    """A report cannot pair a dataset with a different evaluator rubric."""
    report = await JudgeCalibrationService().calibrate(
        dataset=dataset([example("a", 4.0)]),
        summary_metric=SequenceMetric([4.0]),
        evaluator=evaluator(),
        thresholds=strict_thresholds(1),
    )
    payload = report.model_dump()
    payload["evaluator"]["judge_rubric_version"] = "v0"

    with pytest.raises(ValidationError, match="rubric version"):
        JudgeCalibrationReport.model_validate(payload)


def test_dataset_identity_is_stable_and_order_independent() -> None:
    """Canonical ordering should keep the same content hash stable."""
    first = dataset([example("a", 3.0), example("b", 4.0)])
    reordered = dataset([example("b", 4.0), example("a", 3.0)])
    changed = dataset([example("a", 3.0), example("b", 5.0)])

    assert first.identity().sha256 == reordered.identity().sha256
    assert first.identity().sha256 != changed.identity().sha256


def test_dataset_rejects_empty_and_duplicate_examples() -> None:
    """A dataset cannot be empty or contain ambiguous duplicate ids."""
    with pytest.raises(ValidationError, match="at least one"):
        dataset([])
    with pytest.raises(ValidationError, match="unique"):
        dataset([example("a", 4.0), example("a", 5.0)])


class TestCalibrationLoader:
    """Validate supported calibration file shapes and errors."""

    examples: ClassVar[list[dict[str, object]]] = [
        {
            "id": "case-1",
            "expected_summary": "Expected",
            "actual_summary": "Actual",
            "human_score": 4,
        },
        {
            "id": "case-2",
            "expected_summary": "Expected",
            "actual_summary": "Actual",
            "human_score": 5,
        },
    ]

    def payload(
        self,
        *,
        rubric_version: str = SUMMARY_QUALITY_RUBRIC_VERSION,
        rubric_sha256: str | None = None,
        examples: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """Build a complete calibration file payload."""
        return {
            "metadata": {
                "version": "v1",
                "rubric_version": rubric_version,
                "rubric_sha256": (
                    rubric_sha256 or summary_quality_rubric_sha256()
                ),
            },
            "examples": (
                self.examples if examples is None else examples
            ),
        }

    def test_loads_metadata_and_examples(self, tmp_path: Path) -> None:
        """The loader should return both examples and reproducibility data."""
        path = tmp_path / "calibration.json"
        path.write_text(json.dumps(self.payload()), encoding="utf-8")

        loaded = load_calibration_dataset(path)

        assert [item.id for item in loaded.examples] == ["case-1", "case-2"]
        assert loaded.metadata.version == "v1"

    def test_rejects_list_payload_without_metadata(self, tmp_path: Path) -> None:
        """A bare list cannot prove its rubric version."""
        path = tmp_path / "calibration.json"
        path.write_text(json.dumps(self.examples), encoding="utf-8")

        with pytest.raises(TypeError, match="metadata"):
            load_calibration_dataset(path)

    def test_rejects_rubric_version_mismatch(self, tmp_path: Path) -> None:
        """A dataset produced for another rubric version is invalid."""
        path = tmp_path / "calibration.json"
        path.write_text(
            json.dumps(self.payload(rubric_version="v0")),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="rubric version"):
            load_calibration_dataset(path)

    def test_rejects_rubric_hash_mismatch(self, tmp_path: Path) -> None:
        """A rubric content change must invalidate old calibration labels."""
        path = tmp_path / "calibration.json"
        path.write_text(
            json.dumps(self.payload(rubric_sha256="0" * 64)),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="rubric hash"):
            load_calibration_dataset(path)

    def test_rejects_invalid_or_missing_human_scores(
        self, tmp_path: Path,
    ) -> None:
        """Human labels must be present and within the 1-5 scale."""
        path = tmp_path / "calibration.json"
        invalid = dict(self.examples[0], human_score=6)
        path.write_text(
            json.dumps(self.payload(examples=[invalid])),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="invalid"):
            load_calibration_dataset(path)

        missing = {
            key: value
            for key, value in self.examples[0].items()
            if key != "human_score"
        }
        path.write_text(
            json.dumps(self.payload(examples=[missing])),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="invalid"):
            load_calibration_dataset(path)

    def test_rejects_duplicate_ids(self, tmp_path: Path) -> None:
        """Duplicate calibration ids would make evidence ambiguous."""
        path = tmp_path / "calibration.json"
        duplicate = [self.examples[0], self.examples[0]]
        path.write_text(
            json.dumps(self.payload(examples=duplicate)),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="unique"):
            load_calibration_dataset(path)

    def test_rejects_empty_payload(self, tmp_path: Path) -> None:
        """An empty calibration file cannot calibrate anything."""
        path = tmp_path / "calibration.json"
        path.write_text(
            json.dumps(self.payload(examples=[])),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="at least one"):
            load_calibration_dataset(path)

    def test_rejects_invalid_json(self, tmp_path: Path) -> None:
        """Malformed JSON should be a clear configuration error."""
        path = tmp_path / "calibration.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(ValueError, match="not valid JSON"):
            load_calibration_dataset(path)
