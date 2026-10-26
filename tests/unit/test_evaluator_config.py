"""Unit tests for evaluator identity and rubric fingerprinting."""

from types import SimpleNamespace

from promptguard.infrastructure.evaluation.evaluator_config import (
    DEFAULT_NVIDIA_BASE_URL,
    build_evaluator_config,
    deepeval_version,
)
from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_rubric_sha256,
)


def test_evaluator_config_contains_non_secret_judge_identity() -> None:
    """The fingerprint metadata should identify the judge without secrets."""
    settings = SimpleNamespace(
        nvidia_model="nvidia/llama-3.1-nemotron-70b-instruct",
        nvidia_base_url="https://integrate.api.nvidia.com/v1/",
    )

    config = build_evaluator_config(settings)  # type: ignore[arg-type]

    assert config["judge_provider"] == "nvidia"
    assert config["judge_model"] == settings.nvidia_model
    assert config["judge_base_url"] == "https://integrate.api.nvidia.com/v1"
    assert config["judge_rubric_version"] == SUMMARY_QUALITY_RUBRIC_VERSION
    assert config["judge_rubric_sha256"] == summary_quality_rubric_sha256()
    assert config["deepeval_version"] == deepeval_version()
    assert "api_key" not in config


def test_evaluator_config_defaults_missing_base_url() -> None:
    """Older settings objects without a base URL remain supported."""
    settings = SimpleNamespace(nvidia_model="judge-model")

    config = build_evaluator_config(settings)  # type: ignore[arg-type]

    assert config["judge_base_url"] == DEFAULT_NVIDIA_BASE_URL


def test_rubric_hash_is_stable_and_content_addressed() -> None:
    """The rubric hash should be deterministic and non-empty."""
    first = summary_quality_rubric_sha256()
    second = summary_quality_rubric_sha256()

    assert first == second
    assert len(first) == 64
    assert all(character in "0123456789abcdef" for character in first)
