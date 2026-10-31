"""Build the reviewer workbook and audit queue from the manifest.

The workbook pre-populates ``actual_summary`` mechanically from the
recorded candidate outputs, matched by sample ID and never hand-edited.
Reviewer-owned fields (expected summary, score, annotator identity,
timestamp, confidence, notes) stay blank until real reviewers fill them.
"""

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
CANDIDATES_PATH = CALIBRATION_DIR / "candidate_outputs.json"
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

REVIEWER_FIELDS = (
    "expected_summary",
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


def build_candidate_summaries_by_id(
    manifest: dict[str, Any],
    candidates: dict[str, Any],
) -> dict[str, str]:
    """Map manifest case IDs to raw recorded candidate summaries.

    Every manifest case must have a successful recorded candidate;
    anything else fails loudly instead of shipping a half-populated
    workbook. Summaries are copied verbatim by case ID, never by
    position.
    """
    manifest_ids = {str(example["id"]) for example in manifest["examples"]}
    summaries: dict[str, str] = {}
    for record in candidates.get("records", []):
        case_id = str(record["id"])
        if case_id not in manifest_ids:
            continue
        if record.get("status") != "success":
            raise ValueError(
                f"Candidate generation failed for {case_id}; "
                "regenerate before building the workbook."
            )
        summaries[case_id] = str(record.get("actual_summary", ""))

    missing = sorted(manifest_ids - set(summaries))
    if missing:
        raise ValueError(
            f"No candidate outputs for: {missing[:5]}. "
            "Generate candidates for every manifest case first."
        )
    return summaries


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
    candidate_summaries_by_id: dict[str, str],
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
            "actual_summary": candidate_summaries_by_id[str(item["id"])],
        }
        rows.append(
            {
                **base,
                "annotation_pass": "primary",
                **{field: "" for field in REVIEWER_FIELDS},
            }
        )
        if item["audit"]:
            rows.append(
                {
                    **base,
                    "annotation_pass": "secondary",
                    **{field: "" for field in REVIEWER_FIELDS},
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
    """Write the reviewer workbook."""
    with WORKBOOK_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=WORKBOOK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Build the queue and workbook from checked-in planning artifacts."""
    manifest = load_json(MANIFEST_PATH)
    audit = load_json(AUDIT_PATH)
    candidates = load_json(CANDIDATES_PATH)
    current_manifest_hash = manifest_hash(MANIFEST_PATH)
    queue = build_queue(manifest, audit, current_manifest_hash)
    candidate_summaries = build_candidate_summaries_by_id(manifest, candidates)
    rows = build_workbook_rows(queue, candidate_summaries)
    write_queue(queue, manifest, current_manifest_hash)
    write_workbook(rows)

    print(f"Queue examples: {len(queue)}")
    print(f"Audit examples: {sum(bool(item['audit']) for item in queue)}")
    print(f"Workbook rows: {len(rows)}")
    print(f"Candidate summaries prefilled: {len(candidate_summaries)}")
    print(f"Workbook: {WORKBOOK_PATH}")


if __name__ == "__main__":
    main()
