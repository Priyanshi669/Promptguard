# Deployment log — PromptGuard drift-alert + dashboard release

Deployment performed per [docs/DEPLOYMENT.md](DEPLOYMENT.md) on
2026-10-31 (local host, branch `origin`, PR #1).

## Pre-deploy gates (§2)

| Gate | Result |
| --- | --- |
| `uv run pytest` | 549 passed, 1 skipped (Windows symlink skip) |
| `uv run ruff check src tests scripts` | All checks passed |
| `uv run mypy src` | no issues in 55 source files |
| `cd frontend && npm test` | 4 passed |
| `cd frontend && npm run build` | OK (vite build ~9 s) |

## Prerequisites (§1)

- `.env`: `NVIDIA_API_KEY` set (redacted from log), `NVIDIA_MODEL`
  `nvidia/nemotron-3-ultra-550b-a55b`.
- Slack: deliberately unconfigured — Tier 1 target.
- `runs/` directory present and writable.

## Tier 1 — evaluation-only (§3, §5.3–5.4)

- Backend started: `uv run promptguard-api --port 8000`,
  `GET /api/v1/health` -> `{"status":"ok"}`.
- **Tier 1 proof:** `GET /api/v1/config` ->
  `"slack_notifications":"unconfigured"`.
- CLI smoke: `uv run promptguard --prompt-version v1 --limit 1` ->
  report `fc8e1ee5-2f3c-457f-95ef-38b9938381fc`, accuracy 100%, summary
  score 5.0/5, exit code 2 (INCONCLUSIVE — no thresholds/no baseline
  requested; the honest gate outcome, not a defect).
- Dashboard artifact: `npm run preview -- --port 4173` serving the
  production bundle; headless DOM check confirms Overview renders from
  live API data (Quality-Gate Verdict panel, run IDs listed).

## Tier 2 / Tier 3 — Slack dry-run and production (§3, §5.5)

Not executed: requires a Slack Incoming Webhook URL supplied by an
operator. Per the deployment plan the rollout must pause between Tier 1
and Tier 2 until the webhook is configured in `.env` and the dry-run
proof (exactly one message per drift event, persisted
`slack_alert.status == "delivered"`, no resend on duplicate) is
performed against a private channel.

## Tier 2 dry-run verification (local webhook stub, 2026-10-31)

Real Slack was NOT contacted (no webhook URL exists in this environment;
`slack_notifications` remains `"unconfigured"`). Instead, the delivery
lifecycle was proven against a genuine HTTP listener bound to a random
loopback port (`tests/integration/test_slack_webhook_lifecycle.py`,
5 tests):

| Scenario | Result |
| --- | --- |
| Wiring + status | enabled/unconfigured/disabled map to `SlackNotifier`/`NoopNotifier` and the honest status enum |
| Drift pair end to end | baseline (4x `billing`) then candidate (2x `billing`, 2x `technical`, TVD 0.5 > 0.2) -> exactly 2 HTTP POSTs: generic completion + one drift alert starting `PromptGuard drift alert` with candidate/baseline run IDs; persisted `slack_alert.status="delivered"`, `error=None` |
| Replay dedupe | re-deciding the persisted delivered event produces no message decision and no extra POST; a genuinely new drift event against the same baseline still fires (dedupe is per event, not per baseline) |
| Transient 5xx | 500, 500, then 200 -> delivery succeeds on attempt 3 within the bounded-retry policy |
| Permanent 4xx | 400 -> immediate sanitized `SlackDeliveryError`, no retries, webhook URL absent from the error |

This is the full notification lifecycle minus only the real webhook URL,
which remains an operator-supplied secret. Tier 2/3 live-Slack proof
stays blocked on that secret; `.env.example` documents the exact
variables (`SLACK_WEBHOOK_URL`, `SLACK_NOTIFICATIONS_ENABLED`,
`SLACK_TIMEOUT_SECONDS`, `SLACK_MAX_ATTEMPTS`,
`SLACK_REPORT_URL_PREFIX`).

## Observations recorded during deployment (§4)

- **CORRECTED (2026-10-31):** `GET /api/v1/config` previously hardcoded
  `credentials_in_identities=true`, an unverifiable claim: the provider
  key actually loads from `.env` (`Settings.nvidia_api_key`,
  `env_file=".env"`), and no credential-bearing identities store exists
  in the codebase (annotation identities are unrelated). The field was
  removed from the response schema, the API endpoint, the dashboard
  Settings panel, and the frontend types. Verified after the change:
  `/api/v1/config` contains no `credentials_in_identities` key, no
  credential material (guard test
  `test_config_has_no_credentials` extended to pin the key's removal),
  and still reports `slack_notifications="unconfigured"`. The
  no-secrets invariant itself remains enforced by that test
  (`nvidia_api_key`, `NVIDIA_KEY`, `api_key` never appear in the body).
- The API process must be restarted to pick up source changes; the
  earlier in-process restart pattern (`cmd /c uv run promptguard-api`)
  is the documented local procedure.

## Run IDs kept as evidence

| Run | Purpose |
| --- | --- |
| `423a509b-8cd6-4409-9fa4-edec3e380c5f` | drift-only baseline workflow (API) |
| `97f58c8d-6dd9-4540-83b0-253108e92dcc` | INCONCLUSIVE with baseline + drop thresholds present (pre-fix demo) |
| `bd32aa66-0310-4d5f-ba0d-cc4e2d6f6f1b` | second pre-fix INCONCLUSIVE (bug reproduction) |
| `00a0cae5-8d64-4dbd-a815-979ab0c0e297` | PASS with `max_summary_score_drop=0.5` + `max_category_drift=0.05` (post-fix) |
| `fc8e1ee5-2f3c-457f-95ef-38b9938381fc` | Tier 1 deployment smoke (CLI, no thresholds) |
