"""In-process background execution of evaluation runs (ADR-0003).

The simplest reliable runner for the single-host deployment target:
asyncio tasks gated by a bounded semaphore, status observable by job id,
and duplicate submissions rejected by request-signature fingerprint
while a job is queued or running. No broker or extra database; completed
reports are durably persisted by the existing run service (per-run JSON
files).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from promptguard.domain.models import GoldenTestCase
from promptguard.domain.statistics import BootstrapConfig
from promptguard.domain.thresholds import RegressionThresholds
from promptguard.interfaces.api.schemas import RunCreateRequest

logger = logging.getLogger("promptguard.api.runner")

BUSY_STATUSES = ("queued", "running")
TERMINAL_STATUSES = (
    "cancelled",
    "succeeded",
    "quality_gate_failed",
    "inconclusive",
    "error",
)


def _now_iso() -> str:
    """UTC timestamp for job bookkeeping."""
    return datetime.now(UTC).isoformat()


def new_job_id() -> str:
    """Unique, time-sortable job identifier."""
    return f"{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:8]}"


@dataclass
class RunWiring:
    """Everything the pipeline needs, composed per job configuration."""

    run_service: Any
    system_prompt: str
    test_cases: list[GoldenTestCase]
    model: str
    eval_config: dict[str, Any]


@dataclass
class JobState:
    job_id: str
    signature_hash: str
    request: RunCreateRequest
    created_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    status: str = "queued"
    error_code: str | None = None
    error_message: str | None = None
    report_run_id: str | None = None
    task: asyncio.Task[None] | None = None


def signature_of(request: RunCreateRequest) -> str:
    """Fingerprint a run request for duplicate-submission detection."""
    payload = request.model_dump()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_limit(
    test_cases: list[GoldenTestCase],
    limit: int | None,
) -> list[GoldenTestCase]:
    """First-N dataset policy shared by CLI and API."""
    if limit is None:
        return test_cases
    return test_cases[:limit]


def thresholds_from_request(
    request: RunCreateRequest,
) -> RegressionThresholds | None:
    """Build the gate thresholds from the validated request body."""
    api_to_domain = {
        "min_category_accuracy": "minimum_category_accuracy",
        "min_summary_score": "minimum_summary_score",
        "max_latency_ms": "maximum_average_latency_ms",
        "max_category_accuracy_drop": "maximum_category_accuracy_drop",
        "max_summary_score_drop": "maximum_summary_score_drop",
        "max_category_accuracy_drop_pct": (
            "maximum_category_accuracy_drop_pct"
        ),
        "max_summary_score_drop_pct": "maximum_summary_score_drop_pct",
        "max_category_drift": "maximum_category_drift",
    }
    values = request.model_dump(
        exclude={"prompt_version", "limit", "baseline_run_id"},
    )
    if all(value is None for value in values.values()):
        return None
    thresholds = {
        api_to_domain[key]: value
        for key, value in values.items()
        if value is not None
    }
    return RegressionThresholds(**thresholds)


def map_report_status(report_report: Any) -> str:
    """Map a persisted report to the API job status vocabulary."""
    regression = getattr(report_report, "regression", None)
    if regression is None:
        return "inconclusive"
    status = getattr(regression, "status", None)
    if status == "PASS":
        return "succeeded"
    if status == "FAIL":
        return "quality_gate_failed"
    return "inconclusive"


class RunManager:
    """Run jobs with bounded concurrency and observable status."""

    def __init__(
        self,
        max_concurrency: int = 1,
        evaluation_timeout_seconds: float = 900.0,
        bootstrap: BootstrapConfig | None = None,
        wiring: RunWiring | CallableWirer = None,  # type: ignore[assignment]
    ) -> None:
        """``wiring`` adapts to RunCreateRequest; see api/deps.py."""
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._evaluation_timeout = evaluation_timeout_seconds
        self._bootstrap = bootstrap
        self._wiring = wiring
        self._jobs: dict[str, JobState] = {}
        self._signature_to_job: dict[str, str] = {}

    def job(self, job_id: str) -> JobState | None:
        """Return job state when present."""
        return self._jobs.get(job_id)

    def all_jobs(self) -> list[JobState]:
        """Newest-first job list."""
        return sorted(
            self._jobs.values(),
            key=lambda state: (state.created_at, state.job_id),
            reverse=True,
        )

    def busy_job_for(
        self,
        request: RunCreateRequest,
        signature_hash: str,
    ) -> JobState | None:
        """The queued/running job with this exact request, if any."""
        mapped_job = self._signature_to_job.get(signature_hash)
        if mapped_job is None:
            return None
        job = self._jobs.get(mapped_job)
        if job is None or job.status not in BUSY_STATUSES:
            return None
        if job.request == request:
            return job
        return None

    def submit(
        self,
        request: RunCreateRequest,
        wiring_override: RunWiring | CallableWirer = None,  # type: ignore[assignment]
    ) -> tuple[JobState, bool]:
        """Register a job; return (job, duplicate_busy_request)."""
        signature = signature_of(request)
        duplicate = self.busy_job_for(request, signature)
        if duplicate is not None:
            return duplicate, True
        wiring = wiring_override if wiring_override is not None else self._wiring
        job = JobState(
            job_id=new_job_id(),
            signature_hash=signature,
            request=request,
        )
        self._jobs[job.job_id] = job
        self._signature_to_job[signature] = job.job_id
        job.task = asyncio.create_task(self._execute(job, wiring))
        return job, False

    async def cancel(self, job_id: str) -> JobState | None:
        """Cancel a queued/running job; terminal states are untouched."""
        job = self._jobs.get(job_id)
        if job is None or job.status in TERMINAL_STATUSES:
            return job
        if job.task is not None and not job.task.done():
            job.task.cancel()
            try:
                await job.task
            except asyncio.CancelledError:
                pass
        job.status = "cancelled"
        job.finished_at = _now_iso()
        self._forget_signature(job)
        return job

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Cancel everything and wait briefly; used for graceful stop."""
        pending_tasks = [
            job.task
            for job in self._jobs.values()
            if job.task is not None and not job.task.done()
        ]
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.wait(set(pending_tasks), timeout=timeout)
        self._signature_to_job.clear()

    async def _execute(self, job: JobState, wiring: Any) -> None:
        """Run the pipeline inside the configured deadline."""
        async with self._semaphore:
            job.status = "running"
            job.started_at = _now_iso()
            started = datetime.now(UTC)
            logger.info(
                "run.started job_id=%s status=%s", job.job_id, job.status,
            )
            try:
                composed = self._compose(wiring, job.request)
                await asyncio.wait_for(
                    self._run_pipeline(job, composed),
                    timeout=self._evaluation_timeout,
                )
            except TimeoutError:
                job.status = "error"
                job.error_code = "evaluation_timeout"
                job.error_message = (
                    "Evaluation exceeded the deadline "
                    f"({self._evaluation_timeout:g}s)."
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to the API
                job.status = "error"
                job.error_code = "run_failed"
                job.error_message = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished_at = _now_iso()
                self._forget_signature(job)
                logger.info(
                    "run.finished job_id=%s status=%s error_code=%s "
                    "elapsed_ms=%.1f",
                    job.job_id,
                    job.status,
                    job.error_code,
                    (datetime.now(UTC) - started).total_seconds() * 1000,
                )

    def _compose(self, wiring: Any, request: RunCreateRequest) -> RunWiring:
        """Adapt the configured wiring function to a RunWiring bundle."""
        if wiring is None:
            raise RuntimeError(
                "Run manager has no evaluation wiring configured."
            )
        if hasattr(wiring, "run_service"):
            composed: RunWiring = wiring
        else:
            composed = wiring(request)
        return composed

    async def _run_pipeline(
        self,
        job: JobState,
        composed: RunWiring,
    ) -> None:
        request = job.request
        test_cases = apply_limit(list(composed.test_cases), request.limit)
        outcome = await composed.run_service.run(
            test_cases=test_cases,
            system_prompt=composed.system_prompt,
            prompt_version=request.prompt_version,
            model=composed.model,
            dataset=f"golden-{request.prompt_version}",
            thresholds=thresholds_from_request(request),
            baseline_run_id=request.baseline_run_id,
            eval_config=composed.eval_config,
            bootstrap=self._bootstrap,
        )
        report = getattr(outcome, "report", None)
        if report is None:
            job.status = "error"
            job.error_code = "no_report"
            job.error_message = "Run completed without a report."
            return
        job.report_run_id = report.run_id
        job.status = map_report_status(report)

    def _forget_signature(self, job: JobState) -> None:
        """Drop the busy-signature mapping if this job still owns it."""
        owner = self._signature_to_job.get(job.signature_hash)
        if owner == job.job_id:
            self._signature_to_job.pop(job.signature_hash, None)


class CallableWirer(Protocol):
    """Framework hook: () -> RunWiring on invoke, injected by the app."""

    def __call__(self, request: RunCreateRequest) -> RunWiring: ...
