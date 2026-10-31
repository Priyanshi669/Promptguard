# Release evidence bundle, 2026-10-26

Generated on branch `origin` at commit `cdf2666` plus the release-candidate
changes (provider pool with failover, client-side request throttling,
timeout default 30s, actionable provider error mapping, and their tests).
Bundle contents:

| File | What it evidences |
|---|---|
| `test-results.txt` | Fresh pytest (484 passed, 1 skipped), ruff, mypy (43 files), and `uv lock --check` on the final code |
| `final-verification.txt` | Release-candidate verification: full suite, lint, types, lockfile, and whitespace checks tied to the exact revision |
| `RELEASE_READINESS.md` | Release-readiness checklist: diff review, secret scan, calibration/failover/CI-enforcement status, verdict |
| `verified-demo-transcript.txt` | Single-provider demo: passing evaluation with baseline deltas (exit 0) and a deliberately failing gate (exit 1) |
| `failover-demo-output.txt` | Live failover run: NVIDIA returned 429 mid-run, the pool failed over to a second provider, and the run still completed and persisted (run `3106b20e`) |
| `classification-metrics-demo.txt` | Live report with per-class precision/recall/F1/support, macro/weighted aggregates, and a persisted confusion matrix (run `cccbab1f`; synthetic mock answers, gate FAIL exit 1) |
| `example-report-with-classification.json` | Persisted JSON for the classification demo run |
| `example-report-with-confidence-intervals.json` | Persisted JSON with seeded percentile-bootstrap intervals for accuracy and mean summary score (run `a0c8b547`, seed 20261026) |
| `paired-analysis-demo.txt` | Live paired baseline analysis: per-case alignment, uninformative McNemar on zero discordant cases, and a paired summary-score interval (mock provider; gate FAIL exit 1) |
| `example-report-with-paired-analysis.json` | Persisted JSON for the paired-analysis demo run |
| `failover-mock-provider.log` | Arrival log of the failover run at the backup provider, including request spacing |
| `example-report.json` | A real persisted passing run (`edacc7cd`) with metrics, token totals, `eval_config_hash`, comparison, regression ledger, and drift |
| `provider-error-handling.txt` | Live-mapped 429, connection, and timeout failures with actionable, secret-free messages (exit 2) |
| `limitations.md` | What the evidence does and does not prove |

Reproduce the demo with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
```

## What is proven

- The evaluation pipeline runs end-to-end against the configured provider
  and persists complete, fingerprinted run reports.
- The quality gate blocks on absolute thresholds with a distinct exit
  code (0 = PASS, 1 = FAIL, 2 = INCONCLUSIVE/error).
- Provider failures are classified into actionable, secret-free messages
  and never collide with a gate decision.
- Request throttling spaces request starts (`NVIDIA_MIN_REQUEST_INTERVAL_
  SECONDS`, default 1.0s) for both the candidate client and the judge.
- Provider failover works live end-to-end: after the primary's quota was
  exhausted mid-run, the pool moved traffic to the configured backup and
  the evaluation completed instead of aborting (see
  `failover-demo-output.txt` and `failover-mock-provider.log`).
- Multi-class classification metrics (precision, recall, F1, support,
  confusion matrix) are computed from per-case evidence, verified against
  hand-calculated values, persisted, and rendered; they are descriptive
  only and do not affect gate decisions.
- Seeded percentile-bootstrap confidence intervals (accuracy and mean
  summary score) are reproducible, validated for finite ordered bounds,
  and persisted; they quantify sampling uncertainty under the resampling
  scheme only and never change gate decisions.
- Paired baseline analysis (exact McNemar over discordant pairs plus a
  paired bootstrap interval for the mean summary-score change) is
  verified against hand calculations, refuses misaligned baselines with
  an explicit reason, and is informational only: release gates, statuses,
  and exit codes are unchanged.
- Pool composition (names and models, never keys) is fingerprinted, so
  runs with a failover pool are not silently compared against
  single-provider baselines.
- Configuration: timeout default 30s (1-60 allowed), env override wins,
  out-of-range values rejected (unit-tested).

## What is not proven (limitations)

1. **Scale.** Demos evaluate 10 of 50 golden cases. The 80% category
   accuracy and ~4.6/5 summary score describe this 10-case subset only;
   they are not proven production performance.
2. **Human calibration.** The judge has NOT been calibrated against human
   labels. The annotation workbook is blank (0/120 rows scored); the
   manifest is approved and candidate outputs exist, but until reviewers
   complete the workbook per `dataset/calibration/ANNOTATION_PROTOCOL.md`,
   no human-alignment claim is supportable.
3. **Provider quota window.** The NVIDIA integration tier throttles after
   roughly 5-6 ten-case evaluations per hour. The failover run completed
   only because a backup provider absorbed the traffic; its scores and
   categories were synthetic (a labeled test double,
   `scripts/mock_provider.py`) and carry no quality meaning. A
   quality-bearing fallback requires a real second provider key in
   `PROVIDER_POOL`.
4. **CI/CD.** Checks are documented and pass locally; wiring them as
   required status checks on a protected branch is not yet evidenced.
5. **Cost capture.** The NVIDIA endpoint does not report per-request
   cost; `cost_usd` is captured only when the provider attaches one
   (unit-tested, null for this provider).
6. **Single-model failover semantics.** Fallback providers may serve a
   different model; that composition is fingerprinted, but per-request
   judge identity is not separately recorded in persisted runs.
