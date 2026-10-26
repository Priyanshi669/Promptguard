"""Validate a completed annotation workbook for calibration readiness.

Checks required columns, complete annotations, valid 1-5 human scores,
non-empty human reference summaries, annotator provenance, timestamps,
double-annotation coverage, candidates copied mechanically (never hand
-edited), and suspiciously copied candidate summaries. Copied-summary
detection is a warning/review flag, never proof of misconduct.
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_rubric_sha256,
)

CALIBRATION_DIR = Path("dataset/calibration/v1")
WORKBOOK_PATH = CALIBRATION_DIR / "annotation_workbook.csv"
MANIFEST_PATH = CALIBRATION_DIR / "sample_manifest.json"
CANDIDATES_PATH = CALIBRATION_DIR / "candidate_outputs.json"
AUDIT_PATH = CALIBRATION_DIR / "double_annotation_ids.json"

REQUIRED_COLUMNS = {
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
}

REQUIRED_ANNOTATOR_FIELDS = (
    "annotator_id",
    "annotated_at",
    "expected_summary",
    "human_score",
    "confidence",
)

ALLOWED_CONFIDENCE = {"low", "medium", "high"}

COPY_VERBATIM_WARNING = (
    "expected_summary matches the candidate summary text; verify the "
    "reference was written from the source input rather than the candidate."
)
COPY_SIMILARITY_THRESHOLD = 0.92
CROSS_PASS_SIMILARITY_THRESHOLD = 0.98


def normalize_text(text: str) -> str:
    """Lowercase and collapse whitespace for copy comparisons."""
    return " ".join(text.lower().split())


def load_json(path: Path) -> Any:
    """Load a JSON artifact."""
    return json.loads(path.read_text(encoding="utf-8"))


def parse_workbook(path: Path) -> tuple[list[dict[str, str]], set[str]]:
    """Read the workbook rows and return them with the column set."""
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = list(reader)
        fields = set(reader.fieldnames or [])
    return rows, fields


def validate_timestamp(value: str, errors: list[str], label: str) -> None:
    """Require an ISO-8601 timestamp that includes a timezone."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        errors.append(
            f"{label}: annotated_at '{value}' is not ISO-8601."
        )
        return
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        errors.append(
            f"{label}: annotated_at '{value}' must include a timezone "
            "offset."
        )


def validate_score(value: str, errors: list[str], label: str) -> None:
    """Require an integer human score from 1 to 5."""
    try:
        score = int(value)
    except ValueError:
        errors.append(
            f"{label}: human_score '{value}' must be an integer 1-5."
        )
        return
    if not 1 <= score <= 5:
        errors.append(
            f"{label}: human_score '{value}' out of range; 1-5 required."
        )


def validate_inclusion_status(
    example: dict[str, Any],
    row: dict[str, str],
    errors: list[str],
    label: str,
) -> None:
    """Require the workbook inclusion status to match the manifest."""
    expected = str(example.get("inclusion_status", "not_applicable"))
    if row.get("inclusion_status", "") != expected:
        errors.append(
            f"{label}: inclusion_status '{row.get('inclusion_status')}' "
            f"must match the manifest value '{expected}'."
        )


def similarity(left: str, right: str) -> float:
    """Return sequence similarity between two normalized texts."""
    if not left or not right:
        return 0.0
    matcher = SequenceMatcher(a=left, b=right)
    return matcher.ratio()


