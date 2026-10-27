# Judge calibration dataset preparation

This directory contains the preparation materials for the first real
summary-judge calibration. It deliberately contains **no human reference
summaries and no human scores**. The manifest selects source cases only;
labels must be written and reviewed by people.

The calibration gate is intentionally separate from the golden evaluation
set. Do not start Level 2.2 multi-dimensional scoring until the first
calibration report has been reviewed.

## Files

| File | Purpose |
|---|---|
| `v1/sampling_plan.json` | Population, sample size, deterministic selection rules, annotation controls, and acceptance criteria |
| `v1/sample_manifest.json` | 100 unlabeled source cases selected for annotation |
| `v1/calibration_schema.json` | JSON Schema for the final labelled calibration file |
| `v1/annotation_workbook.csv` | 120 blank reviewer rows: 100 primary plus 20 secondary audit rows |
| `v1/annotation_queue.json` | 100-case queue with audit flags, source inputs, and pending-review status |
| `v1/double_annotation_ids.json` | Provisional 20-case audit subset and manifest hash |
| `v1/manifest_validation.json` | Structural validation snapshot and semantic blocker |
| `v1/card_pin_coverage_review.md` | Coverage finding and human inclusion criteria |
| `v1/card_pin_inclusion_review.csv` | Blank provenance rows for the two provisional card_pin cases |
| `scripts/build_calibration_sample.py` | Reproducible manifest builder |
| `scripts/build_annotation_package.py` | Reproducible blank workbook and queue builder |

The final labelled dataset and calibration report do not exist yet.
Structural validation passes for the 100-case manifest, but annotation and
calibration are blocked until the `card_pin` inclusion review is complete.

## Sampling plan

- Population: BANKING77 `test` split, which is separate from the
  train-derived golden evaluation cases.
- Coverage: all 50 categories in `prompts/v1.yaml`, two cases per category,
  100 cases total.
- Length diversity: one short and one long case per category. Short means
  at or below the category median word count; long means above it.
- Selection: deterministic SHA-256 ranking of
  `seed + NUL + category + NUL + normalized input`, with seed
  `20261026`. No manual cherry-picking.
- Exclusions: empty inputs, duplicate normalized inputs, inputs already in
  `dataset/v1/golden_dataset.json`, and cases that fail candidate
  generation.
- Replacements: use the next-ranked eligible case in the same category and
  length band, and record the reason.
- `card_pin` has no exact BANKING77 source label. The current two cases are
  provisional and **not validated**. See
  [`v1/card_pin_coverage_review.md`](v1/card_pin_coverage_review.md) before
  using them.

Regenerate the manifest and reviewer package with:

```powershell
uv run python scripts/build_calibration_sample.py
uv run python scripts/build_annotation_package.py
```

The manifest contains `category` (the prompt taxonomy) and
`source_category` (the original BANKING77 label). Preserve both in the
annotation workbook. The manifest marks `card_pin` entries with
`inclusion_status: pending_human_review`.

## Known coverage limitation: card_pin

BANKING77 does not have a `card_pin` label. The golden `card_pin` case was
normalized from `get_physical_card` and concerns a PIN that has not arrived.
The two proposed source categories are not equivalent:

- `get_physical_card` contains PIN delivery, location, setup, and access
  questions. In the test split, all 40 rows mention a PIN.
- `pin_blocked` contains blocked, reset, and unblock PIN questions. It is
  a different intent and may overlap `change_pin` or `passcode_forgotten`.
- The current manifest selected two `pin_blocked` reset cases. Those are
  not validated as `card_pin` and must be replaced after human review.

Human-reviewed inclusion criteria:

- Include only cases whose primary intent is obtaining, locating,
  receiving, setting up, or viewing a card PIN.
- Exclude cases primarily about physical card delivery, blocked or reset
  PIN, changing PIN, forgotten passcode, or account lock.
- Treat source labels as candidate pools, not exact category evidence.
- Record `reviewer_id`, `reviewed_at`, `decision`, `primary_intent`, and
  `rationale` for every included `card_pin` case in
  [`v1/card_pin_inclusion_review.csv`](v1/card_pin_inclusion_review.csv).

Do not annotate or calibrate the `card_pin` rows until this review is
complete and the manifest is regenerated with reviewed inclusions.

## Annotation workflow

### 1. Freeze the rubric and candidate configuration

Do not edit the judge rubric, prompt, or candidate model during a labelling
round. Record the exact model, temperature, seed, prompt version, rubric
version, and rubric hash. The current rubric is `v1` with SHA-256
`7ef7ca7fdfc4703c97ea933f9c5aa7a6afb0d658f647ce2e4ed2aaac2637b1d5`.

Generate exactly one raw candidate summary per manifest case using the
production prompt and model configuration. Do not manually edit candidates
before scoring. If generation fails, replace the case deterministically as
described in the sampling plan.

### 2. Write the human reference summary

Write the reference from the **source input only**, before looking at the
candidate. This avoids anchoring on the model output.

Requirements:

