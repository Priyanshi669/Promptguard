# End-to-end demo walkthrough (LIVE provider run, 2026-10-27)

This walkthrough was performed against the real NVIDIA provider using
the key from `.env` (client-approved). It drives one evaluation run
through the complete HTTP stack: FastAPI → RunManager →
EvaluationRunService → NVIDIA candidate + GEval judge → persisted JSON
report → API read-back. Nothing below is mocked.

## Steps

1. **Serve** — `uv run promptguard-api --port 8000` (uvicorn factory).
2. **Readiness** — `GET /api/v1/readiness` returned ready-up checks
   (settings, dataset v1, runs dir writable, calibration draft present).
   `auth_mode=disabled` because `API_TOKEN` was not set for the local
   demo; localhost-only binding.
3. **Submit** — `POST /api/v1/runs` with `{"prompt_version":"v1",
   "limit":3}`:

   - `job_id = 20261027071310-2373585b`, `status = queued`.

4. **Poll** — `GET /api/v1/run-jobs/{job_id}` every 5 s:

   - `12:43:16 -> running`
   - `12:43:56 -> inconclusive (report=423a509b-8cd6-4409-9fa4-edec3e380c5f)`

   Submission-to-terminal was about 40 s for 3 cases.

5. **Report read-back** — `GET /api/v1/runs/{run_id}`:

   - run_id `423a509b-8cd6-4409-9fa4-edec3e380c5f`, timestamp
     `2026-10-27T07:13:54.307150Z`
   - model `nvidia/nemotron-3-ultra-550b-a55b`, dataset `golden-v1`
   - `case_count = 3`, `category_accuracy = 1.0`
   - `average_summary_score = 4.47/5`
   - `average_latency_ms = 5293.2`
   - tokens: 1,612 in / 342 out
   - classification: total_cases=3, accuracy 1.0, macro/weighted
     precision/recall/F1 all 1.0, full per-class + confusion data
     persisted in the report JSON.

6. **Listing** — `GET /api/v1/runs` showed 19 persisted runs with the
   new run first.

## Why the status is INCONCLUSIVE (and why that is the honest result)

The gate needs calibrated regression thresholds. Calibration execution
is deliberately blocked until the annotator-2 secondary pass (20 rows)
completes and the strict validator exits 0. With no thresholds in the
request and no calibration-backed policy, the runner exercises the full
pipeline and records complete evidence — then refuses to call the run
PASS or FAIL. A threshold-free run can never satisfy a quality gate;
this is the same invariant the CLI documents. Once thresholds are
supplied (calibrated or explicit), this exact request map resolves to
`succeeded` (PASS) or `quality_gate_failed` (FAIL).

## What this proves

- The API-driven run path works end to end against a live provider,
  including job lifecycle, deadline handling, dedupe eligibility, and
  report persistence/read-back.
- The report schema matches the CLI-persisted format (shared
  `EvaluationRunService`), including token totals and multi-class
  classification evidence.
- Status mapping is honest: evidence without a calibrated gate never
  claims a release decision.

Repeat the demo after the annotator-2 pass and calibration execution;
the same request body will resolve to a calibrated PASS/FAIL decision.
