"""Read-only calibration draft service tests (ADR-0002).

The critical guarantee: inspection flows never mutate calibration
artifacts. Byte hashes of every artifact are captured before and after.
"""

import hashlib
from pathlib import Path

import pytest

from promptguard.application.calibration_draft_service import (
    CalibrationDraftService,
    summarize_strict_report,
)
from promptguard.infrastructure.calibration.draft_reader import (
    REVIEWER_FIELDS,
    DraftReader,
)

ROOT = Path(__file__).resolve().parents[2]
CALIBRATION_DIR = ROOT / "dataset" / "calibration" / "v1"

WATCHED_FILES = (
    "sample_manifest.json",
    "annotation_workbook.csv",
    "candidate_outputs.json",
    "annotation_queue.json",
    "double_annotation_ids.json",
    "calibration_schema.json",
    "manifest_validation.json",
    "card_pin_inclusion_review.csv",
    "card_pin_recommendations.md",
    "card_pin_coverage_review.md",
    "sampling_plan.json",
    "generation_failures.json",
    "annotation_packets/packets_metadata.json",
    "annotation_packets/primary_packet.csv",
    "annotation_packets/secondary_packet.csv",
)


def artifact_hashes() -> dict[str, str]:
    """Return SHA-256 for every watched calibration artifact."""
    return {
        name: hashlib.sha256((CALIBRATION_DIR / name).read_bytes()).hexdigest()
        for name in WATCHED_FILES
    }


@pytest.fixture()
def fresh_service() -> CalibrationDraftService:
    """Service wired to the real artifacts, fresh each test."""
    return CalibrationDraftService(CALIBRATION_DIR)


def test_inspection_never_mutates_calibration_artifacts(
    fresh_service: CalibrationDraftService,
) -> None:
    """overview/list_rows/strict validation must be byte-read-only."""
    before = artifact_hashes()

    overview = fresh_service.overview()
    rows, total = fresh_service.list_rows(offset=0, limit=120)
    strict = fresh_service.strict_validation_summary()

    after = artifact_hashes()
    assert before == after
    assert overview.draft_state == "prepared_final_draft"
    assert total == 120
    assert overview.total_rows == 120
    assert len(rows) == 120
    assert strict is not None


def test_draft_overview_states_are_actually_separated(
    fresh_service: CalibrationDraftService,
) -> None:
    """Draft readiness, validation, and execution status stay distinct."""
    overview = fresh_service.overview()

    assert overview.draft_state == "prepared_final_draft"
    assert overview.validation_state == "clean_draft_awaiting_reviewer_fields"
    assert overview.calibration_state == "not_executed"
    assert overview.calibration_report_path is None


def test_overview_counts_match_the_prepared_draft(
    fresh_service: CalibrationDraftService,
) -> None:
    """Counts derive from the real artifacts, not constants."""
    overview = fresh_service.overview(include_strict_validation=False)

    assert overview.total_rows == 120
    assert overview.unique_cases == 100
    assert overview.primary_rows == 100
    assert overview.secondary_rows == 20
    # Annotator 1 batch (2026-10-27) scored every primary row; the 20
    # secondary rows await the independent second annotator. All 100
    # unique cases have a scored primary.
    assert overview.scored_rows == 100
    assert overview.scored_unique_cases == 100
    assert overview.pending_reviewer_rows == 20
    assert overview.double_annotation_cases == 20
    assert overview.candidate_run_ids, "candidate run ids surfaced"
    assert overview.candidate_models, "candidate models surfaced"


def test_overview_reports_provisional_provenance(
    fresh_service: CalibrationDraftService,
) -> None:
    """AI-generated content is labeled, never claimed human-validated."""
    overview = fresh_service.overview()

    assert overview.card_pin_provisional_cases == 2
    joined = " ".join(overview.provenance_notes)
    assert "AI-generated" in joined
    assert "advisory" in joined


def test_strict_validation_categorizes_draft_state(
    fresh_service: CalibrationDraftService,
) -> None:
    """Exactly the reviewer-pending errors are expected; nothing else."""
    strict = fresh_service.strict_validation_summary()

    assert strict is not None
    assert strict.status == "invalid"
    # Outstandings are exactly the 20 still-blank secondary rows
    # (5 required reviewer fields each); 100 primary rows are scored.
    assert strict.error_count == 100
    assert strict.pending_reviewer_field_count == 100
    assert strict.defect_count == 0
    assert strict.defects == []


