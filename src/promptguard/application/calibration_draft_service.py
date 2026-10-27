"""Read-only calibration draft workflows (ADR-0002).

Surfaces the owner-prepared draft with clearly separated states: draft
readiness, categorized validation findings, and calibration execution
status. No method here mutates any calibration artifact; strict
production approval remains the untouched validator script and
``promptguard-calibrate``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, Protocol

from promptguard.domain.calibration_draft import (
    CalibrationState,
    DraftFinding,
    DraftOverview,
    DraftRow,
    StrictValidationSummary,
    ValidationState,
)
from promptguard.infrastructure.calibration.draft_reader import (
    REVIEWER_FIELDS,
    DraftReader,
)

ValidatorPathPair = tuple[Path, Path, Path, Path]


class _StrictReport(Protocol):
    status: str
    total_rows: int | None
    error_count: int
    errors: list[str]
    warnings: list[str]


StrictValidator = Callable[[ValidatorPathPair], _StrictReport]


def builtin_report(
    paths: ValidatorPathPair,
) -> _StrictReport:
    """Run the repository's strict validator when importable.

    Requires a repository checkout (the validator lives under
    ``scripts`` and is not part of the installed wheel). When it cannot
    be imported, callers must surface ``validation_state = 'not_run'``
    honestly rather than inventing success.
    """
    from scripts.validate_annotation_workbook import build_report

    workbook, manifest, candidates, audit = paths
    return build_report(  # type: ignore[no-any-return,return-value]
        workbook,
        manifest,
        candidates,
        audit,
    )


def _finding_from_error(error: str) -> DraftFinding:
    """Categorize one strict-validator error line into a finding."""
    row_id = ""
    if error.startswith("Row "):
        label = error.split(":", 1)[0][len("Row "):]
        if "/" in label:
            row_id = label.split("/", 1)[0]
    for name in REVIEWER_FIELDS:
        if f"required field '{name}' is blank" in error:
            return DraftFinding(
                row_id=row_id,
                field=name,
                finding_type="pending_reviewer_field",
                message=error.strip(),
                hint=(
                    "Awaiting genuine reviewer input. Do not synthesize "
                    "values; the draft workflow expects real reviewer "
                    "annotations."
                ),
            )
    hint = None
    field = ""
    if "actual_summary was hand-edited" in error:
        field = "actual_summary"
        hint = (
            "Candidate summaries must be raw recorded text from "
            "candidate_outputs.json; copy mechanically by case ID."
        )
    return DraftFinding(
        row_id=row_id,
        field=field,
        finding_type="defect",
        message=error.strip(),
        hint=hint,
    )


def _strict_field(report: _StrictReport, field: str, default: object) -> object:
    """Read a strict-report field whether it is a dict or an object."""
    if isinstance(report, dict):
        return report.get(field, default)
    return getattr(report, field, default)


def summarize_strict_report(
    report: _StrictReport,
) -> StrictValidationSummary:
    """Categorize a strict validator report for the draft state view."""
    defects: list[DraftFinding] = []
    pending = 0
    errors = _strict_field(report, "errors", [])
    assert isinstance(errors, list)
    for error in errors:
        finding = _finding_from_error(str(error))
        if finding.finding_type == "pending_reviewer_field":
            pending += 1
            continue
        defects.append(finding)
    status = str(_strict_field(report, "status", "error"))
    raw_total = _strict_field(report, "total_rows", None)
    total_rows = int(raw_total) if isinstance(raw_total, int) else None
    warnings = _strict_field(report, "warnings", [])
    assert isinstance(warnings, list)
    raw_error_count = _strict_field(report, "error_count", len(errors))
    error_count = (
        int(raw_error_count) if isinstance(raw_error_count, int) else len(errors)
    )
    return StrictValidationSummary(
        validator="scripts_validate_annotation_workbook",
        status=status,
        total_rows=(
            int(total_rows) if total_rows is not None else None
        ),
        error_count=error_count,
        pending_reviewer_field_count=pending,
        defect_count=len(defects),
        defects=defects[:50],
        raw_errors=[str(error) for error in errors[:50]],
        warning_count=len(warnings),
    )


class CalibrationDraftService:
    """Read-only workflows over the owner-prepared calibration draft."""

    def __init__(
        self,
        calibration_dir: Path,
        strict_validator: StrictValidator | None = None,
    ) -> None:
        """Compose with explicit paths; no hidden global state."""
        self._reader = DraftReader(calibration_dir)
        self._strict_validator = strict_validator

    def overview(
        self,
        include_strict_validation: bool = True,
    ) -> DraftOverview:
        """Bundle the whole workspace state; strictly read-only."""
        reader = self._reader
        if not reader.draft_exists():
            return DraftOverview(
                draft_state="missing",
                total_rows=0,
                unique_cases=0,
                primary_rows=0,
                secondary_rows=0,
                scored_rows=0,
                scored_unique_cases=0,
                pending_reviewer_rows=0,
                double_annotation_cases=0,
                card_pin_provisional_cases=0,
                candidate_run_ids=[],
                candidate_models=[],
                validation_state="not_run",
                calibration_state="not_executed",
            )
        rows, _columns = reader.read_workbook()
        audit = reader.load_audit()
        manifest_examples = reader.load_manifest().get("examples", [])

        scored_rows = sum(
            1 for row in rows if row.get("human_score", "").strip()
        )
        scored_case_ids = {
            str(row["id"])
            for row in rows
            if row.get("human_score", "").strip()
        }
        pending_rows = sum(
            1 for row in rows if not row.get("human_score", "").strip()
        )
        secondary_rows = sum(
            1 for row in rows
            if row.get("annotation_pass", "") == "secondary"
        )
        card_pin_cases = sum(
            1 for example in manifest_examples
            if str(example.get("category", "")) == "card_pin"
        )

        candidate_records = list(
            reader.load_candidates().get("records", []),
        )
        run_ids: list[str] = []
        models: list[str] = []
        for record in candidate_records:
            run_id = str(record.get("run_id", ""))
            model = str(record.get("model", ""))
            if run_id and run_id not in run_ids:
                run_ids.append(run_id)
            if model and model not in models:
                models.append(model)

        strict = (
            self.strict_validation_summary()
            if include_strict_validation
            else None
        )
        if strict is None:
            validation_state: ValidationState = "not_run"
        elif strict.status == "error":
            validation_state = "error"
        elif strict.defect_count > 0:
            validation_state = "draft_with_defects"
        elif strict.error_count == strict.pending_reviewer_field_count:
            validation_state = "clean_draft_awaiting_reviewer_fields"
        else:
            validation_state = "error"

        calibration_state = self._calibration_state()

        return DraftOverview(
            draft_state="prepared_final_draft",
            total_rows=len(rows),
            unique_cases=len({str(row["id"]) for row in rows}),
            primary_rows=len(rows) - secondary_rows,
            secondary_rows=secondary_rows,
            scored_rows=scored_rows,
            scored_unique_cases=len(scored_case_ids),
            pending_reviewer_rows=pending_rows,
            double_annotation_cases=len(audit.get("ids", [])),
            card_pin_provisional_cases=card_pin_cases,
            candidate_run_ids=run_ids,
            candidate_models=models,
            validation_state=validation_state,
            calibration_state=calibration_state,
            calibration_report_path=(
                str(reader.calibration_report_path)
                if calibration_state == "executed_report_available"
                else None
            ),
            strict_validation=strict,
            provenance_notes=self._provenance_notes(),
        )

    def list_rows(
        self,
        offset: int = 0,
        limit: int = 25,
    ) -> tuple[list[DraftRow], int]:
        """Paginated read-only row previews with provenance flags."""
        if not self._reader.draft_exists():
            return [], 0
        rows, _columns = self._reader.read_workbook()
        candidates = self._reader.candidates_by_id()
        previews: list[DraftRow] = []
        for row in rows:
            case_id = str(row["id"])
            record = candidates.get(case_id)
            reference = str(row.get("expected_summary", ""))
            provenance = (
                self._reader.candidate_provenance(record)
                if record is not None
                else None
            )
            annotation_pass: Literal["primary", "secondary"] = (
                "secondary"
                if row.get("annotation_pass") == "secondary"
                else "primary"
            )
            previews.append(
                DraftRow(
                    id=case_id,
                    annotation_pass=annotation_pass,
                    is_double_annotated=(
                        row.get("is_double_annotated", "") == "true"
                    ),
                    source_split=str(row.get("source_split", "")),
                    category=str(row.get("category", "")),
                    source_category=str(row.get("source_category", "")),
                    inclusion_status=str(row.get("inclusion_status", "")),
                    source_input=str(row.get("source_input", "")),
                    expected_summary=reference,
                    actual_summary=str(row.get("actual_summary", "")),
                    provisional_reference=bool(reference.strip()),
                    reference_is_human_validated=False,
                    human_score=str(row.get("human_score", "")),
                    confidence=str(row.get("confidence", "")),
                    notes=str(row.get("notes", "")),
                    annotator_id=str(row.get("annotator_id", "")),
                    annotated_at=str(row.get("annotated_at", "")),
                    candidate=provenance,
                )
            )
        total = len(previews)
        return previews[offset : offset + limit], total

    def strict_validation_summary(self) -> StrictValidationSummary | None:
        """Run strict validation read-only and categorize its report.

        Returns None when the strict validator is unavailable (for
        example, running from a wheel without the scripts tree); the
        overview then reports ``validation_state = 'not_run'``.
        """
        reader = self._reader
        if not reader.draft_exists():
            return None
        validator = self._strict_validator or builtin_report
        paths: ValidatorPathPair = (
            reader.workbook_path,
            reader.manifest_path,
            reader.candidates_path,
            reader.audit_path,
        )
        try:
            report = validator(paths)
        except ImportError:
            return None
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            return StrictValidationSummary(
                validator="scripts_validate_annotation_workbook",
                status="error",
                total_rows=None,
                error_count=0,
                pending_reviewer_field_count=0,
                defect_count=0,
                raw_errors=[f"Validator failed: {exc}"],
                warning_count=0,
            )
        return summarize_strict_report(report)

    def _calibration_state(self) -> CalibrationState:
        """Report calibration execution without auto-running anything."""
        if self._reader.calibration_report_path.is_file():
            return "executed_report_available"
        return "not_executed"

    def _provenance_notes(self) -> list[str]:
        """Expose provisional-content provenance honestly."""
        notes: list[str] = []
        reader = self._reader
        if reader.card_pin_review_path.is_file():
            notes.append(
                "card_pin_recommendations.md contains AI-evaluator "
                "generated provisional decisions; they are advisory and "
                "not approvals. Human-reviewed decisions live in "
                "card_pin_inclusion_review.csv."
            )
        candidates = reader.candidates_by_id()
        if candidates:
            notes.append(
                "All candidate summaries in the draft are AI-generated "
                f"model outputs ({len(candidates)} recorded cases); "
                "they are never human-validated annotations."
            )
        return notes
