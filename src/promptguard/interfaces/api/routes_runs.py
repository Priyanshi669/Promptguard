"""Evaluation-run and baseline HTTP routes (thin adapters)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from promptguard.domain.models import EvaluationReport
from promptguard.infrastructure.reporting.html_renderer import (
    TextReportFormatter,
)
from promptguard.interfaces.api.schemas import (
    BaselineListItem,
    BaselineListResponse,
    RunCancelResponse,
    RunCreatedResponse,
    RunCreateRequest,
    RunDetailResponse,
    RunJobStatusResponse,
    RunListItem,
    RunListResponse,
)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    """HTTP error carrying an API code for the envelope handler."""
    exc = HTTPException(status_code=status_code, detail=message)
    exc.api_code = code  # type: ignore[attr-defined]
    return exc


def _job_status_payload(
    manager: Any, job_id: str,
) -> RunJobStatusResponse:
    """Job state mapped to the status schema, or 404."""
    job = manager.job(job_id)
    if job is None:
        raise _api_error(404, "job_not_found", f"Job {job_id} not found.")
    return RunJobStatusResponse(
        job_id=job.job_id,
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.error_code,
        error_message=job.error_message,
        report_run_id=job.report_run_id,
        signature_hash=job.signature_hash[:12],
    )


def _load_report(request: Request, run_id: str) -> EvaluationReport:
    """Fetch one persisted report or raise a 404 envelope error."""
    repository = request.app.state.run_repository
    try:
        return repository.get(run_id)
    except FileNotFoundError as exc:
        raise _api_error(
            404, "run_not_found", f"Run {run_id} not found.",
        ) from exc


def _run_item(report: EvaluationReport) -> RunListItem:
    """Project a persisted report onto the list-item schema."""
    regression = report.regression
    return RunListItem(
        run_id=report.run_id,
        timestamp=report.timestamp.isoformat(),
        model=report.model,
        dataset=report.dataset,
        prompt_version=report.prompt_version,
        case_count=report.case_count,
        category_accuracy=report.category_accuracy,
        average_summary_score=report.average_summary_score,
        average_latency_ms=report.average_latency_ms,
        regression_status=(
            str(regression.status) if regression is not None else None
        ),
        baseline_run_id=report.baseline_run_id,
        eval_config_hash=report.eval_config_hash,
    )


def _baseline_item(report: EvaluationReport) -> BaselineListItem:
    """List item plus baseline-only factual projection fields."""
    item = _run_item(report).model_dump()
    regression = report.regression
    critical_count = 0
    if regression is not None:
        critical_count = len(
            [
                case_regression
                for case_regression in regression.case_regressions
                if case_regression.is_regression
            ],
        )
        if regression.critical_case_failure:
            critical_count += 1
    item.update(
        critical_count=critical_count,
        has_thresholds=report.thresholds is not None,
    )
    return BaselineListItem(**item)


def create_runs_router(
    authorize_mutation: Callable[[Any], Awaitable[None]],
) -> APIRouter:
    """Run lifecycle endpoints; mutations require the API token."""
    router = APIRouter(tags=["runs"])

    @router.get("/runs", response_model=RunListResponse)
    async def list_runs(
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> RunListResponse:
        """Persisted run history, newest first, paginated."""
        repository = request.app.state.run_repository
        reports = repository.list_runs()
        total = len(reports)
        newest_first = list(reversed(reports))
        items = [_run_item(report) for report in newest_first[offset:offset + limit]]
        return RunListResponse(
            items=items,
            total=total,
            offset=offset,
            limit=limit,
            skipped_runs=repository.skipped_runs[-5:],
        )

    @router.get("/runs/{run_id}", response_model=RunDetailResponse)
    async def get_run(request: Request, run_id: str) -> RunDetailResponse:
        """Full report plus its originating job status when available."""
        report = _load_report(request, run_id)
        job_status = None
        for job in request.app.state.run_manager.all_jobs():
            if job.report_run_id == run_id:
                job_status = _job_status_payload(
                    request.app.state.run_manager, job.job_id,
                )
                break
        return RunDetailResponse(job=job_status, report=report)

    @router.get("/runs/{run_id}/export")
    async def export_run(
        request: Request,
        run_id: str,
        format: str = Query(default="json", pattern="^(json|text)$"),
    ) -> Response:
        """Export a report as JSON (native) or formatted console text."""
        report = _load_report(request, run_id)
        filename_root = f"report-{run_id}"
        if format == "text":
            text = TextReportFormatter().format(report)
            return PlainTextResponse(
                text,
                media_type="text/plain; charset=utf-8",
                headers={
                    "Content-Disposition":
                        f'attachment; filename="{filename_root}.txt"',
                },
            )
        return JSONResponse(
            report.model_dump(mode="json"),
            headers={
                "Content-Disposition":
                    f'attachment; filename="{filename_root}.json"',
            },
        )

    @router.post(
        "/runs",
        response_model=RunCreatedResponse,
        status_code=202,
        dependencies=[Depends(authorize_mutation)],
    )
    async def create_run(
        request: Request, body: RunCreateRequest,
    ) -> RunCreatedResponse:
        """Queue an evaluation run (ADR-0003 in-process runner)."""
        job, duplicate = request.app.state.run_manager.submit(body)
        return RunCreatedResponse(
            job_id=job.job_id,
            status=job.status,
            created_at=job.created_at,
            duplicate=duplicate,
        )

    @router.get("/run-jobs/{job_id}", response_model=RunJobStatusResponse)
    async def job_status(request: Request, job_id: str) -> RunJobStatusResponse:
        """Observable background job status."""
        return _job_status_payload(request.app.state.run_manager, job_id)

    @router.delete(
        "/run-jobs/{job_id}",
        response_model=RunCancelResponse,
        dependencies=[Depends(authorize_mutation)],
    )
    async def cancel_job(request: Request, job_id: str) -> RunCancelResponse:
        """Cancel a queued/running job; persisted reports remain intact."""
        job = await request.app.state.run_manager.cancel(job_id)
        if job is None:
            raise _api_error(404, "job_not_found", f"Job {job_id} not found.")
        return RunCancelResponse(
            job_id=job.job_id,
            status=job.status,
            cancelled=job.status == "cancelled",
        )

    return router


def create_baselines_router() -> APIRouter:
    """Baseline listing; read-only because no management ops exist."""
    router = APIRouter(tags=["baselines"])

    @router.get("/baselines", response_model=BaselineListResponse)
    async def list_baselines(
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=100),
    ) -> BaselineListResponse:
        """Persisted runs usable as regression baselines (id or latest)."""
        repository = request.app.state.run_repository
        reports = list(reversed(repository.list_runs()))
        total = len(reports)
        items = [
            _baseline_item(report)
            for report in reports[offset:offset + limit]
        ]
        return BaselineListResponse(
            items=items, total=total, offset=offset, limit=limit,
        )

    return router
