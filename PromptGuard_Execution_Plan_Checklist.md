# PromptGuard 2.0: Sequential Execution Plans and Acceptance Checklist

**Document purpose:** A controlled execution contract for completing and
demonstrating PromptGuard in three sequential plans, with expected
outputs, acceptance gates, and final deliverables.

**Project context:** Existing Python/uv repository at
`C:\project\Promptguard`; layered domain/application/infrastructure/CLI
architecture; BANKING77 golden dataset; NVIDIA-backed candidate/judge
integration; DeepEval GEval summary-quality evaluation; evaluator
configuration fingerprinting; regression comparisons and quality gates.

**Important execution rule:** Treat this document as a plan, not
evidence that a step has run. Do not mark a checklist item complete
until the command, test, artifact, or review output proves it. Do not
fabricate run IDs, scores, provider usage, latency, token counts, human
annotations, or calibration status.

------------------------------------------------------------------------

## 1. Target outcome

At the end of the three plans, PromptGuard should be able to:

1.  Run a real evaluation through the existing production CLI and
    service/repository path.
2.  Persist a baseline report with a run ID and reproducibility
    metadata.
3.  Compare a subsequent run to an explicit baseline and produce a
    readable regression decision.
4.  Demonstrate a controlled regression without modifying the production
    prompt or silently changing the golden dataset.
5.  Produce evidence-backed reports and a final status summary that
    distinguishes passed, failed, blocked, and not-run work.
6.  Preserve the calibration approval gate. If human annotations are
    unavailable, calibration stays **blocked / not human-calibrated**.
    Provisional model-generated labels may be used only in a clearly
    separated experiment and never described as human ground truth.

## 2. Execution rules that apply to every plan

-   Work from `C:\project\Promptguard` and the intended project virtual
    environment.
-   Use `uv` for Python commands and dependency management.
-   Inspect the actual CLI help and current repository before assuming
    flags, file paths, or behavior.
-   Keep production prompts and the canonical golden dataset unchanged
    during regression demonstrations.
-   Use an explicit baseline run ID when comparing runs. Avoid `latest`
    in evidence intended for a report because it can point to a
    different run later.
-   Do not run paid/live-provider calls until provider configuration,
    cost/rate expectations, and the intended number of cases are
    understood.
-   Start with a small evaluation, such as 10 cases, if the existing CLI
    supports a limit. This is a smoke test, not proof of full-dataset
    quality.
-   Record command, timestamp, commit SHA, prompt version,
    dataset/version/hash, evaluator-config hash, model/provider, run ID,
    thresholds, metrics, and artifact locations where available.
-   If the code does not emit a field, report it as unavailable rather
    than inventing it. Make a code change only when the field is
    required and the change is in scope.
-   Keep unrelated pre-existing formatting or lint drift separate from
    changes made for these plans.
-   Never weaken approval checks simply to make a plan pass.
-   A run that exits successfully is not automatically a quality pass.
    Report execution status and quality-gate status separately.
-   Stop and investigate if output is malformed, persistence is missing,
    the baseline is ambiguous, or the evaluator configuration
    unexpectedly changes.

------------------------------------------------------------------------

# Plan 1: Establish a trustworthy baseline

## Objective

Prove that the existing production evaluation path runs successfully on
a small, known slice of the canonical golden dataset and emits a
durable, identifiable report.

## Steps

### 1.1 Inspect repository and CLI contract

-   [ ] Confirm repository root and branch.
-   [ ] Capture `git status --short` and current commit SHA.
-   [ ] Inspect CLI help before using flags.
-   [ ] Confirm the golden dataset path, prompt-version selection
    behavior, provider/model configuration, report persistence location,
    and whether `--limit` exists.
-   [ ] Confirm that provider credentials are configured without
    printing secrets.
-   [ ] Check that the intended run is small enough for time/cost
    constraints.

Suggested PowerShell commands:

``` powershell
Set-Location C:\project\Promptguard
git status --short
git rev-parse HEAD
uv run promptguard --help
```

If the executable or option differs, use the command documented by the
repository and record the actual syntax. Do not assume the example below
is valid until `--help` confirms it.

### 1.2 Run a small baseline

-   [ ] Run the existing production CLI on 10 cases, if supported.
-   [ ] Do not modify the production prompt or golden dataset to force a
    result.
-   [ ] Capture the complete terminal output.
-   [ ] Record the run ID printed by the program.
-   [ ] Confirm the report is persisted and can be reopened/read.
-   [ ] Confirm result count matches the requested limit, subject to
    documented CLI behavior.
