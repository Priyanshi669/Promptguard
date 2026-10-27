"""PromptGuard HTTP API (v1) — thin HTTP adapters over existing services.

The API exposes capabilities that exist in the repository and no others.
Routes stay thin: domain and application services own all logic. See
docs/architecture/ADR-0003 for technology and execution-model decisions.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as FastApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.types import ASGIApp, Receive, Scope, Send

from promptguard.application.calibration_draft_service import (
    CalibrationDraftService,
)
from promptguard.application.wiring import (
    PROJECT_ROOT,
    build_default_run_service,
)
from promptguard.config import get_settings
from promptguard.domain.statistics import BootstrapConfig
from promptguard.infrastructure.dataset.json_repository import (
    JsonDatasetRepository,
)
from promptguard.infrastructure.evaluation.evaluator_config import (
    build_evaluator_config,
)
from promptguard.infrastructure.llm.errors import describe_provider_error
from promptguard.infrastructure.notifications.slack import NoopNotifier
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.interfaces.api.routes_calibration import (
    create_calibration_router,
)
from promptguard.interfaces.api.routes_runs import (
    create_baselines_router,
    create_runs_router,
)
from promptguard.interfaces.api.runner import RunManager, RunWiring
from promptguard.interfaces.api.schemas import (
    ConfigResponse,
    DatasetSummaryResponse,
    HealthResponse,
    ProviderPoolEntry,
    ReadinessResponse,
    RunCreateRequest,
)

logger = logging.getLogger("promptguard.api")

MAX_BODY_BYTES = 1_000_000
API_PREFIX = "/api/v1"


class BodySizeLimitMiddleware:
    """Reject oversized mutating requests before they reach handlers."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        """Hold the max body size for later comparison."""
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("method", "GET") in {
            "POST", "PUT", "PATCH",
        }:
            declared = 0
            for name, value in scope.get("headers", []):
                if name == b"content-length":
                    try:
                        declared = int(value)
                    except ValueError:
                        declared = self._max_bytes + 1
                    break
            if declared > self._max_bytes:
                response = JSONResponse(
                    status_code=413,
                    content=error_payload(
                        "payload_too_large",
                        "Request body exceeds the configured size limit.",
                    ),
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


class RequestContextMiddleware:
    """Attach a request id and one structured access-log entry."""

    def __init__(self, app: ASGIApp) -> None:
        """Keep the wrapped app."""
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope["state"] = {"request_id": request_id}
        start = datetime.now(UTC)

        async def send_with_request_id(message: Any) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "api.request method=%s path=%s request_id=%s elapsed_ms=%.1f",
                scope.get("method", "-"),
                scope.get("path", "-"),
                request_id,
                (datetime.now(UTC) - start).total_seconds() * 1000,
            )


def error_payload(
    code: str,
    message: str,
    request_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical error envelope body."""
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "details": details,
        },
    }


class ApiHttpError(HTTPException):
    """HTTP error carrying an API code for the envelope handler."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        """Compose the underlying HTTPException."""
        super().__init__(status_code=status_code, detail=message)
        self.api_code = code


def bearer_token_of(request: Request) -> str | None:
    """Parse an Authorization: Bearer header when present."""
    header = request.headers.get("Authorization")
    if header is None:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token or None


async def authorize_mutation(request: Request) -> None:
    """Require the configured API token for mutating operations."""
    settings = get_settings()
    expected = getattr(settings, "api_token", None)
    if not expected:
        return
    if not secrets.compare_digest(
        (bearer_token_of(request) or "").encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise ApiHttpError(403, "not_authorized", "Valid API token required.")


def compose_run_wiring(request: RunCreateRequest) -> RunWiring:
    """Build everything required for one evaluation run (ADR-0003)."""
    settings = get_settings()
    run_service, system_prompt, test_cases, eval_config = (
        build_default_run_service(
            prompt_version=request.prompt_version,
            runs_dir=PROJECT_ROOT / "runs",
            notifier=NoopNotifier(),
            settings=settings,
        )
    )
    return RunWiring(
        run_service=run_service,
        system_prompt=system_prompt,
        test_cases=test_cases,
        model=settings.nvidia_model,
        eval_config=eval_config,
    )


def bootstrap_config(settings: Any) -> BootstrapConfig:
    """Statistical bootstrap settings for the API runner."""
    return BootstrapConfig(
        seed=settings.bootstrap_seed,
        resamples=settings.bootstrap_resamples,
        confidence_level=settings.bootstrap_confidence_level,
    )


def _safe_errors(
    errors: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Validation errors without non-serializable exception contexts."""
    return [
        {key: value for key, value in error.items() if key != "ctx"}
        for error in errors
    ]


