"""Regression tests for the Docker build context."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dockerignore_excludes_sensitive_and_generated_paths() -> None:
    """Local secrets and generated artifacts must not enter the image."""
    dockerignore = PROJECT_ROOT / ".dockerignore"
    assert dockerignore.is_file()

    patterns = {
        line.strip()
        for line in dockerignore.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    assert {
        ".git",
        ".venv",
        ".env",
        ".env.*",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "runs",
        "reports",
    } <= patterns
