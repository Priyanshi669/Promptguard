"""Offline unit tests for the PromptGuard HTTP API (ADR-0003)."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from promptguard.application.calibration_draft_service import (
    CalibrationDraftService,
)
from promptguard.application.reporting_service import ReportingService
from promptguard.domain.models import (
    EvaluationReport,
    EvaluationResult,
)
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.interfaces.api.app import (
    ApiHttpError,
    authorize_mutation,
    create_app,
)
from promptguard.interfaces.api.runner import RunManager, RunWiring

WORKBOOK_COLUMNS = [
    "id",
    "annotation_pass",
    "is_double_annotated",
    "source_split",
    "category",
    "source_category",
    "inclusion_status",
    "source_input",
    "expected_summary",
    "actual_summary",
    "actual_summary_is_ai_candidate",
    "provisional_reference",
    "reference_is_human_validated",
    "human_score",
    "confidence",
    "notes",
    "annotator_id",
    "annotated_at",
]


def build_result(test_case_id: str = "TC-001") -> EvaluationResult:
    """One valid evaluation result."""
    return EvaluationResult(
        test_case_id=test_case_id,
        expected_category="billing",
        actual_category="billing",
        expected_summary="Customer reports a duplicate charge.",
        actual_summary="Customer was charged twice.",
        category_match=True,
        summary_score=4.0,
        latency_ms=250.0,
        input_tokens=20,
        output_tokens=12,
    )


class StubRunService:
    """Test double mirroring the EvaluationRunService contract."""

    def __init__(
        self,
        report: EvaluationReport | None,
        delay: float = 0.0,
        repository: JsonEvaluationRunRepository | None = None,
    ) -> None:
        self._report = report
        self._delay = delay
        self._repository = repository
        self.calls: list[dict[str, Any]] = []

    async def run(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._report is None:
            raise RuntimeError("stub pipeline failure")
        if self._repository is not None:
            self._repository.save(self._report)
        outcome = type("StubOutcome", (), {})()
        outcome.report = self._report
        return outcome


def make_wiring(
    stub_service: StubRunService,
    test_cases: list[object] | None = None,
) -> Callable[[Any], Any]:
    """A wiring callable that never touches providers or the filesystem."""

    def wire(_request: Any) -> RunWiring:
        return RunWiring(
            run_service=stub_service,
            system_prompt="stub prompt",
            test_cases=list(test_cases or []),
            model="stub-model",
            eval_config={"stub": True},
        )

    return wire


@pytest.fixture()
def stub_report() -> EvaluationReport:
    """A persisted-shape report for success-path jobs."""
    return ReportingService().build_report(
        [build_result()], "v1", "stub-model", "golden-v1",
    )


def offline_app(
    tmp_path: Path,
    stub: StubRunService,
    test_cases: list[object] | None = None,
) -> TestClient:
    """App instance with offline state and a stub runner wiring."""
    app = create_app()
    app.state.run_repository = JsonEvaluationRunRepository(tmp_path / "runs")
    app.state.calibration = CalibrationDraftService(
        tmp_path / "dataset" / "calibration" / "v1",
    )
    app.state.run_manager = RunManager(
        wiring=make_wiring(stub, test_cases),
    )
    return TestClient(app)


@pytest.fixture()
def client(
    tmp_path: Path, stub_report: EvaluationReport,
):
    """Success-path app: stub pipeline returns and persists a report."""
    repository = JsonEvaluationRunRepository(tmp_path / "runs")
    stub = StubRunService(stub_report, repository=repository)
    http = offline_app(tmp_path, stub)
    with http:
        yield http, stub


@pytest.fixture()
def failing_client(tmp_path: Path):
    """Error-path app: stub pipeline always raises."""
    stub = StubRunService(None)
    http = offline_app(tmp_path, stub)
    with http:
        yield http, stub


def poll_job(
    client: TestClient, job_id: str, timeout: float = 5.0,
) -> dict[str, Any]:
    """Wait for a terminal job status without asserting on timing."""
    deadline = time.monotonic() + timeout
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/run-jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        time.sleep(0.05)
    return payload


def assert_envelope(response_json: dict[str, Any]) -> dict[str, Any]:
    """Every error response must use the canonical envelope."""
    assert set(response_json) == {"error"}
    error = response_json["error"]
    assert {"code", "message"} <= set(error)
    return error


# --------------------------------------------------------------------- #
# Meta endpoints
# --------------------------------------------------------------------- #


def test_health_is_public(client: tuple[TestClient, StubRunService]) -> None:
    http, _stub = client
    response = http.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_reports_structured_checks(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/readiness")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["settings"] is True
    assert body["checks"]["dataset_v1"] is True
    assert body["checks"]["runs_dir_writable"] is True
    assert body["checks"]["calibration_draft"] is False
    assert body["ready"] is False
    assert any("calibration" in issue for issue in body["issues"])


def test_readiness_flags_missing_token(
    client: tuple[TestClient, StubRunService],
) -> None:
    """Without API_TOKEN the readiness report must warn, not lie."""
    http, _stub = client
    body = http.get("/api/v1/readiness").json()
    assert body["auth_mode"] == "disabled"
    assert any("API_TOKEN" in issue for issue in body["issues"])


def test_config_has_no_credentials(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/config")
    assert response.status_code == 200
    assert response.json()["candidate_model"]
    text = response.text
    for secret in ("nvidia_api_key", "NVIDIA_KEY", "api_key"):
        assert secret not in text.lower()


def test_dataset_summary_counts_by_category(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/datasets/v1")
    assert response.status_code == 200
    body = response.json()
    assert body["prompt_version"] == "v1"
    assert body["case_count"] > 0
    assert sum(body["case_count_by_category"].values()) == body["case_count"]


def test_dataset_unknown_version_is_404_envelope(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/datasets/v999")
    assert response.status_code == 404
    error = assert_envelope(response.json())
    assert error["code"] == "dataset_not_found"


# --------------------------------------------------------------------- #
# Run lifecycle
# --------------------------------------------------------------------- #


def test_create_run_returns_queued_job(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.post("/api/v1/runs", json={"prompt_version": "v1"})
    assert response.status_code == 202
    body = response.json()
    assert body["status"] in {"queued", "running"}
    assert body["duplicate"] is False
    assert body["job_id"]


def test_run_completes_with_report_and_status(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    created = http.post("/api/v1/runs", json={}).json()
    job = poll_job(http, created["job_id"])
    assert job["status"] == "inconclusive"
    assert job["report_run_id"]
    detail = http.get(f"/api/v1/runs/{job['report_run_id']}")
    assert detail.status_code == 200
    report = detail.json()["report"]
    assert report["run_id"] == job["report_run_id"]
    assert report["model"] == "stub-model"


def test_failing_pipeline_sets_error_status(
    failing_client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = failing_client
    created = http.post("/api/v1/runs", json={}).json()
    job = poll_job(http, created["job_id"])
    assert job["status"] == "error"
    assert job["error_code"] == "run_failed"
    assert "stub pipeline failure" in job["error_message"]
    assert job["report_run_id"] is None


def test_duplicate_busy_request_is_deduplicated(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, stub = client
    stub._delay = 0.3
    first = http.post("/api/v1/runs", json={}).json()
    second = http.post("/api/v1/runs", json={}).json()
    assert second["duplicate"] is True
    assert second["job_id"] == first["job_id"]
    poll_job(http, first["job_id"])
    third = http.post("/api/v1/runs", json={}).json()
    assert third["duplicate"] is False
    assert third["job_id"] != first["job_id"]


def test_different_requests_are_not_deduplicated(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, stub = client
    stub._delay = 0.3
    first = http.post("/api/v1/runs", json={}).json()
    second = http.post("/api/v1/runs", json={"limit": 1}).json()
    assert second["duplicate"] is False
    assert second["job_id"] != first["job_id"]
    poll_job(http, first["job_id"])
    poll_job(http, second["job_id"])


def test_running_job_can_be_cancelled(tmp_path: Path) -> None:
    """A running job can be cancelled; persisted reports stay intact."""
    stub = StubRunService(None, delay=2.0)
    http = offline_app(tmp_path, stub)
    with http:
        created = http.post("/api/v1/runs", json={}).json()
        time.sleep(0.1)
        response = http.delete(f"/api/v1/run-jobs/{created['job_id']}")
        assert response.status_code == 200
        body = response.json()
        assert body["cancelled"] is True
        assert body["status"] == "cancelled"


def test_cancel_unknown_job_is_404(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.delete("/api/v1/run-jobs/does-not-exist")
    assert response.status_code == 404
    error = assert_envelope(response.json())
    assert error["code"] == "job_not_found"


def test_job_status_unknown_is_404(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/run-jobs/does-not-exist")
    assert response.status_code == 404
    error = assert_envelope(response.json())
    assert error["code"] == "job_not_found"


def test_unknown_run_is_404(client: tuple[TestClient, StubRunService]) -> None:
    http, _stub = client
    response = http.get("/api/v1/runs/missing-run")
    assert response.status_code == 404
    error = assert_envelope(response.json())
    assert error["code"] == "run_not_found"


def test_run_export_json_and_text(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    created = http.post("/api/v1/runs", json={}).json()
    job = poll_job(http, created["job_id"])
    run_id = job["report_run_id"]
    json_response = http.get(f"/api/v1/runs/{run_id}/export?format=json")
    assert json_response.status_code == 200
    assert json_response.json()["run_id"] == run_id
    text_response = http.get(f"/api/v1/runs/{run_id}/export?format=text")
    assert text_response.status_code == 200
    assert "stub-model" in text_response.text


def test_run_export_invalid_format_is_422(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/runs/missing/export?format=csv")
    assert response.status_code == 422
    assert_envelope(response.json())


def test_run_list_paginates_newest_first(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    created = http.post("/api/v1/runs", json={}).json()
    job = poll_job(http, created["job_id"])
    body = http.get("/api/v1/runs?offset=0&limit=10").json()
    assert body["total"] >= 1
    assert body["items"][0]["run_id"] == job["report_run_id"]


def test_limited_run_request_applies_limit(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, stub = client
    http.app.state.run_manager._wiring = make_wiring(
        stub, test_cases=["a", "b"],
    )
    http.post("/api/v1/runs", json={"limit": 1})
    deadline = time.monotonic() + 5.0
    while not stub.calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stub.calls
    assert len(stub.calls[0]["test_cases"]) == 1


# --------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------- #


def test_baselines_list_is_read_only(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    created = http.post("/api/v1/runs", json={}).json()
    poll_job(http, created["job_id"])
    response = http.get("/api/v1/baselines")
    body = response.json()
    assert response.status_code == 200
    assert body["total"] >= 1
    item = body["items"][0]
    assert item["run_id"]
    assert "critical_count" in item
    assert item["has_thresholds"] is False


def test_baselines_has_no_write_method(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.post("/api/v1/baselines", json={})
    assert response.status_code == 405


# --------------------------------------------------------------------- #
# Calibration (read-only, ADR-0002)
# --------------------------------------------------------------------- #


def write_draft(tmp_path: Path) -> None:
    """Create a minimal draft workbook + manifest + candidates."""
    calibration = tmp_path / "dataset" / "calibration" / "v1"
    calibration.mkdir(parents=True)
    manifest = {
        "rubric_version": "v1",
        "rubric_sha256": "0" * 64,
        "sample_count": 1,
        "examples": [
            {
                "id": "banking77-001",
                "category": "card_arrival",
                "category_counts": {},
            },
        ],
    }
    (calibration / "sample_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    header = ",".join(WORKBOOK_COLUMNS)
    row = ["banking77-001"] + [""] * (len(WORKBOOK_COLUMNS) - 1)
    row[1] = "primary"
    row[4] = "card_arrival"
    row[5] = "banking"
    row[6] = "included"
    row[7] = "input text"
    row[8] = "reference summary"
    row[9] = "candidate summary"
    row[10] = "TRUE"
    row[11] = "TRUE"
    (calibration / "annotation_workbook.csv").write_text(
        header + "\n" + ",".join(row), encoding="utf-8",
    )
    (calibration / "double_annotation_ids.json").write_text(
        json.dumps({"ids": []}), encoding="utf-8",
    )
    candidates = {
        "records": [
            {
                "id": "banking77-001",
                "summary": "candidate summary",
                "run_id": "run-1",
                "model": "stub-model",
                "generated_at": "2026-10-01T00:00:00Z",
            },
        ],
    }
    (calibration / "candidate_outputs.json").write_text(
        json.dumps(candidates), encoding="utf-8",
    )


def calibrated_app(tmp_path: Path) -> TestClient:
    """App whose calibration service reads a tmp draft workspace."""
    app = create_app()
    write_draft(tmp_path)
    app.state.calibration = CalibrationDraftService(
        tmp_path / "dataset" / "calibration" / "v1",
    )
    return TestClient(app)


def test_calibration_missing_draft_is_404(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/calibration/draft")
    assert response.status_code == 404
    error = assert_envelope(response.json())
    assert error["code"] == "draft_not_found"


def test_calibration_overview_read_only(tmp_path: Path) -> None:
    with calibrated_app(tmp_path) as http:
        response = http.get("/api/v1/calibration/draft")
        assert response.status_code == 200
        overview = response.json()["overview"]
        assert overview["draft_state"] == "prepared_final_draft"
        assert overview["total_rows"] == 1
        assert overview["scored_rows"] == 0
        assert overview["candidate_run_ids"] == ["run-1"]


def test_calibration_rows_are_previews(tmp_path: Path) -> None:
    with calibrated_app(tmp_path) as http:
        response = http.get("/api/v1/calibration/draft/rows")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        row = body["items"][0]
        assert row["id"] == "banking77-001"
        assert row["actual_summary"] == "candidate summary"


def test_draft_is_not_mutable_via_api(tmp_path: Path) -> None:
    """No POST/PUT/DELETE route exists for the draft (ADR-0002)."""
    with calibrated_app(tmp_path) as http:
        response = http.post(
            "/api/v1/calibration/draft", json={"human_score": 5},
        )
        assert response.status_code == 405


# --------------------------------------------------------------------- #
# Auth and error envelope
# --------------------------------------------------------------------- #


def test_mutation_auth_dependency_can_reject(
    client: tuple[TestClient, StubRunService],
) -> None:
    """With the dependency denied, POST /runs is 403 enveloped."""
    http, _stub = client
    app = http.app

    def deny(_request: Request) -> None:
        raise ApiHttpError(403, "not_authorized", "Valid API token required.")

    app.dependency_overrides[authorize_mutation] = deny
    try:
        response = http.post("/api/v1/runs", json={})
        assert response.status_code == 403
        error = assert_envelope(response.json())
        assert error["code"] == "not_authorized"
    finally:
        app.dependency_overrides.pop(authorize_mutation, None)


def test_invalid_run_request_is_enveloped(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.post("/api/v1/runs", json={"limit": 0})
    assert response.status_code == 422
    error = assert_envelope(response.json())
    assert error["code"] == "invalid_request"


def test_invalid_path_param_is_enveloped(
    client: tuple[TestClient, StubRunService],
) -> None:
    http, _stub = client
    response = http.get("/api/v1/datasets/has%20space")
    assert response.status_code == 422
    assert_envelope(response.json())


def test_lifespan_drains_run_manager_on_shutdown() -> None:
    """Graceful stop drains in-flight runs through RunManager.shutdown."""
    import promptguard.interfaces.api.app as app_module

    app = create_app()

    class SpyRunManager:
        def __init__(self) -> None:
            self.timeouts: list[float] = []

        async def shutdown(self, timeout: float = 10.0) -> None:
            self.timeouts.append(timeout)

    spy = SpyRunManager()
    app.state.run_manager = spy
    with TestClient(app):
        assert spy.timeouts == []
    assert spy.timeouts == [10.0]

    # Sanity: the real manager exposes the awaited signature (typing seam).
    assert hasattr(app_module.RunManager, "shutdown")
