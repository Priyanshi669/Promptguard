# PromptGuard Production-Readiness Audit

Status date: 2026-10-27. Audit revision: `c4977f2` + local uncommitted changes
at audit time. Scope: native capabilities of the repository before the
production-readiness work stream. This document is updated as each phase of
the implementation plan completes.

## 1. Current architecture

Layered Python package (`src/promptguard/`, uv build, Python 3.11,
src-layout):

- **Domain** — pure, I/O-free decision logic:
  - `models.py`: `GoldenTestCase`, `LLMOutput/LLMResponse`,
    `EvaluationResult`, `EvaluationReport` with aggressive
    self-consistency validation (`aggregate_issues`, `evidence_issues`).
  - `classification.py`: per-class precision/recall/F1/support, macro and
    weighted aggregates, confusion matrix.
  - `statistics.py`: seeded percentile bootstrap (`BootstrapConfig`
    defaults seed 20261026, 2000 resamples, 95% level), small-sample
    threshold 30.
  - `paired.py`: exact McNemar + paired bootstrap aligned by case ID;
    explicitly uninformative with zero discordant pairs; informational
    only (never gates).
  - `regression.py`, `thresholds.py`, `enums.py`: gate decision objects;
    `PASS | FAIL | INCONCLUSIVE`.
  - `calibration.py`: `CalibrationDataset`, per-item judge comparison,
    `CalibrationThresholds`, `JudgeCalibrationReport`.
  - `normalization.py`: exact→alias→passthrough category normalization.

- **Application** — orchestration:
  - `run_service.py`: end-to-end pipeline (evaluate → report → baseline
    load (ID or `latest`) → compatibility validation → comparison +
    paired analysis → regression gate → persist → format → notify).
  - `evaluation_service.py`: candidate LLM → normalize → match → judge
    score (sequential per case).
  - `calibration_service.py`: judge-vs-human agreement (MAE, RMSE,
    Pearson, Spearman, exact/within-one agreement, mean bias) with
    threshold violations.
  - `contracts.py`: ports for run repository, formatter, notifier, LLM.

- **Infrastructure**:
  - `persistence/`: `JsonEvaluationRunRepository` — one self-contained
    pretty-printed JSON report per run (`runs/<run-id>.json`), corrupt/skip
    diagnostics in `list_runs`, path-traversal-guarded IDs.
  - `dataset/`, `prompts/`: JSON golden dataset + prompt YAML loaders.
  - `llm/`: `NVIDIAClient` (candidate) + failure pipeline; provider-pool
    building (`PROVIDER_POOL`), per-provider rate limiting, transient
    retries (3, exponential 1–8s), failover on rate-limit/auth errors,
    actionable secret-redacting error descriptions (`errors.py`).
  - `evaluation/`: DeepEval GEval summary judge (`nvidia_judge.py`),
    versioned rubric (`rubric.py`, v1, sha-pinned),
    non-secret evaluator fingerprinting (`evaluator_config.py`).
  - `notifications/`: Slack notifier (webhook, 10s timeout) or noop.
  - `reporting/`: `TextReportFormatter` (console).
  - `path_safety.py`: identifier/path-traversal guards.

- **Interfaces**: CLI only —
  - `promptguard` (evaluation, exit 0 PASS / 1 FAIL / 2 INCONCLUSIVE or
    unrecoverable error; `--prompt-version`, `--baseline latest|id`,
    `--runs-dir`, `--limit`, absolute/drop/pct gate thresholds).
  - `promptguard-calibrate` (judge calibration, same exit-code scheme,
    thresholds: min-samples 20, MAE ≤ 0.75, Pearson ≥ 0.6,
    within-one ≥ 0.8, |bias| ≤ 0.5).

- **Tooling/scripts**: mock OpenAI-compatible provider (dev-only),
  demo runner, calibration sampling/packet/workbook builders, output
  generator, workbook validator, golden-dataset builder.

- **CI**: single workflow `evaluation.yml` — `test` job (pytest, ruff,
  mypy) + `evaluate` quality-gate job (requires secrets). Branch
  protection required-check enforcement is NOT verified yet.

- **A Dockerfile exists** (python:3.11-slim, uv sync --frozen, non-root
  `appuser`, CMD = the CLI). No compose file, no web/API target.
- **No HTTP API and no frontend exist.** Node v24 / npm 11 are available
  in the development environment for a TypeScript frontend.

## 2. Existing capabilities (working, tested)

- Deterministic evaluation pipeline with quality gates, exit codes, and
  baseline comparison (embedding-safe `latest` resolution).
- Multi-class classification evidence, bootstrap CIs, paired baseline
  statistics — persisted in the report JSON, integrity-checked on load.