-   [ ] Record errors, skipped cases, retries, and provider failures, if
    reported.

Example only, after confirming the CLI flags:

``` powershell
uv run promptguard --limit 10
```

### 1.3 Validate baseline integrity

-   [ ] Check the report's run ID and timestamp.
-   [ ] Check prompt version and dataset identity/version.
-   [ ] Check evaluator-config fingerprint/hash and pinned evaluation
    steps, if exposed.
-   [ ] Check candidate and judge model/provider identities, if exposed.
-   [ ] Check aggregate metrics and per-case results.
-   [ ] Check persistence by reading the saved report, not only the
    terminal summary.
-   [ ] Save the command/output and report path as evidence.

## Expected output after Plan 1

The exact formatting depends on the existing CLI. The following is a
**shape example**, not expected real values:

``` text
PromptGuard Evaluation
Status: COMPLETED
Run ID: <actual-run-id>
Prompt version: <actual-prompt-version>
Dataset: <actual-dataset/version>
Cases requested: 10
Cases evaluated: <actual-count>
Execution errors: <actual-count>

Metrics:
  Classification accuracy: <actual-value-or-N/A>
  Summary quality: <actual-value-or-N/A>
  Mean latency: <actual-value-or-N/A>
  Token usage: <actual-value-or-N/A>

Evaluator config hash: <actual-hash-or-N/A>
Report: <actual-persisted-report-path>
Quality gate: <PASS | FAIL | NOT_CONFIGURED>
```

### Plan 1 acceptance gate

**PASS only if** the production CLI completes, returns a real run ID,
persists a readable report, and report metadata/results are internally
consistent.

**FAIL** if the run errors, result counts are inconsistent, or
persistence cannot be verified.

**BLOCKED** if provider credentials/configuration or a necessary
dependency is unavailable.

**NOT PROVEN:** Overall model quality, full-dataset quality, stability
across model calls, and calibration. Ten cases are only a smoke-test
baseline.

### Plan 1 evidence to retain

-   [ ] Commit SHA and clean/known working-tree status.
-   [ ] Exact command and terminal output.
-   [ ] Run ID.
-   [ ] Persisted report file/path.
-   [ ] Key metrics and execution/quality-gate statuses.
-   [ ] Any limitations or missing fields.

------------------------------------------------------------------------

# Plan 2: Prove regression comparison and quality-gate behavior

## Objective

Demonstrate that PromptGuard can compare a new run against the exact
baseline from Plan 1 and make a transparent regression decision.

## Steps

### 2.1 Inspect the real comparison behavior

-   [ ] Read CLI help and relevant implementation/tests for baseline
    comparison.
-   [ ] Confirm which metrics and thresholds are compared.
-   [ ] Confirm the report format for deltas and gate failures.
-   [ ] Confirm whether baseline and candidate runs must use the same
    dataset, prompt/model settings, or evaluator fingerprint.
-   [ ] Use the explicit Plan 1 run ID for every comparison.

### 2.2 Run a comparison without intentional changes

-   [ ] Run the same evaluation configuration again, if cost and runtime
    permit.
-   [ ] Pass the exact Plan 1 run ID as the baseline using the actual
    supported syntax.
-   [ ] Record the second run ID.
-   [ ] Capture absolute values, deltas, threshold settings, and gate
    decision.
-   [ ] Interpret changes cautiously: model outputs and LLM-judge scores
    may vary even with identical configuration.

Do not demand identical scores from a live LLM run unless the system and
provider truly guarantee deterministic outputs. This run tests the
comparison pipeline; it does not by itself prove regression detection.

### 2.3 Demonstrate a controlled regression safely

Preferred order:

1.  Use an existing regression test or fixture that deliberately lowers
    a metric and verifies that the gate fails.
2.  If the project has no suitable fixture, add a focused automated test
    using synthetic reports or a test-only fake provider. Keep it
    isolated from production configuration and real golden data.
3.  Only if the existing architecture supports it cleanly, use a
    temporary prompt/config copy in a disposable test location. Do not
    overwrite the production prompt.
4.  Do not label a live rerun as a "controlled regression" merely
    because its score went down.

-   [ ] Verify the degraded case triggers the intended metric delta.
-   [ ] Verify the threshold comparison returns the expected gate
    failure.
-   [ ] Verify a non-regressing case passes the same test.
-   [ ] Verify the baseline report remains unchanged.
-   [ ] Verify production prompt and golden dataset are unchanged.
-   [ ] Capture the automated test output and regression report.

### 2.4 Validate failure reporting

