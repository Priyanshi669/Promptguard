# Final pre-commit review (2026-10-27)

Review of the complete uncommitted work on top of checkpoint `6483784`
(after Phases 7 and 8: API contract tests, live end-to-end demo, and
project documentation). No commit, push, merge, deploy, reset, or data
revert was performed during this review.

## 1. Review scope

- Tracked modifications: `.env.example`, `README.md`,
  `dataset/calibration/v1/annotation_workbook.csv`, `pyproject.toml`,
  `src/promptguard/config.py`, `src/promptguard/interfaces/cli.py`,
  `tests/unit/test_calibration_artifacts.py`, `uv.lock`, plus (added
  during this review) `.gitignore`.
- Untracked-to-be-added: `docs/` (2 ADRs, audit, operations, quick
  start, demo walkthrough), `frontend/` (15 source files),
  `src/promptguard/application/{calibration_draft_service,wiring}.py`,
  `src/promptguard/domain/calibration_draft.py`,
  `src/promptguard/infrastructure/calibration/draft_reader.py`,
  `src/promptguard/interfaces/api/` (app, runner, routes, schemas,
  launcher),
  `tests/unit/{test_api_routes,test_api_contract,
  test_calibration_draft_service}.py`.
- Not touched: `PromptGuard_Execution_Plan_Checklist.md` (client-provided
  plan in repo root, intentionally left untracked),
  `runs/` (ignored, contains the live-demo run artifacts),
  `reports/evidence-2026-10-26/` (historical evidence).
- Calibration artifacts were read but never modified; byte-stability is
  proven below.

## 2. Exact commands executed and observed results

Backend (repository-documented commands):

| Command | Observed result |
|---|---|
| `uv run pytest` | **530 passed, 1 skipped** (Windows symlink test skip) |
| `uv run ruff check .` | All checks passed |
| `uv run mypy src/` | Success: no issues found in 54 source files |
| `uv lock --check` | Resolved 93 packages; exit 0 |
| `uv run python scripts/validate_annotation_workbook.py` | `status: invalid`, **error_count exactly 100** (20 secondary rows × 5 required fields), **0 non-pending defects**, 6 similarity warnings on primary rows (`2971, 0701, 0042, 1118, 1130, 0440`), exit 1 — the expected pre-annotation-gate state, matching all documentation |
| calibration artifact SHA-256 before/after validator run | **zero changed, zero new files** (validator is read-only) |

Frontend (repository-documented commands):

| Command | Observed result |
|---|---|
| `npm test` | 1 file / **4 tests passed** |
| `npm run build` | `tsc -b` clean; vite production build OK (285 kB js + 3.4 kB css), exit 0 |

Live smoke (verified during this review, then torn down):

- uvicorn (`promptguard-api --port 8000`) + vite dev server;
  through the vite proxy: `/api/v1/health` → `ok`,
  `/` → HTTP 200, SPA route `/runs` → HTTP 200,
  `/api/v1/calibration/draft` → 100/120 scored rows,
  `/api/v1/runs?limit=3` → total=19 persisted runs. Exit 0.

Security scan:

- 183 committable files scanned with patterns for NVIDIA/OpenRouter/AWS
  keys, generic bearer tokens, `api_key/secret/password/token`
  assignments, Slack webhooks, and long hex.
- Result: **no real secrets**. Matches were: uv.lock wheel sha-256
  hashes, calibration/evidence content fingerprints (manifest sha-256s),
  and pre-existing fake test fixture keys (`test-api-key`,
  `sk-secret-test-key-1234` in tracked test files predating this work).
- `.env` is git-ignored (`git check-ignore .env` confirms) and was never
  part of the commit set.
- No unsafe-default patterns (`allow_origins=["*"]`, `verify=False`,
  `debug=True`, `shell=True`, `os.system`) in source.

## 3. Issues found during this review (all fixed)

1. **API contract tests were vacuous (correctness defect).**
   `tests/unit/test_api_contract.py` as previously written extracted
   frontend endpoints with a regex that matched nothing (the client
   builds URLs from an `API_PREFIX` constant), and enumerated backend
   routes via `app.routes`, which — with FastAPI 0.162's lazy
   `_IncludedRouter` mounts — listed only 4 of 12 registered routes.
   Both tests passed while checking nothing. The review treated this as
   a false claim and rebuilt the checks:
   - frontend endpoint extraction now parses every `request<T>(...)`
     call site (template and quoted literals) plus the export URL
     helper (11 real paths);
   - backend inventory now comes from `/api/openapi.json` (all 12
     paths);
   - both directions are compared under a shared `{param}`
     normalization with non-vacuous guards (≥6 client paths, ≥10
     registered paths);
   - parser sensitivity was proven by a scratch-copy sabotage
     experiment (renaming one route changed exactly one path in the
     extracted set and still drifted from the API).
   Result: 2 passed, for real. (During the fix, ruff also flagged
   `re.S` → `re.DOTALL`; fixed.)

