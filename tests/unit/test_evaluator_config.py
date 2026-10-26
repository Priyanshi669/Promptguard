"""Unit tests for evaluator identity and rubric fingerprinting."""

from types import SimpleNamespace

import pytest

from promptguard.infrastructure.evaluation import rubric as rubric_module
from promptguard.infrastructure.evaluation.deepeval_metrics import (
    DeepEvalSummaryMetric,
)
from promptguard.infrastructure.evaluation.evaluator_config import (
    DEFAULT_NVIDIA_BASE_URL,
    build_evaluator_config,
    deepeval_version,
)
from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_EVALUATION_STEPS,
    SUMMARY_QUALITY_RUBRIC_VERSION,
    summary_quality_evaluation_steps_sha256,
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


def test_evaluator_config_contains_pinned_evaluation_steps() -> None:
    """Step wording and its hash must be part of the evaluator identity."""
    settings = SimpleNamespace(nvidia_model="judge-model")

    config = build_evaluator_config(settings)  # type: ignore[arg-type]

    assert config["evaluation_steps"] == list(SUMMARY_QUALITY_EVALUATION_STEPS)
    assert config["evaluation_steps_sha256"] == (
        summary_quality_evaluation_steps_sha256()
    )
    assert len(config["evaluation_steps_sha256"]) == 64
    assert all(
        character in "0123456789abcdef"
        for character in config["evaluation_steps_sha256"]
    )
    assert "api_key" not in config


def test_evaluator_steps_hash_is_stable_and_content_addressed() -> None:
    """The step hash should be deterministic and non-empty."""
    first = summary_quality_evaluation_steps_sha256()
    second = summary_quality_evaluation_steps_sha256()

    assert first == second
    assert len(first) == 64
    assert all(character in "0123456789abcdef" for character in first)


def test_evaluator_config_hash_changes_when_steps_change(monkeypatch) -> None:
    """The fingerprint must reflect changes to the operational steps."""
    settings = SimpleNamespace(nvidia_model="judge-model")
    baseline = build_evaluator_config(settings)  # type: ignore[arg-type]

    original_steps = list(SUMMARY_QUALITY_EVALUATION_STEPS)
    monkeypatch.setattr(
        rubric_module,
        "SUMMARY_QUALITY_EVALUATION_STEPS",
        original_steps[:-1],
    )
    changed = build_evaluator_config(settings)  # type: ignore[arg-type]

    assert changed["evaluation_steps"] == original_steps[:-1]
    assert changed["evaluation_steps_sha256"] != (baseline["evaluation_steps_sha256"])
    # Other identity fields are preserved.
    assert changed["judge_rubric_sha256"] == baseline["judge_rubric_sha256"]
    assert changed["judge_model"] == baseline["judge_model"]


def test_existing_rubric_identity_remains_in_fingerprint() -> None:
    """Rubric identity fields must remain present after adding steps."""
    settings = SimpleNamespace(nvidia_model="judge-model")

    config = build_evaluator_config(settings)  # type: ignore[arg-type]

    assert config["judge_rubric_version"] == SUMMARY_QUALITY_RUBRIC_VERSION
    assert config["judge_rubric_sha256"] == summary_quality_rubric_sha256()


@pytest.mark.asyncio
async def test_pinned_steps_are_identical_across_metric_instances() -> None:
    """Two independent metric instances must receive the same step wording."""
    from unittest.mock import MagicMock, patch

    captured_kwargs: list[dict] = []

    def capture_kwargs(**kwargs):
        captured_kwargs.append(dict(kwargs))
        return MagicMock()

    for _ in range(2):
        with (
            patch(
                "promptguard.infrastructure.evaluation.deepeval_metrics.NVIDIAJudge",
                return_value=MagicMock(),
            ),
            patch(
                "promptguard.infrastructure.evaluation.deepeval_metrics.GEval",
                side_effect=capture_kwargs,
            ),
        ):
            DeepEvalSummaryMetric()

    assert len(captured_kwargs) == 2
    assert (
        captured_kwargs[0]["evaluation_steps"] == captured_kwargs[1]["evaluation_steps"]
    )

    settings = SimpleNamespace(nvidia_model="judge-model")
    config = build_evaluator_config(settings)  # type: ignore[arg-type]
    assert captured_kwargs[0]["evaluation_steps"] == config["evaluation_steps"]

    # Stable criteria, params, and metric identity must also be reproduced.
    for field in ("name", "criteria", "threshold"):
        assert captured_kwargs[0][field] == captured_kwargs[1][field]


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