-   [ ] The report identifies the baseline run ID and candidate run ID.
-   [ ] It states which metric changed and by how much.
-   [ ] It records the configured threshold and observed value/delta.
-   [ ] It gives an explicit gate outcome and a human-readable reason.
-   [ ] It distinguishes execution failure from a quality regression.
-   [ ] Missing metric data is handled explicitly, not silently treated
    as a pass.

## Expected output after Plan 2

Shape example only:

``` text
PromptGuard Regression Check
Baseline run: <Plan-1-run-id>
Candidate run: <actual-second-run-id>
Comparison: COMPLETED

Metric                         Baseline   Candidate   Delta   Threshold   Result
Classification accuracy        <value>    <value>     <delta> <threshold> <PASS/FAIL>
Summary quality                <value>    <value>     <delta> <threshold> <PASS/FAIL>

Quality gate: <PASS | FAIL>
Reason: <actual explanation>
Regression report: <actual path>
```

For the controlled test, the expected assertion should look conceptually
like:

``` text
Test: degraded metric crosses configured threshold
Expected gate: FAIL
Actual gate: FAIL
Test result: PASS
```

This is a **test passing by correctly detecting a regression**. It is
not a statement that the degraded model passed quality.

### Plan 2 acceptance gate

**PASS only if** the comparison uses the explicit baseline ID, emits
interpretable deltas and threshold decisions, and a controlled test
proves that an intentional degradation triggers the expected failure
while a healthy fixture behaves as specified.

**FAIL** if the wrong baseline is used, the threshold behavior is
incorrect, or the system silently passes missing/invalid metrics.

**BLOCKED** if the existing CLI cannot compare reports and a required
implementation change cannot be safely completed in the time available.
Report the blocker; do not fake the comparison.

### Plan 2 evidence to retain

-   [ ] Baseline and candidate run IDs.
-   [ ] Exact command lines.
-   [ ] Comparison report.
-   [ ] Threshold configuration.
-   [ ] Test proving the expected failure/pass behavior.
-   [ ] `git diff` showing production prompt and golden dataset were not
    modified for the demonstration.

------------------------------------------------------------------------

# Plan 3: Harden evidence, run final verification, and prepare the showcase

## Objective

Turn the working baseline/regression path into a reproducible,
reviewable project demonstration, and clearly disclose any work that
remains incomplete.

## Steps

### 3.1 Re-run quality checks

Use the repository's actual documented commands. Typical checks, if
configured:

``` powershell
uv run pytest
uv run ruff check src tests scripts
uv run mypy src
uv lock --check
```

-   [ ] Record each command and its actual exit status.
-   [ ] Separate new failures from pre-existing repository issues.
-   [ ] Do not claim a check passed if it was not run.
-   [ ] If full tests take too long, run focused tests and label the
    broader suite `NOT RUN`, not `PASS`.

### 3.2 Verify report and reproducibility metadata

-   [ ] Ensure baseline and candidate reports are retained.
-   [ ] Ensure run IDs are unique and references resolve.
-   [ ] Ensure evaluator fingerprint/config hash is present where
    supported.
-   [ ] Ensure prompt and dataset versions are recorded.
-   [ ] Ensure threshold values and comparison results are retained.
-   [ ] Ensure provider/model, latency, token usage, and cost are
    reported only if the implementation actually captures them.
-   [ ] Ensure report schema/version is explicit if supported.
-   [ ] Ensure errors and missing values are visible.

### 3.3 Calibration status must be honest

-   [ ] Keep the calibration manifest approval gate intact.
-   [ ] If human annotations are absent, mark calibration **BLOCKED:
    human labels/review unavailable**.
-   [ ] Do not create or claim a human-calibration report without valid
    human annotations.
-   [ ] If using LLM-generated provisional labels for exploratory
    analysis, label them `PROVISIONAL / MODEL-GENERATED`, store the
    generator identity/config, and do not use them to approve the
    production evaluator.
-   [ ] Do not mix provisional pseudo-labels with human-ground-truth
    metrics.

### 3.4 Create the final evidence bundle

Use the project's existing report/document locations where possible. Do
not invent a new output directory if repository conventions already
define one.

Suggested bundle contents:

``` text
execution-evidence/
  execution-summary.md
  plan-1-baseline/
    command.txt
    terminal-output.txt
    run-id.txt
    report-reference.txt
  plan-2-regression/
    comparison-command.txt
    comparison-output.txt
    comparison-report-reference.txt
    regression-test-output.txt
  plan-3-verification/
    test-results.txt
    lint-results.txt
    type-check-results.txt
    lock-check-results.txt
  limitations-and-blockers.md
```

