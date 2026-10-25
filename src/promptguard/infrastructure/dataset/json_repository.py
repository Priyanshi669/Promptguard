"""JSON dataset repository."""

import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from promptguard.domain.models import GoldenTestCase


class JsonDatasetRepository:
    """Load golden test cases from a JSON file."""

    def __init__(self, path: Path) -> None:
        """Initialize the repository."""
        self._path = path

    def load(self) -> list[GoldenTestCase]:
        """Load and validate all golden test cases."""
        if not self._path.exists():
            raise FileNotFoundError(
                f"Golden dataset not found: {self._path}"
            )

        try:
            data = json.loads(
                self._path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Invalid golden dataset JSON"
            ) from exc

        if not isinstance(data, list):
            raise TypeError(
                "Golden dataset must contain a JSON array"
            )

        try:
            cases = [
                GoldenTestCase.model_validate(item)
                for item in data
            ]
        except ValidationError as exc:
            raise ValueError(
                "Golden dataset contains invalid test case data"
            ) from exc

        duplicates = sorted(
            case_id
            for case_id, count in Counter(case.id for case in cases).items()
            if count > 1
        )
        if duplicates:
            raise ValueError(
                "Golden dataset contains duplicate test case ids: "
                f"{duplicates}"
            )
        return cases
