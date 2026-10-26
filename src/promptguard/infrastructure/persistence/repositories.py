"""JSON-file persistence for complete evaluation runs."""

import json
from pathlib import Path

from promptguard.application.contracts import EvaluationRunRepository
from promptguard.domain.models import EvaluationReport
from promptguard.infrastructure.path_safety import resolve_identifier_path


class JsonEvaluationRunRepository(EvaluationRunRepository):
    """Persist every evaluation report as one JSON file."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._skipped_runs: list[str] = []

    @property
    def skipped_runs(self) -> list[str]:
        """Return diagnostics for files skipped by the last list operation."""
        return list(self._skipped_runs)

    def save(self, report: EvaluationReport) -> None:
        path = self._path(report.run_id)
        validated = EvaluationReport.model_validate(report.model_dump())
        self._directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            validated.model_dump_json(indent=2), encoding="utf-8",
        )

    def get(self, run_id: str) -> EvaluationReport:
        path = self._path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"Evaluation run not found: {run_id}")
        try:
            report = EvaluationReport.model_validate_json(
                path.read_text(encoding="utf-8"),
            )
            if report.run_id != run_id:
                raise ValueError(
                    f"Embedded run ID {report.run_id!r} does not match "
                    f"filename {path.name!r}."
                )
            return report
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid evaluation run: {run_id}") from exc

    def get_latest(self) -> EvaluationReport | None:
        reports = self.list_runs()
        if self._skipped_runs:
            return None
        return reports[-1] if reports else None

    def list_runs(self) -> list[EvaluationReport]:
        self._skipped_runs = []
        if not self._directory.exists():
            return []
        reports: list[EvaluationReport] = []
        for path in self._directory.glob("*.json"):
            try:
                reports.append(self.get(path.stem))
            except (OSError, ValueError) as exc:
                self._skipped_runs.append(f"{path.name}: {exc}")
        return sorted(reports, key=lambda report: (report.timestamp, report.run_id))

    def _path(self, run_id: str) -> Path:
        return resolve_identifier_path(
            self._directory,
            run_id,
            ".json",
            kind="evaluation run ID",
        )
