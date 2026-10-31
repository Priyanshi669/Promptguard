# Annotation workbook prefill fix

Date: 2026-10-27. Parent commit: `6483784` (frozen checkpoint, unchanged).

## Finding

The freshly built `annotation_workbook.csv` could never pass its own
validator: the builder blanked `actual_summary` while the validator
requires it to equal the recorded candidate summary verbatim. The before
state recorded 720 validator errors (120 rows x 6), including 120
misleading "actual_summary was hand-edited" flags on a workbook nobody
had touched.

Evidence: `annotation-validator-before-prefill.json`.

## Fix

`scripts/build_annotation_package.py` now pre-fills `actual_summary`
mechanically from `candidate_outputs.json`, matched by sample ID (never
by position), rejecting failed or missing candidate records loudly.
`expected_summary` stays blank by design: per the annotation protocol it
is a human-written reference summary produced from the source input
alone, before looking at the candidate. The calibration cases are drawn
from the BANKING77 `test` split and are deliberately disjoint from
`dataset/v1/golden_dataset.json`, so there is no golden reference text to
copy; the golden dataset join was verified to match 0 of 100
`source_input`s and must not be used.

`annotation_packet` sheet semantics (already copying candidate text) are
unchanged; the workbook builder now matches them.

After regeneration: all 120 rows carry the exact recorded candidate text,
all reviewer-owned fields (`expected_summary`, `human_score`,
`annotator_id`, `annotated_at`, `confidence`, `notes`) remain blank.

## After

Evidence: `annotation-validator-after-prefill.json`. Status remains
`invalid` with exactly 600 errors: 5 genuinely reviewer-owned blank
fields per row across 120 rows. 0 hand-edit flags remain. This is the
correct pending-annotation state; the gate stays pending until real
reviewer annotations exist.

## Tests

- `tests/unit/test_calibration_artifacts.py` now asserts the workbook
  copies candidate text by ID and leaves reviewer fields blank.
- `tests/unit/test_annotation_tooling.py` gains builder tests proving
  exact-copy, ID-based alignment on a scrambled queue, blank reviewer
  fields, and loud rejection of failed/missing candidates.
- Full suite at this fix: 488 passed, 1 skipped; ruff, mypy (43 files),
  and lock check clean.
