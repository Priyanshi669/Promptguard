"""Evaluator identity captured by evaluation fingerprints."""

from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from promptguard.config import Settings
from promptguard.infrastructure.evaluation import rubric as rubric_module
from promptguard.infrastructure.evaluation.rubric import (
    SUMMARY_QUALITY_RUBRIC_VERSION,
)

JUDGE_METRIC_NAME = "deepeval-geval-summary-quality"
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"


@lru_cache
def deepeval_version() -> str:
    """Return the installed DeepEval version, if it can be determined."""
    try:
        return version("deepeval")
    except PackageNotFoundError:
        return "unavailable"


def build_evaluator_config(settings: Settings) -> dict[str, Any]:
    """Return the non-secret identity of the configured summary judge.

    When a failover pool is configured, its composition (never credentials)
    is fingerprinted too, because failover changes which provider may serve
    a request.
    """
    base_url = getattr(settings, "nvidia_base_url", DEFAULT_NVIDIA_BASE_URL)
    identity: dict[str, Any] = {
        "judge_provider": "nvidia",
        "judge_model": settings.nvidia_model,
        "judge_base_url": str(base_url).rstrip("/"),
        "judge_metric": JUDGE_METRIC_NAME,
        "judge_rubric_version": SUMMARY_QUALITY_RUBRIC_VERSION,
        "judge_rubric_sha256": rubric_module.summary_quality_rubric_sha256(),
        "evaluation_steps": list(rubric_module.SUMMARY_QUALITY_EVALUATION_STEPS),
        "evaluation_steps_sha256": (
            rubric_module.summary_quality_evaluation_steps_sha256()
        ),
        "deepeval_version": deepeval_version(),
    }
    pool = getattr(settings, "provider_pool", None)
    if pool:
        identity["judge_provider_pool"] = [
            {"name": spec.name, "model": spec.model} for spec in pool
        ]
    return identity
