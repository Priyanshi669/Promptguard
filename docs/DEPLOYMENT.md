# PromptGuard deployment plan (Drift-Alert + Dashboard release)

Scope: ship the Slack drift-alert feature, the Stitch dashboard, and the
API threshold-mapping fix. Current state on this branch: backend tests
549 passed / 1 skipped, ruff + mypy clean, live provider runs verified
(pairing PASS gate and TVD drift evidence). Everything in this plan is a
repeat of verified steps with deployment-level failure modes added.

## 0. Ground rules

- The quality gate stays deliberately honest: a run with a baseline but
  no drop thresholds is INCONCLUSIVE, never PASS. Deployment must not
  camouflage that state; dashboards and alerts surface it verbatim.
- Secrets stay out of images, logs, the repo, and API responses. The
  webhook URL and provider keys live only in environment settings.
- `runs/` artifacts are append-only evidence; deployments never rewrite
  persisted reports.

## 1. Build and environment prerequisites

| Requirement | Notes |
| --- | --- |
| python >= 3.11 + `uv` | pinned by `pyproject.toml` |
| node >= 18 + npm | frontend build (`npm run build`) |
| `.env` (never committed) | `NVIDIA_API_KEY`, `NVIDIA_MODEL` |
| `.env` Slack block | `SLACK_WEBHOOK_URL` optional; deploy alert-only environments first |
| `master_key` / identities dir | required so provider keys stay file-backed, not in `.env` |
| writable `runs/` directory | persists every report; mounted volume in containers |

## 2. Verification pipeline (pre-deploy gates run green before shipping)

```powershell
uv run pytest                 # 549 passed, 1 skipped (symlink skip on Windows)
uv run ruff check src tests scripts
uv run mypy src               # 55 files clean
cd frontend && npm test && npm run build
```

The bundle stats seen last build: 324 kB JS, 18.46 kB CSS. Use that as
the sanity envelope; a large regression signals an accidental dependency.

## 3. Deployment tiers

### Tier 1 — evaluation-only (no Slack)

- Config: leave `SLACK_WEBHOOK_URL` empty. `slack_notifications_enabled`
  unused, status reported as `unconfigured`; `NoopNotifier` is wired.
- Prove: `GET /api/v1/config` shows `slack_notifications="unconfigured"`.
- Risk: none of the drift code exists consumer-side, drift evidence is
  still persisted on the report for future alerting.

### Tier 2 — Slack dry-run channel

- Config: add `SLACK_WEBHOOK_URL` pointing at a private `#promptguard-drift-canary`
  channel, `SLACK_NOTIFICATIONS_ENABLED=true`, `SLACK_REPORT_URL_PREFIX`
  set to the internal dashboard URL.
- Prove: run a pair of API runs against the same baseline with
  `max_category_drift` low (e.g. 0.05) and one artificially oscillating
  category so the TVD crosses the threshold. Exactly one message must
  land in the channel; the run JSON gains
  `slack_alert: {status: "delivered", ...}`.
- Prove dedupe: re-submitting the same run (duplicate detection) must
  not double-send. The persisted `slack_alert.status == "delivered"` for
  the same baseline must suppress resend (`drift_alerts.py` replay rule).

### Tier 3 — production Slack alarm channel

- Same as Tier 2 but the webhook targets the release-critical channel.
- Keep `SLACK_MAX_ATTEMPTS <= 5` so a provider-side outage cannot block
  the run pipeline more than ~30 s on retries + backoff.
- The generic "run completed" message is suppressed when a drift alert
  fires (single message per run). Document this in the runbook so
  on-call is not alarmed by silent generic traffic.

## 4. Runtime failure modes and their handling

| Failure | Handling in code | Operator action |
| --- | --- | --- |
| Webhook 4xx (bad URL, revoked token) | `SlackDeliveryError` raised immediately, no retry | rotate webhook |
| Webhook 429/5xx/network | bounded retries w/ backoff, then error | watch `notification_error` in outcome |
| Provider outage mid-run | job fails with error status; alert attempt only happens after a report exists | rerun job |
| Stale/missing baseline | `incompatibility_reasons` recorded; drift/comparison unset; no alert | supply a valid baseline |
| Corrupt run JSON in runs dir | repository lists it under `skipped_runs`; `latest` refuses to operate (`get_latest` returns None) | quarantine the file |
| Frontend unreachable | backend unaffected; alerts contain report link that 404s | fix vite/static hosting |

`notification_error` (outcome) is a sanitized string — it never contains
the webhook URL. Log filters must not attempt to "helpfully" redact it
further; the sanitization contract is enforced by tests.

## 5. Rollout sequence

1. Build image / stage repo on the target host; run `uv sync --locked`.
2. Seed identities dir (provider keys) + `.env` with host URLs.
3. Tier 1 smoke: `uv run promptguard --prompt-version v1 --limit 1`.
4. Tier 2 dry-run drift alert pair; verify Slack message + JSON record.
5. Point the production webhook then move to Tier 3 only after (4).
6. Serve the dashboard behind the same origin (`/api` reverse proxy
   allowed by CORS config); verify `readiness` returns `ready: true`.
7. Record deployment in the runbook with the run IDs used in the smoke
   tests as evidence.

## 6. Rollback

- Code: revert the deployment commit; `runs/*.json` are forward
  compatible (the new `slack_alert` field is optional, so reports written
  by the new version remain readable by the previous one).
- Slack: set `SLACK_NOTIFICATIONS_ENABLED=false` (keeps the webhook but
  stops sending) before rotating the webhook if malicious traffic is
  suspected. Rotate both when needed.
- No data migration exists or is needed: the only new persisted artifact
  is the optional `slack_alert` record on new runs.

## 7. Post-deploy observability

- Watch `notification_error` on `EvaluationRunOutcome` (CLI stdout, and
  the API run list once dashboards expose it).
- Alert fatigue guard: category drift fires per completed run, not per
  minute; the dedupe key (candidate run, baseline) prevents spam from
  re-processed runs.
- Periodic check that the repo's persisted run files remain loadable
  (`GET /api/v1/readiness` runs-dir check covers writability only; run
  `GET /api/v1/runs?limit=1` to catch file-level corruption).
