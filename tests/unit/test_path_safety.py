"""Unit tests for identifier validation and repository path boundaries."""

from pathlib import Path

import pytest

from promptguard.infrastructure.path_safety import (
    resolve_identifier_path,
    validate_identifier,
)
from promptguard.infrastructure.prompts.yaml_repository import PromptRepository


@pytest.mark.parametrize(
    "identifier",
    ["v1", "v1.2", "run-123", "run_123", "0f0f0f0f-0000-0000-0000-000000000000"],
)
def test_validate_identifier_accepts_safe_values(identifier: str) -> None:
    assert validate_identifier(identifier, kind="identifier") == identifier


@pytest.mark.parametrize(
    "identifier",
    [
        "",
        "   ",
        ".",
        "..",
        "../outside",
        "..\\outside",
        "/outside",
        "C:\\outside",
        "nested/run",
        "nested\\run",
        ".hidden",
        "run name",
        "run\x00name",
    ],
)
def test_validate_identifier_rejects_unsafe_values(identifier: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        validate_identifier(identifier, kind="identifier")


def test_resolve_identifier_path_stays_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.mkdir()

    path = resolve_identifier_path(root, "run-123", ".json", kind="run ID")

    assert path == (root / "run-123.json").resolve()


def test_resolve_identifier_path_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = root / "run.json"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(ValueError, match="inside"):
        resolve_identifier_path(root, "run", ".json", kind="run ID")


def test_prompt_repository_loads_valid_version(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "v1.yaml").write_text(
        "system_prompt: hello\n", encoding="utf-8",
    )

    assert PromptRepository(prompts_dir).load("v1") == {
        "system_prompt": "hello",
    }


def test_prompt_repository_rejects_parent_directory_traversal(
    tmp_path: Path,
) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    outside = tmp_path / "outside.yaml"
    outside.write_text("system_prompt: secret\n", encoding="utf-8")

    with pytest.raises(ValueError, match="prompt version"):
        PromptRepository(prompts_dir).load("../outside")
