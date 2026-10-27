"""Unit tests for annotation packets and workbook validation."""

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.build_annotation_package import (
    REVIEWER_FIELDS,
    build_candidate_summaries_by_id,
    build_workbook_rows,
)
from scripts.build_annotation_packets import (
    build_answerable_records_from_candidates,
    build_packet_rows,
)
from scripts.validate_annotation_workbook import build_report

FIXTURES_DIR = Path("tests/unit/data/annotation_workbook")


def load_json(path: Path) -> Any:
    """Load JSON fixture content."""
    return json.loads(path.read_text(encoding="utf-8"))


def write_workbook(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write workbook rows to a temporary CSV file."""
    fields = [
        "id",
        "annotation_pass",
        "is_double_annotated",
        "source_split",
        "category",
        "source_category",
        "inclusion_status",
        "source_input",
        "expected_summary",
        "actual_summary",
        "human_score",
        "annotator_id",
        "annotated_at",
        "confidence",
        "notes",
    ]
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def calibration_data(tmp_path: Path) -> dict[str, Path]:
    """Build a minimal manifest, candidates, and audit fixture set."""
    examples = [
        {
            "id": "banking77-test-1768",
            "source_split": "test",
            "category": "Refund_not_showing_up",
            "source_category": "Refund_not_showing_up",
            "source_input": "How long does it take for a refund?",
            "word_count": 8,
            "length_band": "short",
        },
        {
            "id": "banking77-test-1797",
            "source_split": "test",
            "category": "Refund_not_showing_up",
            "source_category": "Refund_not_showing_up",
            "source_input": "Waiting a week for a seller response about a refund.",
            "word_count": 12,
            "length_band": "long",
        },
        {
            "id": "banking77-test-1247",
            "source_split": "test",
            "category": "card_pin",
            "source_category": "get_physical_card",
            "source_input": "How do I find my card PIN?",
            "word_count": 7,
            "length_band": "short",
            "inclusion_status": "included",
        },
    ]
    manifest = {
        "version": "v1",
        "manifest_status": "approved",
        "total_examples": 3,
        "examples": examples,
    }
    candidates = {
        "version": "v1",
        "run_id": "run-1",
        "total_cases": 3,
        "successful_cases": 3,
        "failed_cases": 0,
        "records": [
            {
                "id": example["id"],
                "source_input": example["source_input"],
                "prompt_version": "v1",
                "model": "m",
                "temperature": None,
                "seed": None,
                "run_id": "run-1",
                "generated_at": "2026-10-26T09:00:00+00:00",
                "status": "success",
                "actual_summary": f"Candidate summary for {example['id']}.",
                "category": example["category"],
                "latency_ms": 100.0,
                "input_tokens": 1,
                "output_tokens": 1,
            }
            for example in examples
        ],
    }
    audit = {
        "version": "v1",
        "seed": "s",
        "manifest_sha256": "0" * 64,
        "count": 1,
        "ids": ["banking77-test-1797"],
        "examples": [],
        "status": "approved",
        "pending_review_ids": [],
    }

    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "candidates.json").write_text(json.dumps(candidates))
    (tmp_path / "audit.json").write_text(json.dumps(audit))
    return {
        "manifest": tmp_path / "manifest.json",
        "candidates": tmp_path / "candidates.json",
        "audit": tmp_path / "audit.json",
    }


def build_valid_rows(
    calibration_data: dict[str, Path],
) -> list[dict[str, Any]]:
    """Return a fully valid workbook row set for the fixture data."""
    manifest = load_json(calibration_data["manifest"])
    candidates = load_json(calibration_data["candidates"])
    candidate_text = {str(r["id"]): r["actual_summary"] for r in candidates["records"]}
    audit_ids = set(load_json(calibration_data["audit"])["ids"])

    rows: list[dict[str, Any]] = []
    for example in manifest["examples"]:
        case_id = str(example["id"])
        row = {
            "id": case_id,
            "annotation_pass": "primary",
            "is_double_annotated": "true" if case_id in audit_ids else "false",
            "source_split": "test",
            "category": example["category"],
            "source_category": example["source_category"],
            "inclusion_status": example.get("inclusion_status", "not_applicable"),
            "source_input": example["source_input"],
            "expected_summary": f"A human reference different from {candidate_text[case_id]!r}.",
            "actual_summary": candidate_text[case_id],
            "human_score": 4,
            "annotator_id": "alice",
            "annotated_at": "2026-10-26T15:00:00+05:30",
            "confidence": "high",
            "notes": "",
        }
        rows.append(row)
        if case_id in audit_ids:
            secondary = dict(row)
            secondary.update(
                {
                    "annotation_pass": "secondary",
                    "expected_summary": "A clearly different wording for the same source input.",
                    "human_score": 5,
                    "annotator_id": "bob",
                    "annotated_at": "2026-10-26T15:05:00+05:30",
                }
            )
            rows.append(secondary)
    return rows


def run_validation(
    tmp_path: Path,
    calibration_data: dict[str, Path],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write rows to a temp workbook and validate it."""
    workbook_path = tmp_path / "annotation_workbook.csv"
    write_workbook(workbook_path, rows)
    return build_report(
        workbook_path,
        calibration_data["manifest"],
        calibration_data["candidates"],
        calibration_data["audit"],
    )


def test_valid_workbook_passes(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """A complete workbook validates with zero errors."""
    rows = build_valid_rows(calibration_data)
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "valid", report["errors"]
    assert report["error_count"] == 0
    assert report["total_rows"] == 4


def test_missing_required_column_fails(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """Removing a required column must fail validation."""
    rows = build_valid_rows(calibration_data)
    for row in rows:
        row.pop("human_score")
    workbook_path = tmp_path / "annotation_workbook.csv"
    fields = [
        "id",
        "annotation_pass",
        "is_double_annotated",
        "source_split",
        "category",
        "source_category",
        "inclusion_status",
        "source_input",
        "expected_summary",
        "actual_summary",
        "annotator_id",
        "annotated_at",
        "confidence",
        "notes",
    ]
    with workbook_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    report = build_report(
        workbook_path,
        calibration_data["manifest"],
        calibration_data["candidates"],
        calibration_data["audit"],
    )
    assert report["status"] == "invalid"
    assert any("missing required columns" in e for e in report["errors"])


@pytest.mark.parametrize(
    "bad_score",
    ["0", "6", "3.5", "three", "-1"],
)
def test_invalid_scores_fail(
    tmp_path: Path,
    calibration_data: dict[str, Path],
    bad_score: str,
) -> None:
    """Scores outside integer 1-5 must fail."""
    rows = build_valid_rows(calibration_data)
    rows[0]["human_score"] = bad_score
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("human_score" in e for e in report["errors"])


def test_blank_required_annotation_fields_fail(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """Missing annotator fields must fail validation."""
    rows = build_valid_rows(calibration_data)
    rows[0]["expected_summary"] = ""
    rows[3]["annotator_id"] = "   "
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("expected_summary" in e for e in report["errors"])
    assert any("annotator_id" in e for e in report["errors"])


@pytest.mark.parametrize(
    "bad_timestamp",
    ["not-a-date", "2026-10-26 15:00:00", "26/10/2026"],
)
def test_invalid_timestamps_fail(
    tmp_path: Path,
    calibration_data: dict[str, Path],
    bad_timestamp: str,
) -> None:
    """Timestamps without explicit timezone offsets must fail."""
    rows = build_valid_rows(calibration_data)
    rows[0]["annotated_at"] = bad_timestamp
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("annotated_at" in e for e in report["errors"])


def test_duplicate_entries_fail(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """Two rows with the same id and pass must fail."""
    rows = build_valid_rows(calibration_data)
    rows.append(dict(rows[0]))
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("duplicate" in e for e in report["errors"])


def test_incomplete_double_annotation_coverage_fails(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """When a case has an audit flag, its secondary row must be filled."""
    rows = build_valid_rows(calibration_data)
    rows = [row for row in rows if row["annotation_pass"] != "secondary"]
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("secondary row missing" in e for e in report["errors"])


def test_missing_primary_row_fails(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """Every manifest case needs its primary row."""
    rows = build_valid_rows(calibration_data)
    rows = [
        row for row in rows if not (
            row["id"] == "banking77-test-1768"
            and row["annotation_pass"] == "primary"
        )
    ]
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("primary row" in e for e in report["errors"])


def test_hand_edited_candidate_summary_fails(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """actual_summary must be the raw recorded candidate."""
    rows = build_valid_rows(calibration_data)
    rows[0]["actual_summary"] = "Edited by a reviewer."
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("hand-edited" in e for e in report["errors"])


def test_copied_candidate_reference_is_warning_only(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """Identical expected text to the candidate is a review flag, not an error."""
    rows = build_valid_rows(calibration_data)
    rows[0]["expected_summary"] = rows[0]["actual_summary"]
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "valid"
    assert report["warning_count"] >= 1
    assert any("candidate summary" in w for w in report["warnings"])


def test_manifest_mismatch_failures(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """Rows that contradict the manifest must fail."""
    rows = build_valid_rows(calibration_data)
    rows[0]["category"] = "wrong_category"
    rows[1]["source_input"] = "Changed input."
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "invalid"
    assert any("category differs" in e for e in report["errors"])
    assert any("source_input differs" in e for e in report["errors"])


def test_cross_pass_identical_reference_warns(
    tmp_path: Path,
    calibration_data: dict[str, Path],
) -> None:
    """Identical references across the two passes should be flagged."""
    rows = build_valid_rows(calibration_data)
    secondary = [row for row in rows if row["annotation_pass"] == "secondary"]
    primary = [row for row in rows if row["id"] == "banking77-test-1797"]
    secondary[0]["expected_summary"] = primary[0]["expected_summary"]
    report = run_validation(tmp_path, calibration_data, rows)
    assert report["status"] == "valid"
    assert any(
        "references are identical" in w for w in report["warnings"]
    )


def test_packet_rows_are_answerable_and_blinded(
    calibration_data: dict[str, Path],
) -> None:
    """Packets carry required context, no reviewer answers."""
    manifest = load_json(calibration_data["manifest"])
    candidates = load_json(calibration_data["candidates"])
    records_by_id = build_answerable_records_from_candidates(
        manifest,
        candidates,
    )
    audit_ids = set(load_json(calibration_data["audit"])["ids"])

    primary_rows = build_packet_rows(
        manifest, records_by_id, audit_ids, "primary",
    )
    secondary_rows = build_packet_rows(
        manifest, records_by_id, audit_ids, "secondary",
        ids=audit_ids,
    )

    assert len(primary_rows) == 3
    assert len(secondary_rows) == 1
    assert all(
        row["annotation_pass"] == "primary" for row in primary_rows
    )
    assert all(
        row["annotation_pass"] == "secondary" for row in secondary_rows
    )
    answerable_fields = {
        "expected_summary", "human_score", "confidence", "notes",
        "annotator_id", "annotated_at",
    }
    for row in primary_rows + secondary_rows:
        assert all(row[field] == "" for field in answerable_fields)
        assert row["actual_summary"].startswith("Candidate summary for")
    assert {row["id"] for row in secondary_rows} == audit_ids


def test_candidate_completeness_failure_raises(
    calibration_data: dict[str, Path],
) -> None:
    """A manifest case without a candidate output must be rejected."""
    manifest = load_json(calibration_data["manifest"])
    candidates = load_json(calibration_data["candidates"])
    candidates["records"] = candidates["records"][:2]
    with pytest.raises(ValueError, match="No candidate outputs"):
        build_answerable_records_from_candidates(manifest, candidates)


def workbook_queue(
    calibration_data: dict[str, Path],
) -> list[dict[str, str]]:
    """Build a workbook queue in a deliberately scrambled order."""
    manifest = load_json(calibration_data["manifest"])
    audit_ids = set(load_json(calibration_data["audit"])["ids"])
    items = [
        {
            "id": str(example["id"]),
            "source_split": str(example["source_split"]),
            "category": str(example["category"]),
            "source_category": str(example["source_category"]),
            "source_input": str(example["source_input"]),
            "audit": str(example["id"]) in audit_ids,
            "inclusion_status": str(
                example.get("inclusion_status", "not_applicable"),
            ),
        }
        for example in manifest["examples"]
    ]
    return items[::-1]


def test_workbook_rows_copy_candidate_summary_by_id(
    calibration_data: dict[str, Path],
) -> None:
    """Prefilled text follows case ID, not candidate-record position."""
    manifest = load_json(calibration_data["manifest"])
    candidates = load_json(calibration_data["candidates"])
    by_id = build_candidate_summaries_by_id(manifest, candidates)

    rows = build_workbook_rows(workbook_queue(calibration_data), by_id)
    original_records = [
        str(record["actual_summary"]) for record in candidates["records"]
    ]

    # Queue order is reversed, so position-based copying would mismatch.
    assert rows[0]["actual_summary"] != original_records[0]
    for row in rows:
        assert row["actual_summary"] == by_id[str(row["id"])]
        assert row["actual_summary"] == (
            f"Candidate summary for {row['id']}."
        )


def test_workbook_rows_never_fill_reviewer_fields(
    calibration_data: dict[str, Path],
) -> None:
    """Reviewer-owned fields stay blank in every primary and secondary row."""
    manifest = load_json(calibration_data["manifest"])
    candidates = load_json(calibration_data["candidates"])
    by_id = build_candidate_summaries_by_id(manifest, candidates)

    rows = build_workbook_rows(workbook_queue(calibration_data), by_id)

    assert len(rows) == 4
    assert all(
        row[field] == ""
        for row in rows
        for field in REVIEWER_FIELDS
    )
    assert sum(
        row["annotation_pass"] == "secondary" for row in rows
    ) == 1
    assert sum(
        row["is_double_annotated"] == "true" for row in rows
    ) == 2


def test_workbook_builder_rejects_failed_candidate(
    calibration_data: dict[str, Path],
) -> None:
    """A failed candidate generation must block workbook build."""
    manifest = load_json(calibration_data["manifest"])
    candidates = load_json(calibration_data["candidates"])
    candidates["records"][0]["status"] = "failed"
    with pytest.raises(ValueError, match="Candidate generation failed"):
        build_candidate_summaries_by_id(manifest, candidates)


def test_workbook_builder_rejects_missing_candidate(
    calibration_data: dict[str, Path],
) -> None:
    """A manifest case without any recorded candidate must block the build."""
    manifest = load_json(calibration_data["manifest"])
    candidates = load_json(calibration_data["candidates"])
    candidates["records"] = candidates["records"][:2]
    with pytest.raises(ValueError, match="No candidate outputs"):
        build_candidate_summaries_by_id(manifest, candidates)
