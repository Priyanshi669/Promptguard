"""Build annotation packet files for the calibration annotators.

Each packet contains the sample ID, original source input, manifest
category, candidate output, and the annotation fields the annotator must
fill. Primary packets cover all 100 cases; secondary packets cover the
20 double-annotation cases and are generated independently, with no
reference to any primary annotator's answers.

This script records provenance (rubric identity, manifest hash, candidate
run identity) in a metadata file adjacent to the packets.
"""

import argparse
import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_rubric_sha256,
)

CALIBRATION_DIR = Path("dataset/calibration/v1")
MANIFEST_PATH = CALIBRATION_DIR / "sample_manifest.json"
CANDIDATES_PATH = CALIBRATION_DIR / "candidate_outputs.json"
AUDIT_PATH = CALIBRATION_DIR / "double_annotation_ids.json"
PACKETS_DIR = CALIBRATION_DIR / "annotation_packets"

ANNOTATED_FIELDS = (
    "expected_summary",
    "human_score",
    "confidence",
    "notes",
    "annotator_id",
    "annotated_at",
)

PACKET_ID_FIELDS = (
    "id",
    "annotation_pass",
    "is_double_annotated",
    "source_split",
    "category",
    "source_category",
    "source_input",
    "actual_summary",
)


def load_json(path: Path) -> Any:
    """Load a JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def build_answerable_records_from_candidates(
    manifest: dict[str, Any],
    candidates: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Map candidate outputs by manifest case id, requiring full success."""
    manifest_ids = {str(example["id"]) for example in manifest["examples"]}
    records: dict[str, dict[str, Any]] = {}
    for record in candidates.get("records", []):
        case_id = str(record["id"])
        if case_id not in manifest_ids:
            continue
        if record.get("status") != "success":
            raise ValueError(
                f"Candidate generation failed for {case_id}; "
                "regenerate before annotating."
            )
        records[case_id] = record

    missing = sorted(manifest_ids - set(records))
    if missing:
        raise ValueError(
            f"No candidate outputs for: {missing[:5]}. "
            "Generate candidates for every manifest case first."
        )
    return records


def build_packet_rows(
    manifest: dict[str, Any],
    candidates_by_id: dict[str, dict[str, Any]],
    audit_ids: set[str],
    annotation_pass: str,
    ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Build one packet row per in-scope manifest case.

    Reviewer answers are left blank; the candidate summary is prefilled
    mechanically from the recorded output, never hand-edited.
    """
    rows: list[dict[str, str]] = []
    for example in manifest["examples"]:
        case_id = str(example["id"])
        if ids is not None and case_id not in ids:
            continue
        record = candidates_by_id[case_id]
        rows.append(
            {
                "id": case_id,
                "annotation_pass": annotation_pass,
                "is_double_annotated": "true" if case_id in audit_ids else "false",
                "source_split": str(example["source_split"]),
                "category": str(example["category"]),
                "source_category": str(example["source_category"]),
                "source_input": str(example["source_input"]),
                "actual_summary": str(record.get("actual_summary", "")),
                **{field: "" for field in ANNOTATED_FIELDS},
            }
        )
    return rows


def write_packet(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    """Write one annotator packet CSV."""
    fields = list(PACKET_ID_FIELDS) + list(ANNOTATED_FIELDS)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Build the independent primary and secondary annotation packets."""
    parser = argparse.ArgumentParser(
        description="Build annotation packets for the calibration annotators.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=PACKETS_DIR,
        help="Directory where packet files are written.",
    )
    args = parser.parse_args()

    manifest = load_json(MANIFEST_PATH)
    candidates = load_json(CANDIDATES_PATH)
    audit = load_json(AUDIT_PATH)

    candidates_by_id = build_answerable_records_from_candidates(
        manifest,
        candidates,
    )
    audit_ids = set(audit.get("ids", []))

    primary_rows = build_packet_rows(
        manifest,
        candidates_by_id,
        audit_ids,
        annotation_pass="primary",
    )
    secondary_rows = build_packet_rows(
        manifest,
        candidates_by_id,
        audit_ids,
        annotation_pass="secondary",
        ids=audit_ids,
    )

    primary_path = args.out_dir / "primary_packet.csv"
    secondary_path = args.out_dir / "secondary_packet.csv"
    write_packet(primary_path, primary_rows)
    write_packet(secondary_path, secondary_rows)

    metadata = {
        "version": "v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "rubric_version": SUMMARY_QUALITY_RUBRIC_VERSION,
        "rubric_sha256": summary_quality_rubric_sha256(),
        "manifest_sha256": hashlib.sha256(
            MANIFEST_PATH.read_bytes()
        ).hexdigest(),
        "candidate_run_ids": sorted(
            {str(r.get("run_id")) for r in candidates["records"] if r.get("run_id")},
        ),
        "primary_packet": {
            "path": primary_path.as_posix(),
            "cases": len(primary_rows),
        },
        "secondary_packet": {
            "path": secondary_path.as_posix(),
            "cases": len(secondary_rows),
            "note": "Secondary annotator works independently; packet content "
            "identical in structure and shares no other annotator's answers.",
        },
    }
    metadata_path = args.out_dir / "packets_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"Primary packet: {len(primary_rows)} rows -> {primary_path}")
    print(f"Secondary packet: {len(secondary_rows)} rows -> {secondary_path}")
    print(f"Metadata: {metadata_path}")


if __name__ == "__main__":
    main()