- Report persistence with per-run files, corrupt-run quarantine
  (`skipped_runs`), and eval-config fingerprinting
  (`eval_config_hash` excludes thresholds by design, so a gate-rule change
  does not invalidate baseline comparability).
- Provider pool failover with per-provider rate limiting and bounded
  retries; credential redaction in error paths (`describe_provider_error`).
- Judge calibration CLI with rubric-hash binding, deterministic report.
- Calibration sampling/drafting toolchain with provenance: manifest
  (100 cases, seed 20261026), candidate outputs (100 records with run
  provenance), reviewer workbook (120 rows, `actual_summary` mechanically
  prefilled by case ID), validator, and annotation packets.
- Test baseline at audit time: **488 passed, 1 skipped**
  (symlink-permission skip), Ruff clean, mypy clean across 43 source
  files, `uv lock` resolved. No test in the default suite uses the
  network or real credentials.

## 3. Missing capabilities (gaps this work stream addresses)

1. **No HTTP API** — evaluation, reports, calibration evidence, and run
   history are reachable only via CLI or in-process import.
2. **No frontend** — no dashboard, no report visualization, no
   calibration workspace.
3. **No run-history surfacing** — `list_runs()`/`get()` exist in the
   repository layer but no CLI/API verb exposes them; no pagination.
4. **No draft-workflow for calibration** — the validator treats a
   pending-annotation workbook as 600 errors; there is no formal
   distinction between draft readiness (mechanical completeness),
   validation status, and calibration execution status. The AI-assisted
   provisional provenance (`card_pin_recommendations.md`,
   `generate_ideal_summaries.py` output) is documented but not surfaced
   programmatically in any status view.
5. **Concurrency/pagination**: evaluation is strictly sequential
   (correct but slow); `list_runs` loads every file eagerly.
6. **Background execution**: a long evaluation must be cancellable,
   observable, and not double-submitted through an API.
7.Observability: no structured/logging story (print statements in CLI);
   no request IDs.
8. **API security posture**: none yet (CLI trusts local user); API must
   add CORS, request-size limits, and protected mutation endpoints.
9. Deployment docs/demo: Dockerfile exists for CLI; no compose/dev
   orchestration, no frontend build/deploy, no documented demo using the
   mock provider as a *labeled* fake source for the API demo path.

## 4. Risks and dependencies

| Risk | Severity | Mitigation in plan |
|---|---|---|
| A **real NVIDIA API key sits in the local `.env`** (gitignored, never committed; `.dockerignore` excludes `.env*`; a test asserts that). | High (local hygiene) | Flag for rotation if ever shared. Keep keys server-side only; never echo values into UI/logs; audit `git log` for historical leakage before any public share. |
| `get_settings()` is `@lru_cache` memoized — env changes invisible after first use. | Low | API layer initializes settings once per process; document test isolation. |
| `EvaluationReport.aggregate_issues` uses 1e-9 float tolerances when re-deriving aggregates on load — bit-rounding differences could flag legitimate persisted old reports as INCONCLUSIVE evidence. | Medium | Phase 3/6: do not weaken; verify round-trip with a property test and if needed tolerance bounded by report schema granularity, WITH regression tests and documented rationale. |
| `--limit N` smoke runs persist into `runs/` and may become `latest` baseline for later full runs. | Medium | Document run-selection guidance; optionally expose dataset/model in run listing so baselines are choosable rather than implicit. |
| Eager `list_runs()` loads every run file. | Low at current scale (18 runs) | Phase 6 pagination in API; keep repository contract unchanged for CLI. |
| DeepEval version drift pins fingerprints (`deepeval_version()` memoized). | Low | Documented in evaluator config; part of release process, unchanged. |
| Mock provider could be mistaken for a real provider. | Medium | Demo mode must be labeled; provider identity always surfaced in every view (`judge_provider`, model, pool). |
| Sequential evaluation latency (~3.5–8 s/case for the NVIDIA provider). | Low | 50-case runs complete well inside `EVALUATION_TIMEOUT_SECONDS=900`. The mock provider path is fast. |
| Zero-cost ambiguity: `total_cost_usd` is None when provider does not report cost. | Low | UI must display "not reported" rather than synthetic numbers. |

## 5. Test baseline

- `uv run pytest -q` → 488 passed, 1 skipped (symlink creation not
  permitted on this Windows checkout; documented, environment-only).
- `uv run ruff check src tests scripts` → clean.
- `uv run mypy src` → clean (43 files).
- `uv lock --check` → resolved, no drift.
- `git status` → clean except one intentionally untracked local planning
  checklist (`PromptGuard_Execution_Plan_Checklist.md`), preserved as-is.

