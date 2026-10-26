# card_pin coverage review

Status: **blocked pending human inclusion review**

## Question

Do the two proposed BANKING77 source categories, `get_physical_card` and
`pin_blocked`, cover the intended meaning of the PromptGuard `card_pin`
category?

## Evidence

- `prompts/v1.yaml` defines the label `card_pin` but provides no definition
  beyond the display name `Card PIN`.
- The existing golden case `banking77-040` has category `card_pin` and
  source input: "my pin hasn't arrived in the post! How do I cancel it or
  get a new one?" Its note says the category was normalized from a source
  label. The input is in BANKING77's `get_physical_card` category.
- In the BANKING77 test split, `get_physical_card` has 40 rows and all 40
  mention a PIN. The rows concern PIN delivery, location, setup, and access.
- In the same split, `pin_blocked` has 40 rows and 35 mention a PIN. The
  rows concern blocked, reset, and unblock PIN requests.
- `change_pin` and `passcode_forgotten` already exist as separate PromptGuard
  categories, so broad PIN-intent rows risk overlapping those categories.

## Finding

The proposed mapping is not exact.

- `get_physical_card` is the closest source pool for the existing golden
  `card_pin` meaning: receiving, finding, or accessing a card PIN.
- `pin_blocked` describes a different primary intent: a PIN is blocked and
  the customer wants it reset or unblocked.
- The two labels cannot be treated as interchangeable. The current
  manifest selected two `pin_blocked` reset cases:
  - `banking77-test-0553`: "How do I reset my PIN?"
  - `banking77-test-0524`: "I accidentally blocked my PIN. How do I reset
    it?"

Those two cases are **not validated** as `card_pin` under the
delivery/access interpretation and must not be annotated until a human
review decides whether blocked/reset PIN is in scope.

## Human-reviewed inclusion criteria

Use these criteria, not the source label alone:

Include a case only when its primary intent is one of:

- obtaining a card PIN;
- locating or viewing a card PIN;
- receiving or waiting for a card PIN;
- setting up a card PIN; or
- another PIN delivery/access question that does not change the primary
  intent to blocking, reset, change, or account recovery.

Exclude a case when its primary intent is:

- physical card delivery or card arrival without a PIN issue;
- blocked, locked, or unblock PIN;
- resetting a PIN;
- changing a PIN;
- forgotten passcode; or
- account lock or login recovery.

Source labels are candidate pools only. The reviewer must record the final
decision and rationale for each included case.

## Required provenance

Record one row per reviewed case in
`v1/card_pin_inclusion_review.csv` with:

- `id`
- `source_category`
- `source_input`
- `decision` (`include` or `exclude`)
- `primary_intent`
- `rationale`
- `reviewer_id`
- `reviewed_at`

At least one human reviewer must apply the criteria. If the final
`card_pin` interpretation is disputed, use two independent reviewers and
adjudicate before updating the manifest.

## Manifest action

Until the review is complete:

- Keep the two `card_pin` manifest entries marked
  `inclusion_status: pending_human_review`.
- Do not generate `judge_calibration.json`.
- Do not run `promptguard-calibrate`.
- After review, replace excluded cases deterministically from the reviewed
  `card_pin` pool, update the manifest, and re-run structural validation.
