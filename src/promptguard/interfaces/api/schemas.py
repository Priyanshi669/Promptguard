"""Typed request/response schemas for the PromptGuard HTTP API.

Report payloads reuse the persisted ``EvaluationReport`` domain model so
the API cannot invent fields the domain does not carry.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from promptguard.domain.calibration_draft import DraftOverview, DraftRow
from promptguard.domain.models import EvaluationReport

RUN_JOB_STATUSES = (
    "queued",
    "running",
    "cancelled",
    "succeeded",
    "quality_gate_failed",
    "inconclusive",
    "error",
)
RunJobStatus = str  # constrained at the edge by RUN_JOB_STATUSES


class ApiError(BaseModel):
    """Consistent error envelope for every non-2xx response."""

    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] | None = None


class RunCreateRequest(BaseModel):
    """Create an evaluation run; provider options are server-side only."""

    prompt_version: str = Field(default="v1", min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    limit: int | None = Field(default=None, ge=1)
    baseline_run_id: str | None = Field(
        default=None,
        description="'latest' or a persisted run id; optional",
    )
    min_category_accuracy: float | None = Field(default=None, ge=0, le=1)
    min_summary_score: float | None = Field(default=None, ge=0, le=5)
    max_latency_ms: float | None = Field(default=None, gt=0)
    max_category_accuracy_drop: float | None = Field(default=None, ge=0, le=1)
    max_summary_score_drop: float | None = Field(default=None, ge=0, le=5)
    max_category_accuracy_drop_pct: float | None = Field(
        default=None, ge=0, le=100,
    )
    max_summary_score_drop_pct: float | None = Field(default=None, ge=0, le=100)


class RunCreatedResponse(BaseModel):
    job_id: str
    status: RunJobStatus
    created_at: str
    duplicate: bool = False


class RunJobStatusResponse(BaseModel):
    job_id: str
    status: RunJobStatus
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    report_run_id: str | None = None
    signature_hash: str


class RunCancelResponse(BaseModel):
    job_id: str
    status: RunJobStatus
    cancelled: bool


class RunListItem(BaseModel):
    run_id: str
    timestamp: str
    model: str
    dataset: str
    prompt_version: str
    case_count: int
    category_accuracy: float
    average_summary_score: float
    average_latency_ms: float
    regression_status: str | None
    baseline_run_id: str | None = None
    eval_config_hash: str | None = None


class RunListResponse(BaseModel):
    items: list[RunListItem]
    total: int
    offset: int
    limit: int
    skipped_runs: list[str] = Field(default_factory=list)


class RunDetailResponse(BaseModel):
    job: RunJobStatusResponse | None = None
    report: EvaluationReport


class BaselineListItem(RunListItem):
    critical_count: int = 0
    has_thresholds: bool = False


class BaselineListResponse(BaseModel):
    items: list[BaselineListItem]
    total: int
    offset: int
    limit: int
    note: str = (
        "Baseline management operations do not exist in the repository; "
        "listing is read-only by design."
    )


class HealthResponse(BaseModel):
    status: str = "ok"


class ReadinessResponse(BaseModel):
    ready: bool
    checks: dict[str, bool]
    auth_mode: str
    issues: list[str] = Field(default_factory=list)


class ProviderPoolEntry(BaseModel):
    name: str
    model: str


class ConfigResponse(BaseModel):
    candidate_model: str
    candidate_base_url: str
    judge_model: str
    judge_base_url: str
    request_timeout_seconds: float
    min_request_interval_seconds: float
    evaluation_timeout_seconds: float
    bootstrap: dict[str, Any]
    provider_pool: list[ProviderPoolEntry]
    prompt_versions: list[str]
    datasets: dict[str, int]
    credentials_in_identities: bool = Field(
        default=False,
        description=(
            "Asserted invariant: evaluator identities contain provider "
            "names and models only, never API keys."
        ),
    )


class DatasetSummaryResponse(BaseModel):
    """Golden dataset identity and composition for one prompt version."""

    prompt_version: str
    case_count: int
    case_count_by_category: dict[str, int]


class DraftOverviewResponse(BaseModel):
    """Calibration-draft workspace bundle with read-only note."""

    overview: DraftOverview
    note: str = (
        "Calibration inspection is read-only; reviewer fields are "
        "filled by humans via the workbook file (ADR-0002)."
    )


class CalibrationRowsResponse(BaseModel):
    """Paginated, read-only calibration draft row previews."""

    items: list[DraftRow]
    total: int
    offset: int
    limit: int