Git checkpoints:
- `6483784` — frozen PromptGuard 2.0 statistical implementation (do not
  modify or amend; not part of this work stream's commits).
- `c4977f2` — calibration workbook mechanical-prefill fix (newest commit
  before this audit).

## 6. Calibration draft state (as a project input)

- `dataset/calibration/v1/annotation_workbook.csv` is the project
  owner's prepared final draft: 120 rows (100 primary + 20 secondary
  audit), `actual_summary` prefilled mechanically from
  `candidate_outputs.json`. **Annotator-1 batch integrated
  (2026-10-27T05:06:36Z):** every primary row now carries
  human-authored `expected_summary`, `human_score` (5×87, 4×9, 3×4),
  `confidence` (high 88 / medium 11 / low 1), `annotator_id=1`,
  `annotated_at` — verified to merge with byte-identical pipeline
  columns (source_input/actual_summary match the draft and candidates
  exactly). The 20 secondary rows remain blank pending the independent
  second annotator (100 reviewer-pending errors; strict validator
  status: `invalid` by design until that pass lands). Six
  cross-similarity warnings flag references that closely resemble the
  candidate text and need a closer look before calibration.
- Provenance artifacts that identify AI-assisted provisional content:
  `card_pin_recommendations.md` (LLM-evaluator generated, explicitly not
  approved), `card_pin_inclusion_review.csv` (human reviewer 1
  decisions), `generate_ideal_summaries.py` writes
  `golden_dataset.annotated.json` (AI-drafted golden reference summaries
  — separate from the reviewed golden dataset file).
- The validator (`validate_annotation_workbook.py`) is strict by design
  and reports the honest pending state (5 reviewer fields per unscored
  row) — intended behavior that this work stream must NOT weaken.
  It is surfaced as a *state-aware draft report* via the API.

## 7. Phased implementation plan

Phase 1 — Audit (this document). ✔

Phase 2 — Calibration-draft integration: read-only draft service
(state, preview, row inspection, draft-aware validation report), explicit
user-controlled actions only; tests prove zero mutation of calibration
artifacts by inspection paths; AI-provisional provenance surfaced.

Phase 3 — Backend HTTP API (`/api/v1`, FastAPI + uvicorn, typed
schemas): ✔ implemented offline-tested; covers health/readiness
(structured checks, honest auth-mode warning), secret-free config +
dataset composition, paginated run listing/detail/export (json|text),
run creation through an in-process runner (ADR-0003: bounded
concurrency, request-signature dedupe, cancellation, terminal statuses
succeeded/quality_gate_failed/inconclusive/error), baselines
(read-only listing; no management endpoints exist), calibration draft
overview/rows/validation (strictly read-only per ADR-0002), canonical
error envelope with request IDs, body-size limit, CORS (localhost:5173
default), optional bearer-token auth for mutations. 28 API unit tests;
full baseline now 527 passed / 1 skipped (symlink test skips on
Windows); ruff + mypy (53 files) + uv lock clean. Run with
`uv run uvicorn promptguard.interfaces.api.app:create_app --factory`;
live-verified health/readiness boot. (Both items that were open at the
time of this phase — a live provider pass for the POST /runs success
path, and a `promptguard api` operator command — were later resolved;
see the Phase 6 and Phase 7 status blocks below.)

Phase 4 — Frontend shell (Vite + React + TypeScript), typed API client,
navigation, dashboard with real data and honest empty states.

Phase 5 — Evaluation run flow (form → confirm → status), report detail
(classification, confusion matrix, CIs, paired stats, gates, per-case),
baseline comparison, calibration workspace (preview/filter/validate;
explicit confirmations; draft status separate from calibration
execution).

Phase 6 — Production hardening: startup config validation, secret
redaction audit, rate/request-size limits, graceful shutdown, structured
logging with request/run IDs, backup/restore documentation for `runs/`
and calibration artifacts, compose-based local demo, frontend build.

Status (2026-10-27): graceful shutdown wired via app lifespan
(`RunManager.shutdown`, 10s drain, regression-tested); constant-time
token comparison (`secrets.compare_digest`); body-size limit (1MB) and
concurrency cap active; structured logs with request ids (`api.request
…`) and run ids (`run.started/finished …`); secret redaction verified by
tests; `uv run promptguard-api` console script added (uvicorn factory
server, live-verified); `docs/operations.md` documents serving, run
lifecycle, backup/restore, and logging redaction guarantees; frontend
production build passing (tsc + vite). Compose-based demo not built —
deferred pending approval for adding Docker packaging.

Phase 7 — Integration tests (API↔frontend contract), full regression
suite, production frontend build, end-to-end mock-provider demo that is
visibly labeled.

