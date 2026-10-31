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

## Observations recorded during deployment (§4)

- `GET /api/v1/config` hardcodes `credentials_in_identities=true`
  (`app.py:430`) rather than reporting the actual settings source.
  Not deployment-blocking; flagged for follow-up honesty alignment.
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
