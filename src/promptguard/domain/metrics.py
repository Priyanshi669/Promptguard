"""Metric calculations for model and prompt comparisons."""
"""Evaluation metric contracts."""

from abc import ABC, abstractmethod


class SummaryMetric(ABC):
    """Interface for evaluating summary quality."""

    @abstractmethod
    async def score(
        self,
        expected_summary: str,
        actual_summary: str,
    ) -> float:
        """Return a summary quality score from 1 to 5."""
        raise NotImplementedError