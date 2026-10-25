"""Shared fixtures for the integration and evaluation test suites."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from promptguard.application.contracts import LLMClient, Notifier
from promptguard.domain.metrics import SummaryMetric
from promptguard.domain.models import GoldenTestCase, LLMOutput, LLMResponse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = PROJECT_ROOT / "dataset" / "v1" / "golden_dataset.json"
PROMPT_PATH = PROJECT_ROOT / "prompts" / "v1.yaml"


class ScriptedLLMClient(LLMClient):
    """LLM client that returns deterministic responses keyed by user input."""

    def __init__(
        self,
        responses: dict[str, LLMOutput],
        latency_ms: float = 12.0,
        input_tokens: int = 20,
        output_tokens: int = 8,
    ) -> None:
        """Store the response script used by every generated call."""
        self._responses = responses
        self._latency_ms = latency_ms
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.calls: list[str] = []

    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """Return the scripted output for the given input."""
        self.calls.append(user_input)
        output = self._responses[user_input]
        return LLMResponse(
            output=output,
            latency_ms=self._latency_ms,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
        )


class FixedSummaryMetric(SummaryMetric):
    """Summary metric that always returns a fixed score."""

    def __init__(self, score: float = 4.0) -> None:
        """Store the score returned by every call."""
        self._score = score

    async def score(
        self,
        expected_summary: str,
        actual_summary: str,
    ) -> float:
        """Return the configured score."""
        return self._score


class SequenceSummaryMetric(SummaryMetric):
    """Summary metric that returns scores in a predefined order."""

    def __init__(self, scores: list[float]) -> None:
        """Store the scores to return in sequence."""
        self._scores = iter(scores)

    async def score(
        self,
        expected_summary: str,
        actual_summary: str,
    ) -> float:
        """Return the next configured score."""
        return next(self._scores)


class RecordingNotifier(Notifier):
    """Notifier that records messages instead of delivering them."""

    def __init__(self) -> None:
        """Initialize an empty message log."""
        self.messages: list[str] = []

    async def notify(self, message: str) -> None:
        """Record one notification message."""
        self.messages.append(message)


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the repository root directory."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def golden_cases() -> list[GoldenTestCase]:
    """Load the real v1 golden dataset."""
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return [GoldenTestCase.model_validate(item) for item in data]


@pytest.fixture(scope="session")
def prompt_config() -> dict[str, Any]:
    """Load the real v1 prompt configuration."""
    data = yaml.safe_load(PROMPT_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="session")
def system_prompt(prompt_config: dict[str, Any]) -> str:
    """Return the configured v1 system prompt."""
    return str(prompt_config["system_prompt"])


@pytest.fixture(scope="session")
def categories(prompt_config: dict[str, Any]) -> list[str]:
    """Return the canonical v1 category taxonomy."""
    return [str(category) for category in prompt_config["categories"]]


@pytest.fixture(scope="session")
def aliases(prompt_config: dict[str, Any]) -> dict[str, str]:
    """Return the v1 category alias table."""
    return {
        str(alias): str(target)
        for alias, target in prompt_config.get("category_aliases", {}).items()
    }


@pytest.fixture
def scripted_client() -> type[ScriptedLLMClient]:
    """Return the scripted LLM client class for deterministic runs."""
    return ScriptedLLMClient


@pytest.fixture
def fixed_metric() -> FixedSummaryMetric:
    """Return a summary metric that always scores 4.0."""
    return FixedSummaryMetric()


@pytest.fixture
def sequence_metric() -> type[SequenceSummaryMetric]:
    """Return the sequential summary metric class."""
    return SequenceSummaryMetric


@pytest.fixture
def recording_notifier() -> RecordingNotifier:
    """Return a notifier that records messages."""
    return RecordingNotifier()
