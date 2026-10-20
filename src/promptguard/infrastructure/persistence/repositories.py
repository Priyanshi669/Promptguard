"""JSON-file persistence for complete evaluation runs."""

import json
from pathlib import Path

from promptguard.application.contracts import EvaluationRunRepository
from promptguard.domain.models import EvaluationReport


class JsonEvaluationRunRepository(EvaluationRunRepository):
    """Persist every evaluation report as one JSON file."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def save(self, report: EvaluationReport) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        self._path(report.run_id).write_text(
            report.model_dump_json(indent=2), encoding="utf-8",
        )

    def get(self, run_id: str) -> EvaluationReport:
        path = self._path(run_id)
        if not path.exists():
            raise FileNotFoundError(f"Evaluation run not found: {run_id}")
        try:
            return EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid evaluation run: {run_id}") from exc

    def get_latest(self) -> EvaluationReport | None:
        reports = self.list_runs()
        return reports[-1] if reports else None

    def list_runs(self) -> list[EvaluationReport]:
        if not self._directory.exists():
            return []
        reports = [self.get(path.stem) for path in self._directory.glob("*.json")]
        return sorted(reports, key=lambda report: (report.timestamp, report.run_id))

    def _path(self, run_id: str) -> Path:
        return self._directory / f"{run_id}.json"
