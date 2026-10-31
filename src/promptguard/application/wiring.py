"""Shared composition of the default evaluation run service.

The CLI and the HTTP API must wire the same concrete adapters; this
module is the single composition point. It contains only composition,
no business logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from promptguard.application.comparison_service import (
    ComparisonService,
    RegressionService,
)
from promptguard.application.contracts import (
    EvaluationRunRepository,
    Notifier,
)
from promptguard.application.drift_service import DriftService
from promptguard.application.evaluation_service import EvaluationService
from promptguard.application.reporting_service import ReportingService
from promptguard.application.run_service import EvaluationRunService
from promptguard.config import Settings, get_settings
from promptguard.domain.models import GoldenTestCase
from promptguard.infrastructure.dataset.json_repository import (
    JsonDatasetRepository,
)
from promptguard.infrastructure.evaluation.deepeval_metrics import (
    DeepEvalSummaryMetric,
)
from promptguard.infrastructure.evaluation.evaluator_config import (
    build_evaluator_config,
)
from promptguard.infrastructure.llm.client import NVIDIAClient
from promptguard.infrastructure.notifications.slack import (
    NoopNotifier,
    SlackNotifier,
)
from promptguard.infrastructure.path_safety import validate_identifier
from promptguard.infrastructure.persistence.repositories import (
    JsonEvaluationRunRepository,
)
from promptguard.infrastructure.prompts.yaml_repository import (
    PromptRepository,
)
from promptguard.infrastructure.reporting.html_renderer import (
    TextReportFormatter,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def build_notifier(settings: Settings) -> Notifier:
    """Return the configured notifier from application settings.

    Unconfigured or explicitly disabled Slack settings produce the safe
    no-op notifier; evaluation runs continue to function normally. The
    webhook URL never leaves this module.
    """
    if not settings.slack_notifications_enabled:
        return NoopNotifier()
    if not settings.slack_webhook_url:
        return NoopNotifier()
    return SlackNotifier(
        settings.slack_webhook_url,
        timeout_seconds=settings.slack_timeout_seconds,
        max_attempts=settings.slack_max_attempts,
    )


def extract_system_prompt(prompt_config: dict[str, Any]) -> str:
    """Extract the system prompt from the prompt configuration."""
    system_prompt = prompt_config.get("system_prompt")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError(
            "Prompt configuration must contain a non-empty 'system_prompt'."
        )
    return system_prompt


def extract_categories(prompt_config: dict[str, Any]) -> list[str]:
    """Extract the allowed category taxonomy from prompt config."""
    categories = prompt_config.get("categories", [])
    if not isinstance(categories, list):
        return []
    return [str(category) for category in categories]


def extract_aliases(prompt_config: dict[str, Any]) -> dict[str, str]:
    """Extract category aliases from prompt config."""
    aliases = prompt_config.get("category_aliases", {})
    if not isinstance(aliases, dict):
        return {}
    return {str(key): str(value) for key, value in aliases.items()}


def dataset_path_for(prompt_version: str) -> Path:
    """Return the golden-dataset path guarded by identifier rules."""
    validate_identifier(prompt_version, kind="prompt version")
    return PROJECT_ROOT / "dataset" / prompt_version / "golden_dataset.json"


def build_default_run_service(
    prompt_version: str,
    runs_dir: Path,
    notifier: Notifier,
    settings: Settings | None = None,
    llm_client: NVIDIAClient | None = None,
    summary_metric: DeepEvalSummaryMetric | None = None,
) -> tuple[
    EvaluationRunService,
    str,
    list[GoldenTestCase],
    dict[str, Any],
]:
    """Wire the orchestrator with concrete infrastructure adapters.

    ``settings`` defaults to ``get_settings()``; ``llm_client`` and
    ``summary_metric`` default to the real provider adapters and may be
    injected for controlled tests or offline demos. Passing explicit
    settings/interfaces keeps CLI-style test seams working.
    """
    if settings is None:
        settings = get_settings()
    validate_identifier(prompt_version, kind="prompt version")
    dataset_path = dataset_path_for(prompt_version)
    prompts_dir = PROJECT_ROOT / "prompts"

    test_cases = JsonDatasetRepository(dataset_path).load()
    prompt_config = PromptRepository(prompts_dir).load(prompt_version)
    system_prompt = extract_system_prompt(prompt_config)
    categories = extract_categories(prompt_config)
    aliases = extract_aliases(prompt_config)
    eval_config: dict[str, Any] = {
        "categories": categories,
        "category_aliases": aliases,
        "temperature": getattr(settings, "nvidia_temperature", None),
        "seed": getattr(settings, "nvidia_seed", None),
        "evaluator": build_evaluator_config(settings),
    }

    evaluation_service = EvaluationService(
        llm_client=llm_client or NVIDIAClient(),
        summary_metric=summary_metric or DeepEvalSummaryMetric(),
        allowed_categories=categories,
        category_aliases=aliases,
    )

    repository: EvaluationRunRepository = JsonEvaluationRunRepository(
        runs_dir,
    )
    run_service = EvaluationRunService(
        evaluation_service=evaluation_service,
        reporting_service=ReportingService(),
        comparison_service=ComparisonService(),
        regression_service=RegressionService(),
        drift_service=DriftService(),
        repository=repository,
        formatter=TextReportFormatter(),
        notifier=notifier,
        drift_alert_url_prefix=settings.slack_report_url_prefix,
    )
    return run_service, system_prompt, test_cases, eval_config
