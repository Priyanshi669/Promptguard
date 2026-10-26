"""Application contracts implemented by infrastructure adapters."""

from abc import ABC, abstractmethod

from promptguard.domain.models import EvaluationReport, LLMResponse


class EvaluationRunRepository(ABC):
    """Store and retrieve complete evaluation runs."""

    @abstractmethod
    def save(self, report: EvaluationReport) -> None:
        """Persist one complete report."""

    @abstractmethod
    def get(self, run_id: str) -> EvaluationReport:
        """Return a report by identifier."""

    @abstractmethod
    def get_latest(self) -> EvaluationReport | None:
        """Return the most recently persisted report, if any."""

    @abstractmethod
    def list_runs(self) -> list[EvaluationReport]:
        """Return all persisted reports in timestamp order."""

    @property
    def skipped_runs(self) -> list[str]:
        """Return diagnostics for files skipped by the last list operation."""
        return []


class ReportFormatter(ABC):
    """Render an evaluation report for a human reader."""

    @abstractmethod
    def format(self, report: EvaluationReport) -> str:
        """Return a human-readable report."""


class Notifier(ABC):
    """Deliver a completed evaluation notification."""

    @abstractmethod
    async def notify(self, message: str) -> None:
        """Deliver a message after an evaluation run."""


class LLMClient(ABC):
    """Abstract contract for LLM providers."""

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """Generate a structured response from an LLM."""
