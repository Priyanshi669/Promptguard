# Limitations and blockers

Execution date: 2026-10-26
Commits: `bd9ec57` (pushed to origin/main), `435e82c` (pinned evaluation steps, local, unpushed)

## Known limitations (implementation truth, not claims)

1. **LLM-as-judge variability.** The summary quality score comes from a
   live judge model. With an identical eval_config_hash, live reruns can
   produce different scores for the same test case (e.g.
   banking77-007 scored 5.00 in the baseline run and 4.20 in the
   comparison candidate). The regression gate mitigates this by gating
   deltas against a fixed baseline rather than absolute values.
2. **Cost is not captured.** The implementation records input/output
   token counts and latency, but not monetary cost. Cost therefore does
   not appear in any report, per the rule that cost is only reported if
   the implementation actually captures it.
3. **No explicit report schema_version field.** The persisted report
   JSON has no top-level schema/version marker. Structure is implied by
   the pydantic model `EvaluationReport` in
   `src/promptguard/domain/models.py`.
4. **Judge fallback drops the response schema.** When the judge is used
   as a plain model (no native structured-output path), DeepEval's
   `a_generate_with_schema_and_extract` silently drops the schema kwarg
   and relies on JSON parsing plus `trimAndLoadJson`. Consequence: an
   intermittent judge response missing `score` produces
   `KeyError: 'score'`, which fails the whole run (observed twice).
   Token-probability weighting is also silently skipped in this path.
   Both are DeepEval 4.1.10 internals outside our control; retries are
   the current workaround.
5. **Per-request timeout is transport-only.** Default
   `NVIDIA_REQUEST_TIMEOUT_SECONDS` is 10s, which caused a full-run
   "Request timed out" failure under provider slowness (one case took
   18.8s in the baseline run). Raising it to 60s fixed the run without
   affecting eval_config_hash.
6. **Ruff format drift.** `ruff format --check` (not part of the
   documented gate) flags ~43 pre-existing files unrelated to this
   work. No formatting sweep was performed.
7. **Windows symlink skip.**
   `tests/unit/test_path_safety.py::test_resolve_identifier_path_rejects_symlink_escape`
   is skipped because symlink creation is not permitted.

## Blockers

### Calibration: BLOCKED — human labels/review unavailable

- The calibration manifest approval gate (`ensure_manifest_approved()`)
  is intact and refuses the provisional manifest
  (`dataset/calibration/v1/sample_manifest.json`), producing exit 1 and
  no `candidate_outputs.json`. Nothing about this work weakened it.
- `dataset/calibration/v1/card_pin_inclusion_review.csv` is
  intentionally blank in all decision fields
  (decision / primary_intent / rationale / reviewer_id / reviewed_at).
  Existing tests require this until a real human reviewer acts.
- `dataset/calibration/v1/card_pin_recommendations.md` contains
  LLM-generated advisory recommendations only, labelled
  PROVISIONAL / MODEL-GENERATED. They have not been used to approve
  anything and never will be.
- No human annotations have been collected. No human-calibration
  report exists or is claimed.

**Exact next action for each blocker appearing above:**

| Blocker | Exact next action |
|---|---|
| (1) Judge variability | Acceptable for gate design; optionally pin a judge seed/temperature if supported. Requires a deliberate follow-up commit. |
| (2) Cost | Only actionable if NVIDIA returns usage-based cost data; otherwise permanently out of scope. |
| (3) schema_version | Add an explicit `schema_version` field to `EvaluationReport` in a follow-up commit. |
| (4) KeyError 'score' | Short-term: retry. Longer-term: wrap judge parsing in a bounded retry at the metric layer so a single malformed judge response does not abort the whole run. |
| (5) Timeout default | Consider making 60s the documented default if the provider remains slow. Transport-only, no hash impact. |
| (6) Ruff format drift | Separate housekeeping commit with repo-wide formatting only, outside evaluation work. |
| (7) Symlink skip | Windows-only environmental skip. No action required; suppressible via pytest skip annotations. |
| Calibration BLOCKED | A human reviewer must fill `card_pin_inclusion_review.csv` (decision, primary_intent, rationale, reviewer_id, reviewed_at) from their own PIN-and-intent review of the 100-case calibration sample. Until then no one — reviewer, automation, or AI — may fill reviewer_id or reviewed_at. Only after a complete human review: deterministic manifest regeneration, then annotation, then calibration. |
| `435e82c` unpushed | Push to origin/main explicitly once reviewed. |
