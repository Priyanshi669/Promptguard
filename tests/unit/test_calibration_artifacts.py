"""Validate the calibration schema, sampling plan, and unlabeled manifest."""

import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from promptguard.domain.calibration import CalibrationDataset
from promptguard.infrastructure.evaluation.rubric import (
    summary_quality_rubric_sha256,
)

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_DIR = ROOT / "dataset" / "calibration" / "v1"


def load_json(path: Path) -> Any:
    """Load a JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def normalize(text: str) -> str:
    """Normalize text for overlap checks."""
    return " ".join(text.lower().split())


def test_schema_tracks_the_calibration_model() -> None:
    """The checked-in schema should expose the loader's required fields."""
    schema = load_json(CALIBRATION_DIR / "calibration_schema.json")
    model_schema = CalibrationDataset.model_json_schema()

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert set(schema["required"]) == {"metadata", "examples"}
    assert schema["properties"]["examples"]["minItems"] == 1

    example = schema["$defs"]["JudgeCalibrationExample"]
    model_example = model_schema["$defs"]["JudgeCalibrationExample"]
    assert set(example["required"]) == set(model_example["required"])
    assert example["properties"]["human_score"]["minimum"] == 1
    assert example["properties"]["human_score"]["maximum"] == 5

    metadata = schema["$defs"]["CalibrationDatasetMetadata"]
    model_metadata = model_schema["$defs"]["CalibrationDatasetMetadata"]
    assert set(metadata["required"]) == set(model_metadata["required"])
    assert (
        metadata["properties"]["rubric_sha256"]["pattern"]
        == model_metadata["properties"]["rubric_sha256"]["pattern"]
    )


def test_sampling_plan_targets_the_reviewed_scope() -> None:
    """The plan should select the agreed unlabeled calibration scope."""
    plan = load_json(CALIBRATION_DIR / "sampling_plan.json")

    assert plan["status"] == "blocked_pending_card_pin_inclusion_review"
    assert plan["selection"]["source_category_mapping_status"] == (
        "pending_human_review"
    )
    assert plan["selection"]["current_manifest_card_pin_status"] == (
        "not_validated"
    )
    assert plan["acceptance"]["requires_card_pin_inclusion_review"] is True
    assert plan["seed"] == "20261026"
    assert plan["target"]["total_examples"] == 100
    assert plan["target"]["categories"] == 50
    assert plan["target"]["examples_per_category"] == 2
    assert plan["annotation"]["double_annotation_count"] == 20
    assert "audit" in plan["annotation"]["audit_selection"]
    assert (
        plan["acceptance"]["rubric_sha256"]
        == summary_quality_rubric_sha256()
    )


def test_manifest_is_balanced_and_unlabeled() -> None:
    """The manifest should cover every category without inventing labels."""
    manifest = load_json(CALIBRATION_DIR / "sample_manifest.json")
    examples = manifest["examples"]
    golden = load_json(ROOT / "dataset" / "v1" / "golden_dataset.json")
    golden_inputs = {
        normalize(case["input"])
        for case in golden
        if isinstance(case.get("input"), str)
    }

    assert manifest["total_examples"] == 100
    assert manifest["manifest_status"] == (
        "provisional_card_pin_pending_review"
    )
    assert manifest["pending_review_categories"] == ["card_pin"]
    assert len(examples) == 100
    assert manifest["source_category_overrides"]["card_pin"] == [
        "get_physical_card",
        "pin_blocked",
    ]

    ids = [example["id"] for example in examples]
    assert len(ids) == len(set(ids))
    assert all(example["source_input"].strip() for example in examples)
    assert all(
        normalize(example["source_input"]) not in golden_inputs
        for example in examples
    )
    assert all(
        not {"expected_summary", "actual_summary", "human_score"}
        & set(example)
        for example in examples
    )

    counts = Counter(example["category"] for example in examples)
    assert len(counts) == 50
    assert set(counts.values()) == {2}

    bands: dict[str, list[str]] = {}
    for example in examples:
        bands.setdefault(example["category"], []).append(
            example["length_band"]
        )
    assert all(sorted(values) == ["long", "short"] for values in bands.values())

    card_pin = [
        example
        for example in examples
        if example["category"] == "card_pin"
    ]
    assert len(card_pin) == 2
    assert all(
        example["inclusion_status"] == "pending_human_review"
        for example in card_pin
    )
    assert all(
        example["source_category"] in {"get_physical_card", "pin_blocked"}
        for example in card_pin
    )


def test_annotation_workbook_is_header_only() -> None:
    """The workbook should provide fields without provisional labels."""
    with (CALIBRATION_DIR / "annotation_workbook.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fields = set(reader.fieldnames or [])

    assert rows == []
    assert {
        "id",
        "source_input",
        "expected_summary",
        "actual_summary",
        "human_score",
        "annotator_id",
    } <= fields


def test_double_annotation_ids_are_reproducible_and_unlabeled() -> None:
    """The audit subset should be deterministic and contain no scores."""
    manifest = load_json(CALIBRATION_DIR / "sample_manifest.json")
    audit = load_json(CALIBRATION_DIR / "double_annotation_ids.json")
    manifest_ids = {example["id"] for example in manifest["examples"]}

    assert audit["status"] == "provisional_card_pin_pending_review"
    assert audit["count"] == 20
    assert len(audit["ids"]) == 20
    assert len(set(audit["ids"])) == 20
    assert set(audit["ids"]) <= manifest_ids
    assert audit["manifest_sha256"] == hashlib.sha256(
        (CALIBRATION_DIR / "sample_manifest.json").read_bytes()
    ).hexdigest()
    assert audit["pending_review_ids"] == [
        "banking77-test-0553",
        "banking77-test-0524",
    ]
    assert all(
        "human_score" not in example
        for example in audit["examples"]
    )


def test_manifest_validation_records_the_blocking_issue() -> None:
    """Structural checks should pass while semantic card_pin review blocks."""
    report = load_json(CALIBRATION_DIR / "manifest_validation.json")

    assert report["status"] == "structural_pass_semantic_blocked"
    assert all(report["checks"].values())
    assert report["rubric_sha256"] == summary_quality_rubric_sha256()
    assert report["manifest_sha256"] == hashlib.sha256(
        (CALIBRATION_DIR / "sample_manifest.json").read_bytes()
    ).hexdigest()
    assert any("card_pin" in issue for issue in report["blocking_issues"])


def test_card_pin_review_documents_the_mapping_limitation() -> None:
    """The review should block exact-label treatment until a human decides."""
    review = (CALIBRATION_DIR / "card_pin_coverage_review.md").read_text(
        encoding="utf-8"
    )

    assert "not exact" in review
    assert "pending human inclusion review" in review
    assert "get_physical_card" in review
    assert "pin_blocked" in review
    assert "Human-reviewed inclusion criteria" in review
    assert "banking77-test-0553" in review
    assert "banking77-test-0524" in review

    with (CALIBRATION_DIR / "card_pin_inclusion_review.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fields = set(reader.fieldnames or [])

    assert rows == []
    assert {
        "id",
        "source_category",
        "decision",
        "primary_intent",
        "rationale",
        "reviewer_id",
        "reviewed_at",
    } <= fields


def test_manifest_matches_the_reproducible_generator(tmp_path: Path) -> None:
    """Regenerating the manifest should reproduce the checked-in artifact."""
    output_path = tmp_path / "sample_manifest.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_calibration_sample.py",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert load_json(output_path) == load_json(
        CALIBRATION_DIR / "sample_manifest.json"
    )