2. **`.gitignore` did not exclude `node_modules`.** `git add frontend/`
   would have staged 1,928 dependency files. Added `node_modules/` and
   `*.tsbuildinfo` (a first pass wrote `*.tsbuildinfo/` with a stray
   slash, which also had to be corrected). The frontend commit surface
   is now exactly the 15 intended source files.

3. **`docs/operations.md` cited a nonexistent variable and a wrong
   route.** `DEEVAL_API_KEY` does not exist anywhere in the codebase —
   the judge shares the `NVIDIA_API_KEY` credential via the NVIDIA
   adapter. The endpoint `GET /api/v1/runs/jobs/{job_id}` was written
   instead of the real `/api/v1/run-jobs/{job_id}`. Both corrected.

4. **Stale "remaining" items in the audit Phase 3 status.** The live
   `POST /runs` provider pass and the `promptguard api` operator
   command were already delivered in Phases 6–7; the status block now
   points at those later sections instead of implying open work.

## 4. Cross-document consistency spot-checks

- **API surface:** `docs/QUICK_START.md`, `docs/operations.md`, and the
  QUICK_START/README claims match the OpenAPI inventory exactly (12
  routes; verified bidirectionally by the contract tests).
- **Demo evidence:** every number in `docs/DEMO_WALKTHROUGH.md` was
  re-checked against the persisted artifact
  `runs/423a509b-8cd6-4409-9fa4-edec3e380c5f.json` (run_id, UTC
  timestamp, model, dataset, case_count 3, accuracy 1.0,
  avg score 4.4667, latency 5293.2 ms, tokens 1612/342, classification
  total_cases=3/accuracy 1.0) and the listing (19 runs, new run first).
  The report records `thresholds: null` and
  `regression.status = INCONCLUSIVE` with reason "No thresholds
  configured and no baseline provided" — **INCONCLUSIVE is preserved
  everywhere** and explained honestly; nothing claims PASS/FAIL.
- **Calibration status:** README, audit, QUICK_START, and DEMO
  walkthrough all consistently state 100/100 primary rows human-scored,
  20/20 secondary rows pending, calibration not executed, and the
  strict validator at exit 1 with 100 pending-field errors — exactly
  what the validator itself observed above.
- **`.env.example`** is placeholders-only; the referenced ADR path
  (`docs/architecture/ADR-0003-api-and-runner.md`) exists.

## 5. Files changed during this review

| File | Change |
|---|---|
| `.gitignore` | +4 lines: `node_modules/`, `*.tsbuildinfo`, frontend section comment |
| `tests/unit/test_api_contract.py` | Rewritten: OpenAPI-backed bidirectional contract checks with non-vacuous guards (previously vacuous) |
| `docs/operations.md` | Fixed `DEEVAL_API_KEY` → shared `NVIDIA_API_KEY`; fixed `/api/v1/runs/jobs/{job_id}` → `/api/v1/run-jobs/{job_id}` |
| `docs/PRODUCTION_READINESS_AUDIT.md` | Phase 3 status: marked the two late-delivered items resolved (pointing at Phase 6/7 blocks) |

No production code, calibration data, baselines, or thresholds were
changed during the review. `dataset/calibration/v1/**` hashes verified
unchanged after all review activity.

## 6. Remaining blockers (not addressed here, by design)

1. **Annotator-2 secondary pass** (20 rows × 5 fields in
   `annotation_workbook.csv`) — human work; then the strict validator
   must exit 0.
2. **Calibration execution** (`uv run promptguard-calibrate
   --min-samples 100 …`) only after 1, then wire calibrated thresholds
   into the CI evaluation workflow (`--min-category-accuracy 0.8 …`).
3. **Operator decisions still open (client's call):** `notes` column
   cleanup on scored rows; 87/100 score-5 distribution skew;
   `reference_is_human_validated` hardcoded `False` in
   `calibration_draft_service.py`.
4. **Deferred/approval-pending:** Docker packaging (compose demo was
   intentionally not built); backup provider for failover beyond the
   single NVIDIA credential; GitHub branch protection is a manual
   repository-settings step.
5. The CI workflow's own `uv run promptguard --min-category-accuracy
   0.8 --min-summary-score 3.5 …` gate requires calibrated thresholds —
   same blocker as 2; the limit-3 live API run recorded in the
   walkthrough served as this review's live-provider evidence instead.

## 7. Recommendation

**Ready to commit.** The tracked diff matches its documented intent,
the new surface (API, runner, calibration workspace, frontend,
hardening, docs) is test-covered and live-verified, no secrets or
dependency directories can enter the commit, calibration artifacts are
byte-stable, and the demo evidence is recorded honestly (INCONCLUSIVE
preserved). Suggested commit plan: a single feature commit (or the
split: backend API + tests, frontend, docs) made only after the client
explicitly authorizes; `PromptGuard_Execution_Plan_Checklist.md`
should be either committed deliberately or excluded from staging.
