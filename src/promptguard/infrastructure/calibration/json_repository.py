"""Load human-labelled calibration datasets from JSON."""

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from promptguard.domain.calibration import CalibrationDataset
from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_rubric_sha256,
)


def load_calibration_dataset(path: Path | str) -> CalibrationDataset:
    """Load a calibration dataset and validate its rubric identity."""
    calibration_path = Path(path)
    try:
        payload: Any = json.loads(
            calibration_path.read_text(encoding="utf-8")
        )
    except OSError as exc:
        raise ValueError(
            f"Could not read calibration file '{calibration_path}': {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Calibration file '{calibration_path}' is not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict):
        raise TypeError(
            "Calibration JSON must be an object with 'metadata' and "
            "'examples' fields."
        )

    try:
        dataset = CalibrationDataset.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(
            f"Calibration file '{calibration_path}' is invalid: {exc}"
        ) from exc

    if dataset.metadata.rubric_version != SUMMARY_QUALITY_RUBRIC_VERSION:
        raise ValueError(
            "Calibration dataset rubric version "
            f"'{dataset.metadata.rubric_version}' does not match the "
            f"current rubric version '{SUMMARY_QUALITY_RUBRIC_VERSION}'."
        )
    current_rubric_hash = summary_quality_rubric_sha256()
    if dataset.metadata.rubric_sha256 != current_rubric_hash:
        raise ValueError(
            "Calibration dataset rubric hash does not match the current "
            "rubric content."
        )
    return dataset
