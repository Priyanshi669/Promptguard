"""Unit tests for the JSON dataset repository."""

import json

import pytest

from promptguard.infrastructure.dataset.json_repository import JsonDatasetRepository


def valid_case() -> dict[str, str]:
    """Return one valid golden dataset record."""
    return {
        "id": "case-001",
        "input": "My card payment was declined.",
        "expected_category": "declined_card_payment",
        "ideal_summary": "Customer reports a declined card payment.",
        "expected_difficulty": "medium",
    }


def test_load_returns_validated_cases(tmp_path) -> None:
    """The repository should deserialize valid golden cases."""
    path = tmp_path / "golden.json"
    path.write_text(json.dumps([valid_case()]), encoding="utf-8")

    cases = JsonDatasetRepository(path).load()

    assert len(cases) == 1
    assert cases[0].id == "case-001"


def test_load_rejects_non_array_dataset(tmp_path) -> None:
    """The repository should reject a JSON object at the dataset root."""
    path = tmp_path / "golden.json"
    path.write_text(json.dumps(valid_case()), encoding="utf-8")

    with pytest.raises(TypeError, match="JSON array"):
        JsonDatasetRepository(path).load()


def test_load_rejects_invalid_case(tmp_path) -> None:
    """The repository should reject records missing required fields."""
    path = tmp_path / "golden.json"
    path.write_text(json.dumps([{"id": "case-001"}]), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid test case data"):
        JsonDatasetRepository(path).load()


@pytest.mark.parametrize(
    "field",
    ["id", "input", "expected_category", "ideal_summary"],
)
def test_load_rejects_blank_required_case_text(
    tmp_path,
    field: str,
) -> None:
    """Blank dataset evidence must fail before evaluation starts."""
    record = valid_case()
    record[field] = "   "
    path = tmp_path / "golden.json"
    path.write_text(json.dumps([record]), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid test case data"):
        JsonDatasetRepository(path).load()


def test_load_rejects_duplicate_case_ids(tmp_path) -> None:
    """Duplicate identifiers make per-case regression tracking ambiguous."""
    path = tmp_path / "golden.json"
    path.write_text(
        json.dumps([valid_case(), valid_case()]), encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate test case ids"):
        JsonDatasetRepository(path).load()


def test_load_rejects_missing_file(tmp_path) -> None:
    """The repository should report a missing dataset path."""
    with pytest.raises(FileNotFoundError, match="Golden dataset not found"):
        JsonDatasetRepository(tmp_path / "missing.json").load()
