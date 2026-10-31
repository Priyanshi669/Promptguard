"""Read-only calibration-draft HTTP routes (ADR-0002).

The calibration workflow has three separated states (draft workbook,
strict validation, executed calibration report). These endpoints only
read and observe them; they can never mutate the draft or fabricate
human annotations.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from promptguard.interfaces.api.schemas import (
    CalibrationRowsResponse,
    DraftOverviewResponse,
)

API_NO_EXPORT_NOTE = (
    "Calibration artifacts are exportable as files from the repository; "
    "the API exposes observation endpoints only (ADR-0002)."
)


def _draft_unavailable() -> Any:
    from promptguard.interfaces.api.routes_runs import _api_error

    raise _api_error(
        404,
        "draft_not_found",
        "No calibration draft workbook exists for this environment.",
    )


def create_calibration_router() -> APIRouter:
    """Calibration draft endpoints; strictly read-only."""
    router = APIRouter(tags=["calibration"])

    @router.get("/calibration/draft", response_model=DraftOverviewResponse)
    async def draft_overview(request: Request) -> DraftOverviewResponse:
        """Workspace bundle: counts, states, strict validation summary."""
        service = request.app.state.calibration
        overview = service.overview(include_strict_validation=True)
        if overview.draft_state == "missing":
            _draft_unavailable()
        return DraftOverviewResponse(
            overview=overview,
            note=API_NO_EXPORT_NOTE,
        )

    @router.get("/calibration/draft/rows", response_model=CalibrationRowsResponse)
    async def draft_rows(
        request: Request,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=25, ge=1, le=100),
    ) -> CalibrationRowsResponse:
        """Paginated read-only row previews with provenance flags."""
        service = request.app.state.calibration
        rows, total = service.list_rows(offset=offset, limit=limit)
        if total == 0 and service.overview(
            include_strict_validation=False,
        ).draft_state == "missing":
            _draft_unavailable()
        return CalibrationRowsResponse(
            items=rows, total=total, offset=offset, limit=limit,
        )

    @router.get(
        "/calibration/draft/validation",
        response_model=DraftOverviewResponse,
    )
    async def draft_validation(request: Request) -> DraftOverviewResponse:
        """Run the strict validator read-only; never weakens errors."""
        service = request.app.state.calibration
        overview = service.overview(include_strict_validation=True)
        if overview.draft_state == "missing":
            _draft_unavailable()
        return DraftOverviewResponse(
            overview=overview,
            note=API_NO_EXPORT_NOTE,
        )

    return router
