# ADR 0002: Calibration draft inspection is read-only and state-aware

Status: Accepted (Phase 2)
Supersedes: none

## Context

`dataset/calibration/v1/annotation_workbook.csv` is the project owner's
prepared final draft: candidate summaries are mechanically prefilled,
reviewer-owned fields (`expected_summary`, `human_score`, `annotator_id`,
`annotated_at`, `confidence`, `notes`) are pending genuine human input
(0/120 scored at integration time).

The strict validator (`scripts/validate_annotation_workbook.py`) treats a
pending workbook as invalid by design (its errors are, today, exclusively
reviewer-field blanks). This is intentional and must NOT be weakened:
strict validation remains the production gate for approved calibration
runs.

Three different things must not be conflated:
1. **Draft readiness** — mechanical completeness of the prepared draft.
2. **Validation status** — findings from strict validation, categorized
   into reviewer-pending (expected on a draft) and genuine defects.
3. **Calibration execution status** — whether the calibration workflow
   has been run against real reviewer-labelled scores.

## Decision

1. A new read-only draft service exposes overview, paginated row previews
   with per-row provenance, and a categorized strict-validation report.
2. No inspection path writes to any calibration artifact. Byte-stability
   of `sample_manifest.json`, `annotation_workbook.csv`,
   `candidate_outputs.json`, `annotation_queue.json`,
   `double_annotation_ids.json`, and all packet files is asserted by
   tests.
3. Strict production approval remains the untouched script CLI
   (`scripts/validate_annotation_workbook.py`) plus
   `promptguard-calibrate`; the API never auto-runs calibration and any
   state-changing operation requires an explicit, confirmed, logged user
   action.
4. AI-generated content is never presented as human-validated:
   - candidate summaries are labeled "AI-generated candidate output"
     with model + run provenance per row;
   - the `card_pin_recommendations.md` provisional content is listed as
     LLM-drafted material that is explicitly not approval evidence;
   - the golden annotated file produced by `generate_ideal_summaries.py`
     is labeled AI-drafted, distinct from the reviewed golden dataset.
5. Default artifact location is `dataset/calibration/v1`, passed as
   explicit paths through dependency injection (no absolute-path caches);
   the draft service declares `strict_validator` as an injected callable
   defaulting to the in-repo script implementation when running from the
   repository checkout.

## Consequences

- The draft can be inspected and progressively completed without
  defeating the strict gate.
- The strict validator stays the single authority for approval-time
  validation; the package-side analyzer only categorizes findings for
  state display.
- Additional code overlaps mildly with the validator's required-field
  list; justification: different purpose (state awareness vs approval),
  and duplication is locked in sync by tests using the real artifacts.