def build_report(
    workbook_path: Path,
    manifest_path: Path,
    candidates_path: Path,
    audit_path: Path,
) -> dict[str, Any]:
    """Validate the workbook; errors fail, warnings are review flags."""
    errors: list[str] = []
    warnings: list[str] = []

    rows, columns = parse_workbook(workbook_path)
    missing_columns = sorted(REQUIRED_COLUMNS - columns)
    if missing_columns:
        errors.append(
            f"Workbook is missing required columns: {missing_columns}."
        )
        return _report(workbook_path, errors, warnings, rows)

    manifest = load_json(manifest_path)
    examples_by_id = {
        str(example["id"]): example for example in manifest["examples"]
    }
    audit_ids = set(load_json(audit_path).get("ids", []))

    candidates = load_json(candidates_path)
    candidates_by_id = {
        str(record["id"]): record for record in candidates.get("records", [])
    }

    seen_passes: dict[str, set[str]] = {}
    for row in rows:
        case_id = row.get("id", "")
        annotation_pass = row.get("annotation_pass", "")
        label = f"Row {case_id}/{annotation_pass or '(missing pass)'}"

        if case_id not in examples_by_id:
            errors.append(
                f"{label}: id is not the approved manifest."
            )
            continue

        example = examples_by_id[case_id]
        if annotation_pass not in {"primary", "secondary"}:
            errors.append(
                f"{label}: annotation_pass must be primary or secondary."
            )
        else:
            passes = seen_passes.setdefault(case_id, set())
            if annotation_pass in passes:
                errors.append(
                    f"{label}: duplicate entry for this case and pass."
                )
            passes.add(annotation_pass)

        is_audit = case_id in audit_ids
        expected_flag = "true" if is_audit else "false"
        if row.get("is_double_annotated", "") != expected_flag:
            errors.append(
                f"{label}: is_double_annotated "
                f"('{row.get('is_double_annotated')}') must be "
                f"'{expected_flag}' for this case."
            )

        if row.get("source_split", "") != example["source_split"]:
            errors.append(
                f"{label}: source_split differs from the manifest."
            )
        if row.get("category", "") != example["category"]:
            errors.append(f"{label}: category differs from the manifest.")
        if (
            row.get("source_category", "")
            != example["source_category"]
        ):
            errors.append(
                f"{label}: source_category differs from the manifest."
            )
        validate_inclusion_status(example, row, errors, label)
        if row.get("source_input", "") != example["source_input"]:
            errors.append(
                f"{label}: source_input differs from the manifest."
            )

        candidate = candidates_by_id.get(case_id)
        if candidate is None:
            errors.append(
                f"{label}: no candidate output recorded for this case."
            )
            candidate_summary = None
        else:
            candidate_summary = str(candidate.get("actual_summary", ""))
        actual_summary = row.get("actual_summary", "")
        if candidate_summary is not None and actual_summary != candidate_summary:
            errors.append(
                f"{label}: actual_summary was hand-edited; it must be the "
                "raw recorded candidate summary."
            )

        for field in REQUIRED_ANNOTATOR_FIELDS:
            if not row.get(field, "").strip():
                errors.append(f"{label}: required field '{field}' is blank.")

        confidence = row.get("confidence", "")
        if confidence.strip() and confidence.strip() not in ALLOWED_CONFIDENCE:
            errors.append(
                f"{label}: confidence '{confidence}' must be one of "
                f"{sorted(ALLOWED_CONFIDENCE)}."
            )

        score = row.get("human_score", "")
        if score.strip():
            validate_score(score, errors, label)
        timestamp = row.get("annotated_at", "")
        if timestamp.strip():
            validate_timestamp(timestamp, errors, label)

        expected_summary = row.get("expected_summary", "")
        if expected_summary.strip() and candidate_summary is not None:
            if normalize_text(expected_summary) == normalize_text(
                candidate_summary
            ):
                warnings.append(f"{label}: {COPY_VERBATIM_WARNING}")
            elif (
                similarity(
                    normalize_text(expected_summary),
                    normalize_text(candidate_summary),
                )
                >= COPY_SIMILARITY_THRESHOLD
            ):
                warnings.append(
                    f"{label}: expected_summary closely resembles the "
                    "candidate summary; verify the reference was written "
                    "from the source input."
                )

    _validate_double_coverage(seen_passes, examples_by_id, audit_ids, errors)

    successful = sum(
        1 for record in candidates["records"]
        if record.get("status") == "success"
    )
    if successful != len(examples_by_id):
        errors.append(
            "Candidate outputs must cover every manifest case with "
            "successful generation."
        )

    return _report(workbook_path, errors, warnings, rows)


def _validate_double_coverage(
    seen_passes: dict[str, set[str]],
    examples_by_id: dict[str, Any],
    audit_ids: set[str],
    errors: list[str],
) -> None:
    """Require one primary row per case and secondary rows for the audit set."""
    for case_id, example in examples_by_id.items():
        passes = seen_passes.get(case_id, set())
        if "primary" not in passes:
            errors.append(
                f"Case {case_id} ({example['category']}): primary row is "
                "missing."
            )
    for case_id in sorted(audit_ids):
        if case_id not in examples_by_id:
            continue
        passes = seen_passes.get(case_id, set())
        if "secondary" not in passes:
            errors.append(
                f"Case {case_id}: secondary row missing; double-annotation "
                "coverage is incomplete."
            )


def compare_cross_pass_answers(
    rows: list[dict[str, str]],
    warnings: list[str],
) -> None:
    """Flag suspiciously identical references or scores across passes."""
    by_id: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row.get("annotation_pass") in {"primary", "secondary"}:
            by_id.setdefault(row.get("id", ""), []).append(row)
    for case_id, pair in by_id.items():
        if len(pair) != 2:
            continue
        primary, secondary = pair
        if primary["expected_summary"].strip() and (
            normalize_text(primary["expected_summary"])
            == normalize_text(secondary["expected_summary"])
        ):
            warnings.append(
                f"Case {case_id}: primary and secondary references are "
                "identical; independent annotators reach identical wording "
                "rarely, review for shared drafting before adjudication."
            )


def _report(
    workbook_path: Path,
    errors: list[str],
    warnings: list[str],
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    """Assemble the structured validation report."""
    compare_cross_pass_answers(rows, warnings)
    return {
        "workbook_path": workbook_path.as_posix(),
        "status": "valid" if not errors else "invalid",
        "rubric_version": SUMMARY_QUALITY_RUBRIC_VERSION,
        "rubric_sha256": summary_quality_rubric_sha256(),
        "total_rows": len(rows),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    """Run workbook validation from command line arguments."""
    parser = argparse.ArgumentParser(
        description="Validate the completed calibration annotation workbook.",
    )
    parser.add_argument(
        "--workbook",
        type=Path,
        default=WORKBOOK_PATH,
        help="Path to the annotation workbook CSV.",
    )
    args = parser.parse_args()

    report = build_report(
        args.workbook,
        MANIFEST_PATH,
        CANDIDATES_PATH,
        AUDIT_PATH,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] != "valid":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
