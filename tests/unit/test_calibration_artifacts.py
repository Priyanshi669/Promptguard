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

    assert plan["status"] == "manifest_approved_awaiting_human_annotation"
    assert plan["selection"]["source_category_mapping_status"] == (
        "superseded_by_per_case_human_review"
    )
    assert plan["selection"]["current_manifest_card_pin_status"] == (
        "human_validated"
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
    assert manifest["manifest_status"] == "approved"
    assert manifest["pending_review_categories"] == []
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
        example["inclusion_status"] == "included"
        for example in card_pin
    )
    assert all(
        example["source_category"] in {"get_physical_card", "pin_blocked"}
        for example in card_pin
    )


def test_annotation_workbook_carries_candidate_text_and_blanks_reviewers() -> None:
    """Human fields stay blank; candidate text is copied verbatim by ID."""
    manifest = load_json(CALIBRATION_DIR / "sample_manifest.json")
    audit = load_json(CALIBRATION_DIR / "double_annotation_ids.json")
    manifest_ids = {example["id"] for example in manifest["examples"]}

    with (CALIBRATION_DIR / "annotation_workbook.csv").open(
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fields = set(reader.fieldnames or [])

    assert len(rows) == 120
    assert len({row["id"] for row in rows}) == 100
    assert {row["id"] for row in rows} == manifest_ids
    assert sum(row["annotation_pass"] == "primary" for row in rows) == 100
    assert sum(row["annotation_pass"] == "secondary" for row in rows) == 20
    assert {
        row["id"]
        for row in rows
        if row["annotation_pass"] == "secondary"
    } == set(audit["ids"])
    # Reviewer state after the annotator-1 batch (2026-10-27): every
    # primary row is scored; the 20 secondary rows still await the
    # independent second annotator.
    scored_passes = {
        row["annotation_pass"]
        for row in rows
        if (row["expected_summary"] or row["human_score"]).strip()
    }
    assert scored_passes == {"primary"}
    assert all(
        row[field] == ""
        for row in rows
        if row["annotation_pass"] == "secondary"
        for field in (
            "expected_summary",
            "human_score",
            "annotator_id",
            "annotated_at",
            "confidence",
        )
    )
    candidates = load_json(CALIBRATION_DIR / "candidate_outputs.json")
    recorded = {
        str(record["id"]): str(record.get("actual_summary", ""))
        for record in candidates.get("records", [])
    }
    assert all(
        row["actual_summary"] == recorded[row["id"]]
        for row in rows
    )
    plan = load_json(CALIBRATION_DIR / "sampling_plan.json")
    assert fields == set(plan["annotation"]["workbook_fields"])
    assert {
        "annotation_pass",
        "is_double_annotated",
        "inclusion_status",
        "annotator_id",
    } <= fields


def test_annotation_queue_matches_manifest_and_audit() -> None:
    """The queue should expose the 100 cases and 20 audit cases."""
    manifest = load_json(CALIBRATION_DIR / "sample_manifest.json")
    audit = load_json(CALIBRATION_DIR / "double_annotation_ids.json")
    queue = load_json(CALIBRATION_DIR / "annotation_queue.json")

    assert queue["status"] == "approved"
    assert queue["manifest_sha256"] == hashlib.sha256(
        (CALIBRATION_DIR / "sample_manifest.json").read_bytes()
    ).hexdigest()
    assert queue["rubric_sha256"] == summary_quality_rubric_sha256()
    assert queue["total_examples"] == 100
    assert queue["audit_examples"] == 20
    assert queue["pending_review_examples"] == 0
    assert len(queue["items"]) == 100
    assert {item["id"] for item in queue["items"]} == {
        example["id"] for example in manifest["examples"]
    }
    assert {
        item["id"] for item in queue["items"] if item["audit"]
    } == set(audit["ids"])
    assert all(
        item["inclusion_status"] == "included"
        for item in queue["items"]
        if item["category"] == "card_pin"
    )


def test_double_annotation_ids_are_reproducible_and_unlabeled() -> None:
    """The audit subset should be deterministic and contain no scores."""
    manifest = load_json(CALIBRATION_DIR / "sample_manifest.json")
    audit = load_json(CALIBRATION_DIR / "double_annotation_ids.json")
    manifest_ids = {example["id"] for example in manifest["examples"]}

    assert audit["status"] == "approved"
    assert audit["count"] == 20
    assert len(audit["ids"]) == 20
    assert len(set(audit["ids"])) == 20
    assert set(audit["ids"]) <= manifest_ids
    assert audit["manifest_sha256"] == hashlib.sha256(
        (CALIBRATION_DIR / "sample_manifest.json").read_bytes()
    ).hexdigest()
    assert audit["pending_review_ids"] == []
    assert all(
        "human_score" not in example
        for example in audit["examples"]
    )


def test_manifest_validation_records_the_blocking_issue() -> None:
    """Structural checks pass; card_pin review is complete, not blocking."""
    report = load_json(CALIBRATION_DIR / "manifest_validation.json")

    assert report["status"] == "approved"
    assert report["checks"]["card_pin_pending_review"] is False
    assert all(
        value is True for key, value in report["checks"].items()
        if key != "card_pin_pending_review"
    )
    assert report["rubric_sha256"] == summary_quality_rubric_sha256()
    assert report["manifest_sha256"] == hashlib.sha256(
        (CALIBRATION_DIR / "sample_manifest.json").read_bytes()
    ).hexdigest()
    assert report["blocking_issues"] == []


def test_card_pin_review_documents_the_mapping_limitation() -> None:
    """Every manifest card_pin case must carry a human include decision."""
    review = (CALIBRATION_DIR / "card_pin_coverage_review.md").read_text(
        encoding="utf-8"
    )

    assert "not exact" in review
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

    assert {row["id"] for row in rows} == {
        "banking77-test-0553",
        "banking77-test-0524",
        "banking77-test-1247",
        "banking77-test-0540",
        "banking77-test-0548",
        "banking77-test-0542",
        "banking77-test-1264",
    }
    assert all(
        row["decision"] in ("include", "exclude")
        and row["rationale"].startswith("Reviewed by reviewer 1.")
        and row["reviewer_id"] == "1"
        and row["reviewed_at"]
        for row in rows
    )

    manifest = load_json(CALIBRATION_DIR / "sample_manifest.json")
    manifest_card_pin = [
        example for example in manifest["examples"]
        if example["category"] == "card_pin"
    ]
    included_ids = {
        row["id"] for row in rows if row["decision"] == "include"
    }
    assert {str(example["id"]) for example in manifest_card_pin} <= included_ids

    round_one = [
        row
        for row in rows
        if row["id"] in ("banking77-test-0553", "banking77-test-0524")
    ]
    assert all(
        row["decision"] == "exclude"
        and row["rationale"].startswith("Reviewed by reviewer 1.")
        and row["reviewer_id"] == "1"
        and row["reviewed_at"]
        for row in round_one
    )
    assert len(round_one) == 2

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
