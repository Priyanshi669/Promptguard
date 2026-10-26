"""Build the blank reviewer workbook and audit queue from the manifest."""

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_rubric_sha256,
)

CALIBRATION_DIR = Path("dataset/calibration/v1")
MANIFEST_PATH = CALIBRATION_DIR / "sample_manifest.json"
AUDIT_PATH = CALIBRATION_DIR / "double_annotation_ids.json"
QUEUE_PATH = CALIBRATION_DIR / "annotation_queue.json"
WORKBOOK_PATH = CALIBRATION_DIR / "annotation_workbook.csv"

WORKBOOK_FIELDS = (
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
)

BLANK_FIELDS = (
    "expected_summary",
    "actual_summary",
    "human_score",
    "annotator_id",
    "annotated_at",
    "confidence",
    "notes",
)


def load_json(path: Path) -> Any:
    """Load a JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_hash(path: Path) -> str:
    """Return the SHA-256 of a manifest file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_queue(
    manifest: dict[str, Any],
    audit: dict[str, Any],
    current_manifest_hash: str,
) -> list[dict[str, object]]:
    """Build one queue entry per manifest case."""
    if audit["manifest_sha256"] != current_manifest_hash:
        raise ValueError(
            "Audit subset does not match the current sample manifest."
        )

    audit_ids = set(audit["ids"])
    manifest_ids = {example["id"] for example in manifest["examples"]}
    if not audit_ids <= manifest_ids:
        raise ValueError("Audit subset contains IDs outside the manifest.")

    queue: list[dict[str, object]] = []
    for example in manifest["examples"]:
        queue.append(
            {
                "id": example["id"],
                "source_split": example["source_split"],
                "category": example["category"],
                "source_category": example["source_category"],
                "source_input": example["source_input"],
                "audit": example["id"] in audit_ids,
                "inclusion_status": example.get(
                    "inclusion_status",
                    "not_applicable",
                ),
            }
        )
    return queue


def build_workbook_rows(
    queue: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Build primary rows plus second-annotator rows for the audit subset."""
    rows: list[dict[str, object]] = []
    for item in queue:
        base = {
            "id": item["id"],
            "source_split": item["source_split"],
            "category": item["category"],
            "source_category": item["source_category"],
            "inclusion_status": item["inclusion_status"],
            "source_input": item["source_input"],
            "is_double_annotated": "true" if item["audit"] else "false",
        }
        rows.append(
            {
                **base,
                "annotation_pass": "primary",
                **{field: "" for field in BLANK_FIELDS},
            }
        )
        if item["audit"]:
            rows.append(
                {
                    **base,
                    "annotation_pass": "secondary",
                    **{field: "" for field in BLANK_FIELDS},
                }
            )
    return rows


def write_queue(
    queue: list[dict[str, object]],
    manifest: dict[str, Any],
    current_manifest_hash: str,
) -> None:
    """Write the queue with provenance metadata."""
    payload = {
        "version": "v1",
        "status": manifest["manifest_status"],
        "manifest_sha256": current_manifest_hash,
        "rubric_version": SUMMARY_QUALITY_RUBRIC_VERSION,
        "rubric_sha256": summary_quality_rubric_sha256(),
        "total_examples": len(queue),
        "audit_examples": sum(bool(item["audit"]) for item in queue),
        "pending_review_examples": sum(
            item["inclusion_status"] == "pending_human_review"
            for item in queue
        ),
        "items": queue,
    }
    QUEUE_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_workbook(rows: list[dict[str, object]]) -> None:
    """Write the blank reviewer workbook."""
    with WORKBOOK_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=WORKBOOK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Build the queue and workbook from checked-in planning artifacts."""
    manifest = load_json(MANIFEST_PATH)
    audit = load_json(AUDIT_PATH)
    current_manifest_hash = manifest_hash(MANIFEST_PATH)
    queue = build_queue(manifest, audit, current_manifest_hash)
    rows = build_workbook_rows(queue)
    write_queue(queue, manifest, current_manifest_hash)
    write_workbook(rows)

    print(f"Queue examples: {len(queue)}")
    print(f"Audit examples: {sum(bool(item['audit']) for item in queue)}")
    print(f"Workbook rows: {len(rows)}")
    print(f"Workbook: {WORKBOOK_PATH}")


if __name__ == "__main__":
    main()
