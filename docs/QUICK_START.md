# PromptGuard quick start

1. Install Python deps with uv:

   ```bash
   uv sync
   ```

2. Create `.env` from the placeholders-only `.env.example` and fill in
   `NVIDIA_API_KEY` (and, when exposing the service beyond localhost,
   `API_TOKEN` with at least 12 characters). Never commit `.env` and
   never put real keys anywhere else.

3. Sanity check without touching a provider:

   ```bash
   uv run pytest -q
   ```

4. Run the CLI evaluation (quality-gate gated; see README for the
   threshold semantics):

   ```bash
   uv run promptguard --prompt-version v1 --limit 10 --baseline latest `
       --min-category-accuracy 0.7 --min-summary-score 3.0 --max-latency-ms 60000
   ```

   Exit codes: `0` PASS, `1` FAIL (gate), `2` INCONCLUSIVE or
   configuration/provider errors. Reports persist under `runs/`.

5. Serve the HTTP API (thin adapter over the same services):

   ```bash
   uv run promptguard-api            # binds 127.0.0.1:8000
   curl http://127.0.0.1:8000/api/v1/readiness
   ```

   Mutating endpoints (`POST /api/v1/runs`,
   `DELETE /api/v1/run-jobs/{job_id}`) require
   `Authorization: Bearer <API_TOKEN>` whenever `API_TOKEN` is set. The
   server never starts without valid settings: `.env` mistakes surface
   honestly in `/api/v1/readiness` instead of failing mid-run.

6. Dashboard (development):

   ```bash
   cd frontend
   npm install
   npm run dev        # http://localhost:5173, proxies /api to :8000
   ```

   Production build:

   ```bash
   cd frontend
   npm run build      # tsc strict + vite -> dist/
   ```

## API surface (v1)

- `GET  /api/v1/health` — liveness
- `GET  /api/v1/readiness` — settings, dataset, runs-dir, draft state,
  auth mode, issues
- `GET  /api/v1/config` — models, timeouts, limits, and the provider
  pool composition (credentials never leave the server)
- `GET  /api/v1/datasets/{prompt_version}` — case counts by category
- `GET  /api/v1/runs` / `GET /api/v1/runs/{run_id}` /
  `GET /api/v1/runs/{run_id}/export?format=json|text`
- `POST /api/v1/runs` — enqueue an in-process run (async job), bounded
  by `API_MAX_CONCURRENT_RUNS`, deduplicated by request signature
- `GET  /api/v1/run-jobs/{job_id}` — poll to a terminal status:
  `succeeded`, `quality_gate_failed`, `inconclusive`, `error`,
  `cancelled`
- `DELETE /api/v1/run-jobs/{job_id}` — cancel queued/running job
- `GET  /api/v1/baselines` — read-only baseline listing
- `GET  /api/v1/calibration/draft|rows|validation` — read-only
  calibration workspace state

Errors use one envelope:
`{"error": {"code", "message", "request_id"?, "details"?}}`; every
response carries `X-Request-Id`.

## Honest limitations

- One host, in-process run execution; no broker, no clustering, no
  horizontal scale. Run coordination lives in RAM (jobs), durability in
  per-run JSON files under `runs/`.
- Calibration execution is blocked pending the annotator-2 secondary
  pass (20 rows); until then no regression thresholds are calibrated and
  threshold-free runs report INCONCLUSIVE rather than claiming success.
- No rate limiting beyond the request body limit (1 MB), the
  concurrency semaphore, and provider-side backoff; put a proxy with
  rate limits in front for untrusted users.
- Docker packaging was deliberately not added to the codebase in this
  phase (approval pending); the operations guide documents manual
  serving and backup/restore instead.
- The judge and the candidate may be the same model by configuration;
  judge = human agreement is only claimable after calibration, and is
  not claimable today.