def create_app() -> FastAPI:
    """Create the PromptGuard API application."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        logger.info("promptguard api starting")
        yield
        manager = getattr(application.state, "run_manager", None)
        if manager is not None:
            await manager.shutdown(timeout=10.0)
        logger.info("promptguard api stopped; in-flight runs drained")

    app = FastAPI(
        title="PromptGuard API",
        version="v1",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    settings = get_settings()
    origins = [
        origin.strip()
        for origin in str(getattr(settings, "api_cors_origins", "")).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=MAX_BODY_BYTES)
    app.add_middleware(RequestContextMiddleware)

    app.state.settings_error = None
    try:
        get_settings()
        app.state.settings_configured = True
    except Exception as exc:  # noqa: BLE001
        app.state.settings_configured = False
        app.state.settings_error = describe_provider_error(exc, None)

    runs_dir = PROJECT_ROOT / "runs"
    app.state.runs_dir = runs_dir
    app.state.run_repository = JsonEvaluationRunRepository(runs_dir)
    app.state.run_manager = RunManager(
        max_concurrency=getattr(settings, "api_max_concurrent_runs", 1),
        evaluation_timeout_seconds=settings.evaluation_timeout_seconds,
        bootstrap=(
            bootstrap_config(settings)
            if app.state.settings_configured
            else None
        ),
        wiring=compose_run_wiring,
    )
    app.state.calibration = CalibrationDraftService(
        PROJECT_ROOT / "dataset" / "calibration" / "v1",
    )

    @app.exception_handler(HTTPException)
    async def http_error_handler(
        _request: Request, exc: HTTPException,
    ) -> JSONResponse:
        body = error_payload(
            getattr(exc, "api_code", "http_error"),
            str(exc.detail),
            request_id=getattr(_request.state, "request_id", None),
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(ValidationError)
    async def validation_error_handler(
        _request: Request, exc: ValidationError,
    ) -> JSONResponse:
        body = error_payload(
            "invalid_request",
            "Request payload failed validation.",
            details={"errors": _safe_errors(exc.errors()[:10])},
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        _request: Request, exc: RequestValidationError,
    ) -> JSONResponse:
        body = error_payload(
            "invalid_request",
            "Request failed parameter validation.",
            details={"errors": _safe_errors(exc.errors()[:10])},
        )
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request, exc: Exception,
    ) -> JSONResponse:
        logger.exception("unhandled api error: %s", exc)
        body = error_payload("internal_error", "Internal server error.")
        return JSONResponse(status_code=500, content=body)

    # -- meta ----------------------------------------------------------- #

    @app.get(f"{API_PREFIX}/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        """Liveness: the process answers."""
        return HealthResponse()

    @app.get(f"{API_PREFIX}/readiness", response_model=ReadinessResponse)
    async def readiness(
        request: Request,
    ) -> ReadinessResponse:
        """Readiness: configuration, dataset, writable runs dir, draft."""
        checks: dict[str, bool] = {}
        issues: list[str] = []
        settings_ready = bool(getattr(request.app.state, "settings_configured", False))
        checks["settings"] = settings_ready
        if request.app.state.settings_error:
            issues.append(f"settings: {request.app.state.settings_error}")
        dataset_path = PROJECT_ROOT / "dataset" / "v1" / "golden_dataset.json"
        checks["dataset_v1"] = dataset_path.is_file()
        if not dataset_path.is_file():
            issues.append("dataset v1 golden dataset is missing")
        runs_dir = request.app.state.runs_dir
        try:
            runs_dir.mkdir(parents=True, exist_ok=True)
            probe = runs_dir / ".readiness-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks["runs_dir_writable"] = True
        except OSError as exc:
            checks["runs_dir_writable"] = False
            issues.append(f"runs dir not writable: {exc}")
        checks["calibration_draft"] = (
            request.app.state.calibration.overview(
                include_strict_validation=False,
            ).draft_state
            == "prepared_final_draft"
        )
        if not checks["calibration_draft"]:
            issues.append("calibration draft workbook is missing")
        with_token = bool(getattr(settings, "api_token", None))
        if not with_token:
            issues.append(
                "API_TOKEN is not configured; mutating endpoints are open. "
                "Set a token (>=12 chars) before exposing this deployment "
                "beyond localhost."
            )
        return ReadinessResponse(
            ready=all(checks.values()),
            checks=checks,
            auth_mode="token" if with_token else "disabled",
            issues=issues,
        )

    @app.get(f"{API_PREFIX}/config", response_model=ConfigResponse)
    async def config() -> ConfigResponse:
        """Server-side configuration; never any credential values."""
        settings = get_settings()
        evaluator = build_evaluator_config(settings)
        pool = getattr(settings, "provider_pool", None) or []
        datasets: dict[str, int] = {}
        for version in ("v1",):
            path = PROJECT_ROOT / "dataset" / version / "golden_dataset.json"
            count = 0
            if path.is_file():
                try:
                    count = len(JsonDatasetRepository(path).load())
                except Exception:  # noqa: BLE001 - reported as zero
                    count = 0
            datasets[version] = count
        return ConfigResponse(
            candidate_model=settings.nvidia_model,
            candidate_base_url=str(settings.nvidia_base_url).rstrip("/"),
            judge_model=str(evaluator.get("judge_model", "")),
            judge_base_url=str(evaluator.get("judge_base_url", "")),
            request_timeout_seconds=settings.nvidia_request_timeout_seconds,
            min_request_interval_seconds=(
                settings.nvidia_min_request_interval_seconds
            ),
            evaluation_timeout_seconds=settings.evaluation_timeout_seconds,
            bootstrap={
                "seed": settings.bootstrap_seed,
                "resamples": settings.bootstrap_resamples,
                "confidence_level": settings.bootstrap_confidence_level,
            },
            provider_pool=[
                ProviderPoolEntry(name=spec.name, model=spec.model)
                for spec in pool
            ],
            prompt_versions=["v1"],
            datasets=datasets,
            credentials_in_identities=True,
        )

    @app.get(
        f"{API_PREFIX}/datasets/{{prompt_version}}",
        response_model=DatasetSummaryResponse,
    )
    async def dataset_summary(
        prompt_version: Annotated[
            str, FastApiPath(pattern=r"^[A-Za-z0-9._-]+$"),
        ],
    ) -> DatasetSummaryResponse:
        """Golden dataset identity and composition counts."""
        path = (
            PROJECT_ROOT / "dataset" / prompt_version / "golden_dataset.json"
        )
        if not path.is_file():
            raise ApiHttpError(
                404,
                "dataset_not_found",
                f"Dataset {prompt_version} not found.",
            )
        try:
            cases = JsonDatasetRepository(path).load()
        except Exception as exc:
            raise ApiHttpError(
                503,
                "dataset_unreadable",
                f"Dataset {prompt_version} failed to load: {exc}",
            ) from exc
        counts: dict[str, int] = {}
        for case in cases:
            counts[case.expected_category] = (
                counts.get(case.expected_category, 0) + 1
            )
        return DatasetSummaryResponse(
            prompt_version=prompt_version,
            case_count=len(cases),
            case_count_by_category=counts,
        )

    # -- domain routers -------------------------------------------------- #

    app.include_router(
        create_runs_router(authorize_mutation),
        prefix=API_PREFIX,
    )
    app.include_router(create_baselines_router(), prefix=API_PREFIX)
    app.include_router(create_calibration_router(), prefix=API_PREFIX)

    return app


__all__ = [
    "API_PREFIX",
    "ApiHttpError",
    "authorize_mutation",
    "bearer_token_of",
    "compose_run_wiring",
    "create_app",
    "error_payload",
]
