# ADR 0003: HTTP API technology and execution model

Status: Accepted (Phase 3)

## Context

PromptGuard is a layered CLI application with no HTTP surface. The
production-readiness work stream ships a versioned API (`/api/v1`) and a
TypeScript frontend. Requirements: real re-use of application services,
typed schemas, background evaluation runs with observable status,
minimal moving parts, no invented capabilities.

## Decision

1. **FastAPI + uvicorn** — the repo already standardizes on Pydantic v2
   models and Pydantic-Settings; FastAPI reuses the exact same models,
   keeping one schema language across domain, API, and frontend client.
   Starlette TestClient-based tests run offline.
2. **In-process asyncio task runner with a bounded semaphore** for
   background evaluation (safety default: one concurrent run). No Redis,
   broker, or new database: deployment target is a single host; reports
   are already durably persisted per run (`runs/<run-id>.json`). This is
   the simplest reliable solution compatible with the deployment target.
   Limitations are documented: a process restart loses only the busy-run
   register, not completed reports; scaling out processes is out of scope
   until a broker is justified.
3. **Duplicate-submission protection**: a request-signature fingerprint
   (prompt version, limit, baseline id, threshold set) queued/running
   rejects identical submissions with HTTP 409 while results differ.
4. **Secrets**: provider credentials come only from server-side
   environment/pydantic-settings. The request body can never specify
   provider options beyond model identity exposure. No token is echoed.
5. **Authorization boundary**: optional `API_TOKEN`; when set, mutating
   endpoints (POST /runs, run cancel) require `Authorization: Bearer`.
   When unset, the deployment is explicitly flagged `auth: "disabled"`
   in the readiness payload so it can never silently look protected.
6. **Pagination**: limit/offset query params on run history and
   calibration rows; the underlying repository still returns everything
   at current scale; caps enforced at the route layer.
7. **Report export**: JSON (native) and console-formatted text via the
   existing `TextReportFormatter`; no invented formats.
8. **CORS**: explicit allowlist from `API_CORS_ORIGINS` (default local
   dev Vite origin), never `*` when a token is configured.

## Consequences

- Business logic stays in the existing application services; routes are
  thin translations between HTTP and service calls.
- The API cannot report capabilities the repository lacks (e.g., there
  is no baseline-management operation, so none is exposed).
