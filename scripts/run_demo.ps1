# PromptGuard end-to-end demo: evaluate, inspect the report, and
# demonstrate the regression gate (pass and fail paths).
#
# Usage (from the repository root):
#   powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1 -SkipChecks
#
# Requires .env with NVIDIA_API_KEY and NVIDIA_MODEL.

param(
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Write-Host ("ERROR: .env not found. Copy your credentials into .env " +
        "(NVIDIA_API_KEY, NVIDIA_MODEL) and retry.") -ForegroundColor Red
    exit 2
}

# Explicit 60s per-request headroom over the 30s default for slow providers.
$env:NVIDIA_REQUEST_TIMEOUT_SECONDS = "60"

function Invoke-Step {
    param([string]$Title)
    Write-Host ""
    Write-Host "=== $Title ===" -ForegroundColor Cyan
}

if (-not $SkipChecks) {
    Invoke-Step "Step 0: test suite, lint, and type checks"
    uv run pytest -q
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv run ruff check src tests scripts
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    uv run mypy src
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Invoke-Step "Step 1: evaluate 10 golden cases against the latest baseline"
Write-Host "Gate: category accuracy >= 0.7, summary score >= 3.0, latency <= 60s"
uv run promptguard --prompt-version v1 --limit 10 --baseline latest `
    --min-category-accuracy 0.7 --min-summary-score 3.0 --max-latency-ms 60000
if ($LASTEXITCODE -ne 0) {
    Write-Host "Evaluation or quality gate failed (exit $LASTEXITCODE)." `
        -ForegroundColor Red
    exit $LASTEXITCODE
}

Invoke-Step "Step 2: show the persisted run report"
$latest = Get-ChildItem runs | Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
Write-Host "Persisted run: $($latest.FullName)"

Invoke-Step "Step 3: demonstrate the gate detecting a quality breach"
Write-Host "Requiring 100% category accuracy; the model scores ~80%, so the"`
    "gate must FAIL with exit code 1."
uv run promptguard --prompt-version v1 --limit 10 --baseline latest `
    --min-category-accuracy 1.0
$failCode = $LASTEXITCODE
if ($failCode -eq 1) {
    Write-Host "As expected: the quality gate FAILED the run (exit 1)." `
        -ForegroundColor Yellow
} else {
    Write-Host "Unexpected exit code $failCode (expected 1)." `
        -ForegroundColor Red
    exit $failCode
}

Invoke-Step "Demo complete"
Write-Host "PASS path: exit 0. FAIL path: exit 1. Reports: runs\"