- One concise sentence.
- Include the customer's primary issue or request.
- Include material details that change the meaning, such as amounts, dates,
  products, actions, blockers, or outcomes.
- Do not invent causes, dates, amounts, actions, or outcomes.
- Do not give advice or a solution.
- Do not copy or paraphrase the candidate as the reference.

### 3. Score the candidate

Compare the candidate against both the source input and the human reference.
Score the summary, not the category classification.

| Score | Meaning |
|---|---|
| 5 | Fully faithful and complete for the source. Captures all important information with no contradiction or invented detail. Concision is appropriate. |
| 4 | Faithful and useful. Captures the important information, with only a minor non-material omission or wording issue. No invented detail. |
| 3 | Mostly faithful. Captures the main issue but misses one important detail or adds a minor unsupported detail. Still useful. |
| 2 | Partially faithful. Misses a key detail or contains a material unsupported or contradictory detail. Limited usefulness. |
| 1 | Unfaithful or incorrect. Contradicts the source, invents central facts, or fails to summarize the issue. |

A **material** detail changes what happened or what the customer wants:
amounts, dates, products, actions, blockers, or outcomes. A **minor** detail
is wording, redundancy, or harmless specificity that does not change the
meaning.

Do not penalize:

- Reasonable paraphrasing.
- Brevity when the source is fully covered.
- Writing style or tone.
- Omission of the category label; this rubric scores the summary only.

Do penalize:

- Contradictions or invented facts.
- Omission of the primary issue.
- Wrong amounts, dates, products, actions, or outcomes.
- Advice presented as if it were a fact from the source.

### 4. Handle edge cases consistently

- Empty, truncated, or non-summary output: score `1`.
- Ambiguous source: use only what is explicit; do not infer. A candidate
  that invents a plausible cause or resolution should be penalized.
- Multiple issues: the reference should include the primary issue and any
  material secondary issue. A candidate missing a material secondary issue
  should normally score no higher than `3`.
- Typos or informal language: summarize intent; do not score grammar.
- Candidate adds helpful advice: advice is not automatically a
  contradiction, but unsupported facts or actions in the advice must be
  penalized.
- Reference and candidate use different wording: judge meaning, not lexical
  overlap.

## Double annotation and adjudication

- Independently double-annotate 20 of the 100 cases.
- Select audit cases by ranking IDs with
  `SHA-256(seed + NUL + "audit" + NUL + id)` and taking the first 20.
- Annotators must not see each other's scores before submitting.
- Adjudicate every disagreement. Use a third annotator or consensus for a
  disagreement greater than one point or any material disagreement.
- Only the final adjudicated score enters the calibration file.
- Record exact and within-one annotation agreement in the review notes. If
  exact agreement is below `0.70`, pause and recalibrate the annotators
  before continuing.

The reviewer package is already expanded to 120 rows: one `primary`
row for every case and a `secondary` row for each of the 20 audit cases.
The builder pre-populates `actual_summary` with the raw production
candidate summary, copied mechanically from `candidate_outputs.json` by
case ID; do not hand-edit it. Reviewers fill `annotator_id`,
`annotated_at`, `expected_summary`, and `human_score`. Do not copy another
reviewer's score into the secondary row. The adjudicated score is written
only to the final calibration file.

## Final calibration file

The final file must match `v1/calibration_schema.json`. Use the manifest ID
as the example `id`.

```json
{
  "metadata": {
    "version": "<calibration dataset version>",
    "rubric_version": "v1",
    "rubric_sha256": "<current summary-quality rubric hash>"
  },
  "examples": [
    {
      "id": "<manifest id>",
      "expected_summary": "<human reference summary>",
      "actual_summary": "<raw candidate summary>",
      "human_score": "<adjudicated integer from 1 to 5>"
    }
  ]
}
```

Rules:

- IDs must be non-empty and unique.
- Every example needs a human-reviewed score on the same 1-5 scale as the
  judge.
- Do not include provisional, model-generated, or inferred human scores.
- Do not add examples outside the manifest without updating the sampling
  plan and manifest first.
- If the rubric changes, bump the rubric version and re-label; do not reuse
  labels from a different rubric.
- Keep the source input and workbook for audit, but do not add them to the
  calibration JSON unless the schema is deliberately extended.

## Calibration run

After labels are reviewed:

```powershell
uv run promptguard-calibrate `
  --file dataset/calibration/v1/judge_calibration.json `
  --min-samples 100 `
  --output dataset/calibration/v1/calibration_report.json
```

Review the per-example items and every gate violation. A failed calibration
is evidence to investigate, not a reason to relax thresholds on the same
data. If the judge or rubric changes, rerun calibration and document the
change.

## Review gate

Do not start Level 2.2 multi-dimensional scoring until:

1. The 100-case calibration file has final adjudicated human scores.
2. The calibration report has been reviewed, including per-example errors
   and bias.
3. Any `card_pin` source-mapping decision has been reviewed.
4. The calibration result is recorded and the judge/rubric configuration is
   frozen for the next step.
