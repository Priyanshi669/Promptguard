# PromptGuard cloud deployment plan

Status: PREPARATION ONLY — nothing in this plan has been deployed
publicly. Every command and path below was verified against the current
repository (PR #1 branch `origin`, commits through `5078750`). Items
that are verified facts are marked **[VERIFIED]**; architecture choices
that are proposals are marked **[PROPOSAL]**.

The local deployment evidence (Tier 1, `docs/DEPLOYMENT_LOG.md`) remains
the only completed tier. Slack remains explicitly unconfigured until an
operator supplies a real webhook (`SLACK_WEBHOOK_URL`).

## 1. Repository facts (inspection results)

### 1.1 Backend entry point and production startup — **[VERIFIED]**

- `[project.scripts]` (`pyproject.toml:24-27`) defines:
  - `promptguard` → `promptguard.interfaces.cli:run`
  - `promptguard-calibrate` → `promptguard.interfaces.calibration_cli:run`
  - `promptguard-api` → `promptguard.interfaces.api_launcher:run`
- `api_launcher.py` calls `uvicorn.run(
  "promptguard.interfaces.api.app:create_app", host=--host, port=--port,
  factory=True)`. Defaults: **host `127.0.0.1`, port `8000`**.
- Production implication: the cloud process must pass `--host 0.0.0.0`
  (or the container's private interface). The factory import path means
  any process manager (uvicorn, gunicorn+uvicorn workers,
  systemd/containers) can serve `promptguard.interfaces.api.app:create_app`
  directly. Equivalent manual command:

  ```powershell
  uvicorn promptguard.interfaces.api.app:create_app --factory `
      --host 0.0.0.0 --port 8000
  ```

- Concurrency: `API_MAX_CONCURRENT_RUNS` (default 1) bounds simultaneous
  evaluations via `RunManager`; uvicorn itself can serve other requests
  concurrently. One worker process per container is the safe unit **[VERIFIED
  single-process in-memory job manager; horizontal scaling requires a
  shared runs storage and dedupe-per-process awareness — see §3]**.

### 1.2 Frontend build and API base URL — **[VERIFIED]**

- `frontend/package.json` scripts: `dev` (vite, port 5173), `build`
  (`tsc -b && vite build` → `dist/`, sourcemaps on), `preview`
  (`vite preview`), `test` (vitest).
- `frontend/src/api/client.ts:27`: `const API_PREFIX = "/api/v1"` — all
  requests are **relative** URLs. There is no absolute API base URL
  configuration in the client.
- Dev CORS proxy: `vite.config.ts` proxies `/api` → `127.0.0.1:8000`
  (dev port 5173). `vite preview` currently inherits `server.proxy`
  (observed serving API data on :4173), but **a static production host
  (nginx/Caddy/CDN) that merely serves `dist/` will break the app unless
  it also reverse-proxies `/api/v1` to the backend** — required either
  same-origin proxying or a client change (none exists today).

### 1.3 CORS configuration — **[VERIFIED]**

- `app.py:create_app` builds `allow_origins` from
  `settings.api_cors_origins` (comma-separated string, default
  `"http://localhost:5173"`, `config.py:54`).
- Allowed: methods `GET, POST, DELETE`; headers `Authorization,
  Content-Type`; exposes `X-Request-Id`; credentials disabled.
- Production change: set `API_CORS_ORIGINS` to the exact scheme+host[] of
  the dashboard (e.g. `https://promptguard.example.com`). If using the
  recommended same-origin reverse proxy (§3), CORS is effectively
  bypassed but the setting should still match for defense in depth.
- Auth: `Authorization: Bearer <API_TOKEN>` gates **mutating**
  endpoints (`authorize_mutation`, `app.py:161-173`); the frontend posts
  it when a token provider is registered (`client.ts:71-83`).

### 1.4 Report persistence and filesystem assumptions — **[VERIFIED]**

- `app.py:192` and `app.py:279-282`: runs directory is
  `PROJECT_ROOT / "runs"` — **hardcoded**, not configurable. Reports are
  one JSON file per run
  (`JsonEvaluationRunRepository`, `repositories.py`), written on
  evaluation completion and **re-saved after a notification attempt**
  (drift alert delivery outcome).
- Readiness performs a write probe (`runs/.readiness-probe`,
  `app.py:360-369`) — the volume must be writable at runtime, not just
  at build.
- Also baked into the working directory:
  - `dataset/v1/golden_dataset.json` (evaluation dataset, read-only)
  - `dataset/calibration/v1/…` (read-only calibration draft input)
  - `runs/` (the only mutable data directory)
- Deployment implication: the container image can ship code+dataset;
  `runs/` must be a persistent volume so reports survive restarts. The
  codebase has no S3/blob support — cloud storage must present a POSIX
  filesystem (container volume, EFS/portainer-style network FS) **[VERIFIED
  repository code; blob backends are future work]**.

### 1.5 Environment variables — secrets vs. non-secret — **[VERIFIED]** (`config.py:16-76`, `.env.example`)

| Variable | Class | Production default |
| --- | --- | --- |
| `NVIDIA_API_KEY` | **SECRET** (required, min_length 1) | via secret manager |
| `NVIDIA_MODEL` | secret-adjacent required | e.g. `nvidia/nemotron-3-ultra-550b-a55b` |
| `NVIDIA_BASE_URL` | non-secret | NVIDIA integrated endpoint |
| `NVIDIA_REQUEST_TIMEOUT_SECONDS` | non-secret | 10–30 |
| `EVALUATION_TIMEOUT_SECONDS` | non-secret | 900 |
| `NVIDIA_TEMPERATURE`, `NVIDIA_SEED` | non-secret optional | unset |
| `API_TOKEN` | **SECRET** (optional locally, REQUIRED for any exposed deployment, ≥12 chars) | secret manager |
| `API_CORS_ORIGINS` | non-secret | dashboard origin |
| `API_MAX_CONCURRENT_RUNS` | non-secret | 1 (provider rate limits) |
| `SLACK_WEBHOOK_URL` | **SECRET** — stays **EMPTY/unconfigured** until operator supplies it (plan §3 of DEPLOYMENT.md) | unset |
| `SLACK_NOTIFICATIONS_ENABLED` | non-secret | `true` (no-op while URL unset) |
| `SLACK_TIMEOUT_SECONDS`, `SLACK_MAX_ATTEMPTS` | non-secret | 10 / 3 |
| `SLACK_REPORT_URL_PREFIX` | non-secret | set to the public dashboard URL **after** it exists |
| `DATABASE_URL` (in .env.example) | — | **not consumed by the API code today** **[VERIFIED unused]** |

Honesty guidance: `provider_pool` exists as a list-of-specs setting;
leave unset (empty) unless actual pooled keys are configured.

### 1.6 Health and readiness endpoints — **[VERIFIED]**

- `GET /api/v1/health` → `{"status":"ok"}` (public, unauthenticated,
  `app.py:340-341`). Suitable for container liveness probes.
- `GET /api/v1/readiness` → `{ready, checks: {settings, dataset_v1,
  runs_dir_writable, calibration_draft}, auth_mode, issues[]}` (public,
  `app.py:345-387`). Use as the **readiness probe**; `auth_mode`
  reports `"disabled"` when `API_TOKEN` is unset — a public deployment
  must show `"token"`, never `"disabled"`.

### 1.7 CI/CD capability — **[VERIFIED: none]**

- There are **no GitHub Actions workflows** (no `.github/` directory).
  The only pre-deploy gates are the manual commands in
  `docs/DEPLOYMENT.md` §2 (pytest 554 passed/1 skipped, ruff, mypy,
  frontend build). CI automation is a gap to close before or during
  cloud rollout if desired; it is not required for the first deployment.

## 2. Proposed hosting architecture **[PROPOSAL]**

Reference architecture (single-container, same-origin):

```
                    Internet (HTTPS)
                          │
                    Reverse proxy / LB
                    (TLS termination, gzip)
            ┌─────────────┴─────────────┐
            │ /            /api/v1/     │
            ▼                           ▼
      Static files               FastAPI (uvicorn,
      frontend/dist              factory=True, 1 worker)
      (nginx container           promptguard-api --host 0.0.0.0
       or CDN)                   API_TOKEN required
            └───────────┬───────────────┘
                        ▼
              Persistent volume: /app/runs
              (one JSON report per run; files may be updated in place
              after notification attempts — NOT append-only)
                        │
                        ▼
        NVIDIA integrate endpoint (outbound HTTPS)
        + optional Slack webhook (blocked until operator-supplied)
```

Why this shape:

- The client is relative-`/api/v1`, so same-origin proxying needs
  **zero client/CORS changes** — the largest risk reducer.
- **A single container simplifies topology** (one process manager, one
  volume, one proxy mapping). It does **not** by itself solve secret
  management: `NVIDIA_API_KEY` and `API_TOKEN` still require a proper
  secret source (secret manager / SOPS-style injection), never baked
  into the image regardless of how many containers run.
- Vertical-rail scaling (uvicorn workers > 1) is possible only after
  jobs/runs storage is shared; do not scale-out before that (§3).

Alternatives (acceptable, more work):

- Split static CDN + API behind different subdomains — requires exact
  `API_CORS_ORIGINS` and absolute API base URL support in the client
  (client change, currently absent).
- Managed Platform-as-a-Service (e.g. App Engine/Fly.io/Railway-style)
  with container support and a volume — viable, same rules apply
  (writable volume, 0.0.0.0 bind, `API_TOKEN`).

## 3. Build and startup commands (verified against the repository)

Build (all verified locally this session):

```powershell
# Backend (uv)
uv sync --locked
uv run pytest            # 554 passed, 1 skipped
uv run ruff check .
uv run mypy src

# Frontend
cd frontend
npm ci
npm run test             # 4 passed
npm run build            # emits dist/ (index.html + assets/*)
```

Runtime commands:

```powershell
uvicorn promptguard.interfaces.api.app:create_app --factory --host 0.0.0.0 --port 8000
# equivalent documented command: uv run promptguard-api --host 0.0.0.0 --port 8000
```

Reverse-proxy mapping (same-origin proposal):

| Path | Target | Notes |
| --- | --- | --- |
| `/` … (everything not `/api`) | `frontend/dist/` static | SPA; ensure 404→index.html fallback |
| `/api/v1/*` (incl. requests) | `http://127.0.0.1:8000` | preserve `X-Request-Id` response header |

**[PROPOSAL]** Uvicorn native support for serving dist is not part of
the codebase — a proxy/static layer is required, not optional.

### 3.1 Phase 2B implemented: local production stack (same-origin) **[IMPLEMENTED 2026-10-31]**

Files: `frontend/Dockerfile` (multi-stage: `node:22-slim` build →
`nginx:1.27-alpine` serve), `frontend/nginx.conf`,
`frontend/.dockerignore`, `docker-compose.yaml`. Backend container is
unchanged (`Dockerfile`, commit `dbc0f3e`).

- `web` (nginx) publishes **8080:80** — the only public port. Static
  SPA + `/api/v1/` reverse proxy to `api:8000` over the private
  `promptguard-internal` network. `proxy_pass` without a URI part
  forwards the original path and query unchanged.
- `api` has **no** published port; runs as UID 10001; named volume
  `runs` mounted at `/app/runs`.
- SPA fallback `try_files $uri $uri/ /index.html` covers
  `/runs/:runId`, `/calibration`, `/settings` refreshes; Vite assets in
  `/assets/…` are content-hashed → immutable 365d cache.
- `nginx client_max_body_size 2m` aligned above the backend's
  `MAX_BODY_BYTES = 1 MB` (**[V]** app.py:65).
- Health: `api` uses the image `HEALTHCHECK` (`/api/v1/health`,
  liveness); `web` uses a `wget --spider` check on its own static root;
  compose `depends_on: api: service_healthy` orders startup.
- Vite's `server.proxy` is dev/preview-only and inert in production —
  nginx fully replaces it **[V]** (vite.config.ts has no `preview`
  producer configuration; production serving is nginx-defined).

Run it locally (requires Docker Engine):

```powershell
docker compose -f docker-compose.yaml build
docker compose -f docker-compose.yaml up -d
# dashboard: http://localhost:8080   api: private only
```

Safety notes **[VERIFIED behavior]**:

- `docker compose config` prints resolved environment values from
  `.env`, including `NVIDIA_API_KEY`. Never paste or share that output.
- Secrets enter only at runtime via `env_file: .env`; neither image
  contains them (`.dockerignore` files exclude `.env*` at both levels).
- `compose config` surfaced one stray variable in the local `.env`
  (`EVALUATION_MODEL=gpt-4o`); the backend settings loader ignores
  unknown keys (`extra="ignore"`, config.py). Clean up `.env` manually
  if desired.
- Docker Engine was unavailable on this machine, so `build`/`up` are
  NOT claim-run here; the on-daemon smoke checklist below still applies
  before any external exposure.

## 4. Persistent storage strategy **[PROPOSAL grounded in §1.4]**

| Item | Requirement | Recommendation |
| --- | --- | --- |
| `runs/` | read-write, durable; one JSON per run, mutated in place after notification attempts (NOT append-only) | dedicated volume (container disk or network FS); snapshot/sync off-box continuously rather than daily-only (a daily snapshot can miss the interim update); deploy NEVER wipes it |
| image `dataset/`, `dataset/calibration/` | read-only, version-controlled in repo | baked into the image; not a volume |
| Calibration draft state | read-only in readiness | no storage action needed |
| No DB required | — | `DATABASE_URL` in `.env.example` is unused by the API; skip managed databases until the code consumes them **[VERIFIED]** |

Volume mount must match `PROJECT_ROOT` **[VERIFIED path math]**: the
application resolves the runs directory as
`PROJECT_ROOT = Path(__file__).resolve().parents[3]` from
`src/promptguard/interfaces/cli.py` — i.e. the **repository root**, and
runs live at `<PROJECT_ROOT>/runs` (`app.py:192,280`). If the image
places the repository at `/app` (WORKDIR `/app`, code at `/app/src/…`),
the volume MUST be mounted at **`/app/runs`**. Mounting anywhere else
(e.g. `/data/runs`) has no effect on where reports are written, because
the path is derived from `__file__`, not from the process working
directory. Post-deploy check: the readiness write-probe
(`runs/.readiness-probe`) must be observed inside the mounted volume,
not just on the container's writable layer — otherwise restarts will
lose the (in-place updated) reports.

Scale-out rule: do **not** run multiple API containers on different
volumes for a regulated install (job state is in-process). If scale-out
becomes necessary, first implement shared runs storage + a shared job
store; that is a code change, not a deployment toggle.

## 5. CORS, auth, and security requirements **[VERIFIED constraints]**

1. `API_TOKEN` MUST be set (≥12 chars) in any non-localhost deployment:
   readiness warns and `auth_mode` flips to `"token"`; mutating
   endpoints (POST /runs, POST /cancel, DELETE) require the bearer.
2. `API_CORS_ORIGINS` to exact dashboard origin(s).
3. TLS termination at the proxy; keep uvicorn on the private network.
4. The webhook URL never leaves the process: `/config` returns only the
   `slack_notifications` status enum (contract tests enforce this).
5. `SLACK_WEBHOOK_URL` stays unset at deploy time (Tier 2 later).
6. Google Fonts + Material Symbols are loaded from third-party CDNs by
   `index.html` **[VERIFIED]** — either allowlist at the proxy or
   self-host in a follow-up; blocking them only degrades icons, not
   function.
7. Body size limit middleware exists (`MAX_BODY_BYTES`) — keep the
   proxy's request-size limit aligned (default 10 MB) **[VERIFIED middleware
   registered]**.

## 6. Deployment sequence and rollback **[PROPOSAL]**

1. **Pre-deploy gates** (§3) all green on the release commit.
2. Build the release artifact (container image tagged with the commit
   SHA; image contains code + datasets, no secrets layered in).
3. Provision: volume for `runs/`; secrets injected as env vars
   (`NVIDIA_API_KEY`, `NVIDIA_MODEL`, `API_TOKEN`);
   `API_CORS_ORIGINS` and `SLACK_REPORT_URL_PREFIX` set to the public
   URL; `SLACK_WEBHOOK_URL` left unset.
4. Start a single API container (0.0.0.0:8000 private) + reverse proxy.
5. Smoke-test (§7). If `readiness.ready != true` or `auth_mode !=
   "token"` → stop, fix, re-run.
6. Deploy the dashboard behind the same proxy; `/config` must report
   `slack_notifications="unconfigured"`.
7. Record the deployment (deploy log + smoke run IDs).

Rollback:

- Cross-code: redeploy the previous image tag. Report-schema
  compatibility is **verified-safe in one direction only**: *new code
  reads reports written by older code* — `slack_alert` is optional
  (`models.py:231`, `default=None`), and the full suite plus the live
  session successfully read reports persisted before the feature
  (`423a509b…`, `97f58c8d…`). The reverse direction — *older code
  reading new reports* — is only expected to hold via pydantic's
  default unknown-key tolerance and is NOT test-verified; if a
  rollback must read post-`slack_alert` reports, verify acceptance of
  the `slack_alert`/`drift` fields on the rolled-back build first.
- Rollbacks keep the volume; they never rewrite or delete report files.
- Emergency containment: rotate/disable `API_TOKEN`; the proxy can
  take `/api/v1` offline while the dashboard stays up.
- Slack is a no-op factor in rollback (never configured in Tiers < 2).

## 7. Production smoke-test checklist

Ran against the PUBLIC URL. Each step is a real verification; do not
skip the SLACK_UNCONFIGURED check.

1. `GET https://<public>/api/v1/health` → HTTP 200,
   `{"status":"ok"}`.
2. `GET /api/v1/readiness` → `ready=true`, `checks` all true,
   `auth_mode="token"`, `issues` empty (mutating endpoints closed).
3. `GET /api/v1/config` → dashboard fields, `slack_notifications =
   "unconfigured"`, and NO `credentials_in_identities`, NO key material.
4. Browser: dashboard loads (Overview, Evaluation runs, Settings);
   no CORS or mixed-content errors; network shows same-origin
   `/api/v1/*` calls only.
5. Authenticated `POST /runs` (queue `{"limit":1}`) with a valid
   `Authorization: Bearer <API_TOKEN>` header → `queued` → poll
   `run-jobs/<id>` → terminal state → `GET /runs/<id>` has
   classification + bootstrap data.
6. `GET /runs/<id>/export?format=json` and `?format=text` download.
7. POST /runs without a token → 401/403 (mutating endpoint closed);
   GET /runs without a token still works (read-only surface).
8. Confirm report JSON written to the live `runs/` volume and survives
   a container restart (restart → `GET /runs` still lists it).
9. Confirm **no Slack POST is attempted** (`unconfigured` ⇒ Noop
   notifier) — a drift threshold event must not reach the internet.
10. (later, Tier 2+) with an operator webhook: drift crossing → exactly
    one Slack message; duplicate suppression; persisted `slack_alert`.
