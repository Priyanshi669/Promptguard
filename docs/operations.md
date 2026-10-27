# PromptGuard operations guide

Practical operational notes for running PromptGuard. Configuration
reference and the full audit narrative live elsewhere; this page covers
day-to-day serving, backups, restore, and logging behavior.

## Serving the API

```bash
uv run promptguard-api            # 127.0.0.1:8000 via uvicorn (factory)
uv run promptguard-api --port 8001
```

Equivalent long form:

```bash
uv run uvicorn promptguard.interfaces.api.app:create_app --factory
```

`create_app()` validates `.env`-provided settings at startup. If settings
are broken the server still boots with `/readiness` reporting the exact
issue and ready=false; mutating endpoints stay guarded by the
`Authorization: Bearer <API_TOKEN>` check whenever `API_TOKEN` is set
(`secrets.compare_digest`). Put a reverse TLS proxy in front for any
non-localhost exposure; the API itself binds to localhost only.

## Run lifecycle

- `POST /api/v1/runs` enqueues an in-process asyncio job (bounded by
  `API_MAX_CONCURRENT_RUNS`, deduplicated by request signature while
  queued/running). Poll `/api/v1/run-jobs/{job_id}` until a terminal
  status: `succeeded`, `quality_gate_failed`, `inconclusive`,
  `error`, `cancelled`.
- Deadline: `EVALUATION_TIMEOUT_SECONDS` (max 1800).
- Shutdown: uvicorn receives SIGINT/SIGTERM → app lifespan awaits
  `RunManager.shutdown()` for up to 10s, cancelling in-flight jobs
  cleanly (dedupe fingerprints are released).

## Backups

Everything that matters is JSON/CSV on disk:

- `dataset/` — Golden dataset v1 and the calibration workspace
  (`dataset/calibration/v1/annotation_workbook.csv`,
  `double_annotation_ids.json`, reference artifacts). Version these files
  in git; changes are deliberate workflow steps, not runtime mutations.
- `runs/` — one JSON file per finished evaluation run, including the
  full evaluation report, classification metrics, bootstrap CIs, and
  quality-gate evidence. Baselines are just run IDs; compare across
  historical runs freely.

To back up a host snapshot:

```bash
tar -czf promptguard-backup-$(date -u +%Y%m%dT%H%M%SZ).tar.gz \
    dataset runs
```

## Restore

1. Reinstall: `uv sync` (Python env) and `npm install` inside
   `frontend/` for the dashboard build.
2. Restore `dataset/` and `runs/` from the tarball into the project
   root, preserving file names (run IDs must stay unique, so merges
   from two hosts should prefer distinct prefixes).
3. Copy `.env` (never in the tarball) with `NVIDIA_API_KEY`
   (candidate and judge share this provider credential) and — if
   exposing beyond localhost — `API_TOKEN`.
4. Smoke check: `uv run promptguard-api` then
   `curl http://127.0.0.1:8000/api/v1/readiness` → `ready=true`,
   `auth_mode=token`.

## Logs

Structured single-line entries on stdout:

- `api.request method=… path=… request_id=… elapsed_ms=…` — one per
  HTTP request; the id is also returned as `X-Request-Id` (and echoed
  inside error envelopes).
- `run.started job_id=…` / `run.finished job_id=… status=…
  error_code=… elapsed_ms=…` — one pair per API-triggered evaluation
  run.

No secrets are logged: the API surface token check never echoes the
configured token, `/api/v1/config` reports model names/URLs only, and
validation error details are sanitized (pydantic `ctx` stripped).