def test_rows_are_labeled_ai_candidate_and_reviewers_merge_cleanly(
    fresh_service: CalibrationDraftService,
) -> None:
    """AI provenance is recorded; only the primary pass is scored so far."""
    rows, total = fresh_service.list_rows(limit=500)
    reader = DraftReader(CALIBRATION_DIR)
    candidates = reader.candidates_by_id()

    assert total == 120
    by_id = {(row.id, row.annotation_pass): row for row in rows}
    assert len(by_id) == 120
    scored = 0
    for (case_id, annotation_pass), row in by_id.items():
        assert row.actual_summary_is_ai_candidate is True
        assert row.reference_is_human_validated is False
        record = candidates[case_id]
        assert row.actual_summary == str(record.get("actual_summary", ""))
        assert row.candidate is not None
        assert row.candidate.candidate_model == str(record.get("model", ""))
        assert row.candidate.candidate_run_id == str(record.get("run_id", ""))
        if annotation_pass == "primary":
            # Reviewer fields land from the annotator-1 batch.
            assert row.expected_summary.strip(), case_id
            assert row.human_score.strip(), case_id
            assert row.annotator_id.strip(), case_id
            assert row.annotated_at.strip(), case_id
            assert row.confidence.strip(), case_id
            scored += 1
        else:
            for field in REVIEWER_FIELDS:
                assert getattr(row, field) == "", (case_id, field)
    assert scored == 100


def test_double_annotation_rows_stand_out(
    fresh_service: CalibrationDraftService,
) -> None:
    """The 20 audit cases carry double-annotation and secondary marks."""
    rows, _ = fresh_service.list_rows(limit=500)
    audit = DraftReader(CALIBRATION_DIR).load_audit()
    audit_ids = set(audit["ids"])

    secondary = [row for row in rows if row.annotation_pass == "secondary"]
    assert len(secondary) == 20
    assert {row.id for row in secondary} == audit_ids
    assert all(row.is_double_annotated for row in secondary)


def test_missing_draft_reports_honest_empty_state(tmp_path: Path) -> None:
    """Without artifacts, the workspace is empty, never fabricated."""
    service = CalibrationDraftService(tmp_path / "v1")
    overview = service.overview()
    rows, total = service.list_rows()

    assert overview.draft_state == "missing"
    assert overview.validation_state == "not_run"
    assert overview.calibration_state == "not_executed"
    assert total == 0 and rows == []


def test_unavailable_strict_validator_reports_not_run(
    fresh_service: CalibrationDraftService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validator import failure degrades to not_run, not a fake success."""

    def raise_import_error(_paths: object) -> object:
        raise ImportError("validator unavailable")

    service = CalibrationDraftService(
        CALIBRATION_DIR,
        strict_validator=raise_import_error,
    )
    assert service.strict_validation_summary() is None
    assert (
        service.overview().validation_state
        == "not_run"
    )


def test_validator_exception_surfaces_as_error_state(
    fresh_service: CalibrationDraftService,
) -> None:
    """A crashing validator becomes an error summary, never silence."""

    def boom(_paths: object) -> object:
        raise RuntimeError("validator crashed")

    service = CalibrationDraftService(
        CALIBRATION_DIR,
        strict_validator=boom,
    )
    summary = service.strict_validation_summary()
    assert summary is not None
    assert summary.status == "error"
    assert "validator crashed" in summary.raw_errors[0]


def test_defects_categorize_distinctly(
    fresh_service: CalibrationDraftService,
) -> None:
    """Helper test of the summarizer against a shaped fake report."""

    class FakeReport:
        status = "invalid"
        total_rows = 1
        error_count = 2

        def __init__(self) -> None:
            self.errors = [
                (
                    "Row banking77-test-0925/primary: actual_summary was "
                    "hand-edited; it must be the raw recorded candidate "
                    "summary."
                ),
                (
                    "Row banking77-test-0925/primary: required field "
                    "'annotator_id' is blank."
                ),
            ]
            self.warnings: list[str] = []

    summary = summarize_strict_report(FakeReport())  # type: ignore[arg-type]
    assert summary.defect_count == 1
    assert summary.pending_reviewer_field_count == 1
    assert summary.defects[0].field == "actual_summary"
    assert "candidate_outputs.json" in (summary.defects[0].hint or "")