These filenames are suggestions. Use the actual repository conventions
and ensure sensitive values, especially API keys, are never included.

### 3.5 Produce the final execution summary

-   [ ] List each plan's status: PASS, FAIL, BLOCKED, or NOT RUN.
-   [ ] Include real run IDs, metric values, deltas, thresholds, and
    report locations.
-   [ ] List test/lint/type-check outcomes.
-   [ ] List known limitations.
-   [ ] Distinguish working regression infrastructure from evaluator
    calibration.
-   [ ] State the exact next action for each blocker.
-   [ ] Confirm whether production prompts/dataset were left unchanged.

## Expected output after Plan 3

Shape example only:

``` text
PROMPTGUARD 2.0 EXECUTION SUMMARY
Commit: <actual-sha>
Execution date: <actual-date>

Plan 1: <PASS/FAIL/BLOCKED/NOT RUN>
  Baseline run: <actual-run-id-or-N/A>
  Report: <actual-path-or-N/A>

Plan 2: <PASS/FAIL/BLOCKED/NOT RUN>
  Baseline: <actual-run-id-or-N/A>
  Candidate: <actual-run-id-or-N/A>
  Controlled regression test: <PASS/FAIL/NOT RUN>
  Gate behavior: <actual result>

Plan 3: <PASS/FAIL/BLOCKED/NOT RUN>
  Tests: <actual result>
  Lint: <actual result>
  Type checking: <actual result>
  Dependency lock check: <actual result>

Calibration: <APPROVED only with required evidence | BLOCKED | NOT RUN>
Production prompt changed for demo: <YES/NO>
Canonical golden dataset changed for demo: <YES/NO>

Verified capabilities:
  - <only capabilities demonstrated by evidence>

Known limitations:
  - <specific limitation>

Final project status: <evidence-based summary>
Next action: <single highest-priority action>
```

### Plan 3 acceptance gate

**PASS** when verification results and artifacts are reproducible, the
final summary is evidence-backed, and unresolved blockers are explicit.

A final project status may be **PARTIALLY COMPLETE** even when the plans
were executed successfully. Passing a smoke test and regression test
does not imply that all original product features are implemented.

------------------------------------------------------------------------

# 4. Final system checklist

## Evaluation execution

-   [ ] Production CLI reaches the real evaluation service.
-   [ ] Candidate generation/evaluation uses the configured provider.
-   [ ] The report records one result per processed case.
-   [ ] Execution errors and skipped cases are visible.
-   [ ] Run IDs are unique and report persistence works.

## Evaluation integrity

-   [ ] Canonical dataset identity/version is recorded.
-   [ ] Prompt version is recorded.
-   [ ] Judge/model/provider identity is recorded where available.
-   [ ] Pinned evaluation steps and evaluator-config hash are recorded
    where supported.
-   [ ] Score normalization and aggregate semantics are documented.
-   [ ] Missing metrics do not silently pass a quality gate.

## Regression behavior

-   [ ] Comparison references an explicit baseline run ID.
-   [ ] Metric deltas are shown.
-   [ ] Threshold values are shown.
-   [ ] Regression failure has a clear reason.
-   [ ] A controlled automated test proves the failure path.
-   [ ] A healthy fixture proves the non-regression path.
-   [ ] Live-model variability is not confused with a controlled
    regression.

## Reports and evidence

-   [ ] Baseline and candidate reports can be reopened.
-   [ ] Terminal output and exact commands are retained.
-   [ ] Run IDs and report paths are captured.
-   [ ] Test, lint, type-check, and lock-check statuses are truthful.
-   [ ] Final summary lists PASS, FAIL, BLOCKED, and NOT RUN distinctly.

## Calibration and safety of claims

-   [ ] Human calibration approval is not bypassed.
-   [ ] No fabricated annotations or provider outputs.
-   [ ] Model-generated labels are explicitly provisional.
-   [ ] Calibration remains blocked if its human-label gate is unmet.
-   [ ] No secrets are copied into evidence.

## Scope discipline

-   [ ] Production prompt unchanged during regression demonstration.
-   [ ] Canonical golden dataset unchanged during regression
    demonstration.
-   [ ] No unrelated refactor bundled into the deadline-critical work.
-   [ ] Features not implemented are listed as future work, not
    described as complete.

------------------------------------------------------------------------

# 5. Copy-paste execution prompt for an AI coding assistant

