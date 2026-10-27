"""Read-only access to the calibration draft artifacts.

Every function here opens files for reading only. Nothing in this module
creates, writes, renames, or deletes any calibration artifact (enforced
by tests that hash the files before and after invokes).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from promptguard.domain.calibration_draft import CandidateProvenance

WORKBOOK_COLUMNS = (
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

SCORING_FIELDS = ("human_score",)


class DraftReader:
    """Bundle of artifact locations for one calibration draft version."""

    def __init__(self, calibration_dir: Path) -> None:
        """Point at a version directory such as ``dataset/calibration/v1``."""
        self.dir = calibration_dir
        self.manifest_path = calibration_dir / "sample_manifest.json"
        self.audit_path = calibration_dir / "double_annotation_ids.json"
        self.candidates_path = calibration_dir / "candidate_outputs.json"
        self.queue_path = calibration_dir / "annotation_queue.json"
        self.workbook_path = calibration_dir / "annotation_workbook.csv"
        self.packet_metadata_path = (
            calibration_dir / "annotation_packets" / "packets_metadata.json"
        )
        self.calibration_report_path = (
            calibration_dir / "calibration_report.json"
        )
        self.card_pin_review_path = (
            calibration_dir / "card_pin_recommendations.md"
        )
        self.card_pin_inclusion_path = (
            calibration_dir / "card_pin_inclusion_review.csv"
        )

    def draft_exists(self) -> bool:
        """Return whether the prepared draft workbook is present."""
        return self.workbook_path.is_file() and self.manifest_path.is_file()

    def load_json(self, path: Path) -> Any:
        """Load one JSON artifact."""
        return json.loads(path.read_text(encoding="utf-8"))

    def load_manifest(self) -> dict[str, Any]:
        """Load the sampling manifest."""
        return self.load_json(self.manifest_path)

    def load_audit(self) -> dict[str, Any]:
        """Load the double-annotation audit subset."""
        return self.load_json(self.audit_path)

    def load_candidates(self) -> dict[str, Any]:
        """Load recorded candidate outputs with provenance."""
        return self.load_json(self.candidates_path)

    def candidates_by_id(self) -> dict[str, dict[str, Any]]:
        """Index candidate records by case ID."""
        return {
            str(record["id"]): record
            for record in self.load_candidates().get("records", [])
        }

    def read_workbook(self) -> tuple[list[dict[str, str]], set[str]]:
        """Read the prepared workbook; return rows and the column set."""
        with self.workbook_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            columns = set(reader.fieldnames or [])
            rows = [dict(row) for row in reader]
        return rows, columns

    def candidate_provenance(
        self,
        record: dict[str, Any],
    ) -> CandidateProvenance:
        """Extract the non-secret AI candidate provenance for one record."""
        return CandidateProvenance(
            candidate_model=str(record.get("model", "")),
            candidate_run_id=str(record.get("run_id", "")),
            candidate_generated_at=str(record.get("generated_at", "")),
            candidate_status=str(record.get("status", "")),
            prompt_version=str(record.get("prompt_version", "")),
        )
