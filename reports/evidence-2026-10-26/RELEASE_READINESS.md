# PromptGuard 2.0 Release-Readiness Checklist

Revision under review: `cdf2666` + uncommitted release-candidate changes
(full inventory in `final-verification.txt`). Status date: 2026-10-26.

## 1. Diff and freeze review

| Check | Result |
| --- | --- |
| `git diff --check` (whitespace/conflict markers) | Clean (exit 0) |
| Tracked dataset changes (`git diff --stat -- dataset/`) | None; golden dataset untouched |
| Secret scan of tracked diff (nvapi-/sk-/ghp_/Bearer patterns) | One match: `sk-secret-test-key-1234` in `tests/unit/test_llm_errors.py` — an obviously fake test constant |
| Secret scan of untracked release files | No matches |
| Credentials in evaluator fingerprint | Absent by construction: pool fingerprint records name+model only (`evaluator_config.py`) |
| Credentials in logs/reports | Error descriptions redact keys (`errors.py`); mock provider key is `mock-key` |
| Evaluator fingerprint stability | Pool composition included only when a pool is configured; single-provider fingerprints unchanged, so old baselines stay comparable |
| Regression-decision tests | Untouched and passing; gates, statuses, and exit codes unchanged through Phases 1–3 |
| Integrity validation | `aggregate_issues` now cross-checks classification accuracy, both interval point estimates, sample count, and paired matched cases |
| Legacy report loading | Old reports load with classification/intervals/paired = None (tested) |
| Report serialization | New blocks round-trip through JSON persistence (tested) |

Files reviewed line-by-line: `classification.py`, `statistics.py`,
`paired.py`, `models.py` (integrity rules), `run_service.py`,
`reporting_service.py`, `client.py`, `retry.py`, `pool.py`, `errors.py`,
`nvidia_judge.py`, `evaluator_config.py`, `cli.py`, `calibration_cli.py`,
`config.py`, `html_renderer.py`, all new/updated tests.

## 2. Final local state (this exact revision)

`final-verification.txt`: 484 passed, 1 skipped; ruff clean; mypy clean
across 43 source files; `uv lock --check` resolved; `git diff --check`
exit 0.

## 3. Human calibration — OPEN (blocking release validation)

- Manifest approved; `annotation_workbook.csv` at 0/120 human-scored rows.
- Protocol (`dataset/calibration/ANNOTATION_PROTOCOL.md`) forbids synthetic
  labels; the gate stays pending until real scores exist.
- When reviewers finish: run the workbook validator, resolve findings,
  execute the calibration workflow, record actual results and limitations.
- Judge calibration status remains pending until that workflow passes.

## 4. Real-provider failover — OPEN (blocked on credentials)

Mock-provider failover is proven live (`failover-demo-output.txt`). Still
required for release validation:

1. A genuine second provider (OpenRouter/Groq/etc.) with real credentials
   in `PROVIDER_POOL`.
2. Available NVIDIA quota for the primary.
3. A controlled run verifying: backup uses a real model; report persists;
   provider identity and fingerprints are correct; the quality gate
   evaluates actual outputs; provider failure and quality failure remain
   distinguishable; no credentials leak into logs, reports, or fingerprints.

## 5. CI enforcement — PARTIALLY VERIFIED

- `.github/workflows/evaluation.yml` exists: pytest + ruff + mypy on every
  push/PR, plus a quality-gate evaluation job (requires `NVIDIA_API_KEY`
  secret).
- Branch protection could not be verified from this environment: no `gh`
  CLI is installed and protection settings are not publicly readable.
  Confirm in GitHub: Settings → Branches → Branch protection rules →
  require status checks (`test`, `Evaluation with Quality Gates`) before
  merging into the default branch.
- Recommended workflow improvements (non-blocking):
  - Add `uv lock --check` as a CI step to enforce lockfile consistency.
  - `NVIDIA_REQUEST_TIMEOUT_SECONDS: "10"` predates the 30s default;
    consider aligning or removing the pin.

## 6. Evidence bundle

All artifacts in this directory were regenerated on the final revision:
test results, verified PASS/FAIL demo, failover demo, classification
metrics demo, confidence-interval report, paired-analysis demo, and
explicit limitations. Note that mock-provider artifacts prove mechanisms,
not model quality; the uninformative McNemar result reflects identical
deterministic mock runs, not equivalence evidence.

`failover-mock-provider.log` is deliberately NOT committed: the repo's
`*.log` ignore rule covers it. It is transient mock-server request
diagnostics (port/timestamps only, no credentials); the substantive
failover evidence lives in `failover-demo-output.txt`.

## Verdict

Feature-complete for Phases 1–3 and locally release-ready. Release
validation remains blocked on human calibration (§3), real-provider
failover (§4), and branch-protection confirmation (§5).