Status (2026-10-27): complete, with the client-approved real-provider
demo (stronger than a mock demo and explicitly labeled). Added
`tests/unit/test_api_contract.py`: the frontend `client.ts` endpoint
set and the backend route inventory are extracted mechanically and run
in both directions (every frontend endpoint registered; no undocumented
API routes appear silently). Frontend production build passes
(tsc strict + vite, 285 kB js bundle); frontend vitest suite 4 passed;
backend regression 528 passed / 1 skipped (Windows symlink skip), ruff
and mypy clean, `uv lock --check` clean. Live end-to-end demo (golden
run `423a509b-8cd6-4409-9fa4-edec3e380c5f`, job
`20261027071310-2373585b`): POST /runs → queued → running →
inconclusive in ~40s for 3 real cases; persisted report shows category
accuracy 1.0, summary score 4.47/5, latency ≈5.3 s/case, tokens
1,612/342, full classification evidence; INCONCLUSIVE is the honest,
documented status while regression thresholds await calibration.
Walkthrough: `docs/DEMO_WALKTHROUGH.md`.

Phase 8 — Documentation: quick start, `.env.example` (placeholders only),
demo walkthrough, deployment guide, honest security
statements/limitations, final assessment.

Status (2026-10-27): `docs/QUICK_START.md` (installation, CLI smoke,
API surface, dashboard dev/prod build, honest limitations);
`docs/DEMO_WALKTHROUGH.md` (live labeled evidence above);
`docs/operations.md` (serving, run lifecycle, backup/restore, log
redaction guarantees) doubles as the deployment guide for the
single-host target; `.env.example` remains placeholders-only; README
calibration-status paragraph updated to the true workbook state
(100/100 primary scored, 20 secondary pending). Final assessment below.

## 8. Acceptance criteria

1. Existing CLI contracts, gate semantics, exit codes, and report schema
   remain intact; all pre-existing tests keep passing (only additive
   changes except documented fixes with regression tests).
2. Calibration artifacts (manifest, workbook, candidate outputs,
   validator) are byte-identical after all inspection flows unless an
   explicitly authorized, logged, confirmed write occurs — proven by
   tests.
3. The API serves every capability that exists in the repository and
   never invents results for those that do not; capabilities that require
   credentials degrade to clearly labeled mock/unavailable states.
4. No key, token, or secret value appears in code, UI, logs, reports, or
   fingerprints.
5. The demo runs from a clean checkout with documented commands, works
   with or without real provider credentials, and any simulated data is
   visibly labeled.
6. Full recorded test evidence: pytest, ruff, mypy, lock check, frontend
   unit tests, production frontend build.
7. No unauthorized git operations; every commit attributable and
   reviewed.

## 9. Final assessment (2026-10-27)

All eight phases of this plan are complete or explicitly deferred with
reasons. Evidence in this file:

- Backend: 528 passed / 1 skipped (Windows symlink test), ruff clean,
  mypy clean (54 files), `uv lock --check` clean.
- Frontend: vitest 4 passed, tsc strict + vite build passing, dev proxy
  smoke against a live API passed (health, calibration draft 100/120,
  config secret-free), API↔frontend contract mechanically enforced by
  tests.
- Hardening: startup settings validation surfaced via readiness,
  bearer-token auth with constant-time comparison, 1 MB request-body
  limit, concurrency semaphore, request/run id structured logging,
  verified secret redaction, graceful shutdown drain regression-tested.
- Live capability proof: real NVIDIA-provider run through the complete
  API stack (`423a509b-…`), honestly INCONCLUSIVE pre-calibration,
  full evidence recorded in `docs/DEMO_WALKTHROUGH.md`.
- Documentation: QUICK_START, operations guide with backup/restore,
  ADRs, demo walkthrough, honest limitations throughout.

Deferral: the compose-based demo was not built (Docker packaging not
approved for this phase); manual serving commands are documented in
`docs/operations.md`.

Remaining open items before production calibration (ordering matters):

1. Annotator 2 completes the 20 secondary rows in
   `dataset/calibration/v1/annotation_workbook.csv`.
2. `scripts/validate_annotation_workbook.py` must exit 0 (today: exit 1
   with exactly 100 pending-field errors, 0 defects).
3. Calibrate: `uv run promptguard-calibrate --min-samples 100 …` per
   README, then wire the calibrated thresholds into the CI evaluation
   workflow.
4. Open operator decisions recorded earlier in this file (notes-column
   cleanup on scored rows, score-5 distribution skew, the
   `reference_is_human_validated` hardcode in
   `calibration_draft_service.py`) remain the client's call.