Use the following prompt in your coding assistant or project workspace.
It is intentionally strict about inspecting the repository, executing
plans sequentially, and reporting evidence rather than making
unsupported claims.

> You are the implementation and verification engineer for PromptGuard
> 2.0 in `C:\\project\\Promptguard`.
>
> **Goal:** Execute Plan 1, then Plan 2, then Plan 3 from the
> accompanying "PromptGuard 2.0: Sequential Execution Plans and
> Acceptance Checklist" document. Do not skip gates or start later plans
> before the previous plan's acceptance gate is satisfied, except to
> document a blocker.
>
> **Repository context:** This is a Python project managed with `uv`,
> with a layered domain/application/infrastructure/CLI design, a
> BANKING77 golden dataset, an NVIDIA-backed provider integration,
> DeepEval GEval summary-quality evaluation, evaluator configuration
> fingerprinting, report persistence, regression comparison, and quality
> gates. Verify these facts in the actual repository before relying on
> them.
>
> **Rules:**
>
> 1.  Inspect the current branch, `git status`, commit SHA, CLI help,
>     relevant tests, prompt configuration, dataset configuration,
>     persistence implementation, and report schema before editing files
>     or invoking live model calls.
> 2.  Never claim access to files or command output you have not
>     actually inspected. Never invent run IDs, scores, metrics, hashes,
>     test results, API usage, or report paths.
> 3.  Use `uv` and the repository's real production CLI/service path.
>     Verify all CLI flags from `--help` or source before use.
> 4.  Start with a small baseline (10 cases if supported and
>     affordable). Capture the exact command, terminal output, run ID,
>     persisted report, prompt version, dataset identity, evaluator
>     fingerprint, model/provider identity, metrics, errors, and
>     quality-gate result where available.
> 5.  Compare against the explicit baseline run ID, not an ambiguous
>     `latest` reference. Report actual metric deltas and configured
>     thresholds.
> 6.  Demonstrate controlled regression using an existing automated
>     test/fixture or a focused test-only fake/synthetic report. Do not
>     edit the production prompt or canonical golden dataset to force a
>     failure. Do not treat a stochastic live rerun as a controlled
>     regression.
> 7.  Keep evaluator-calibration approval checks intact. If required
>     human annotations or review are absent, report calibration as
>     blocked/not human-calibrated. Never fabricate human labels or
>     claim model-generated pseudo-labels are human ground truth.
> 8.  Keep changes narrow. Do not add a parallel production runner if
>     the existing CLI supports the required path. Do not undertake
>     unrelated refactors. Do not silently reformat unrelated files.
> 9.  Run the appropriate tests, lint, type checking, and lock
>     validation supported by the repo. Record actual results.
>     Distinguish pre-existing failures from regressions caused by your
>     changes.
> 10. Maintain a checklist with one of these statuses for every item:
>     `PASS`, `FAIL`, `BLOCKED`, or `NOT RUN`. Attach a concrete
>     evidence reference to every `PASS`.
> 11. After each plan, stop and present its results, evidence,
>     limitations, and acceptance-gate decision before proceeding to the
>     next plan. If a gate fails or is blocked, explain the blocker and
>     propose the smallest safe next step. Do not represent the plan as
>     passed.
> 12. Protect credentials and private data. Never print or store secrets
>     in logs or evidence.
>
> **Required final response:** Provide a concise execution summary
> containing the actual commit SHA, status of Plans 1--3, exact run IDs,
> report references, metrics/deltas/thresholds, regression-test result,
> verification results, calibration status, files changed, known
> limitations, and next action. Clearly label any feature that remains
> unimplemented or unverified.
>
> Begin with repository inspection and Plan 1 only. Do not assume
> success and do not proceed to Plan 2 until the Plan 1 acceptance gate
> has been evaluated from actual evidence.

------------------------------------------------------------------------

# 6. Definition of done

The three plans are complete only when:

-   [ ] Plan 1 has a real persisted baseline report and verified run ID.
-   [ ] Plan 2 has a real comparison against that explicit baseline and
    an automated controlled-regression test that verifies expected gate
    behavior.
-   [ ] Plan 3 has recorded verification results and an evidence-backed
    final summary.
-   [ ] Calibration status is accurately disclosed and approval gates
    remain intact.
-   [ ] The final report separates demonstrated capabilities from
    incomplete or unverified features.

**Deadline priority:** Make the production CLI baseline and regression
path demonstrable first. A polished dashboard, Slack integration, API,
Docker packaging, drift monitoring, or human-calibration completion must
not be claimed as done unless separately implemented and verified.
