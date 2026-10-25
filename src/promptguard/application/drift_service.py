"""Category-output drift calculations."""

from promptguard.domain.drift import CategoryDriftResult
from promptguard.domain.models import EvaluationReport


class DriftService:
    """Measure category distribution changes without statistical claims."""

    def compare(
        self, baseline: EvaluationReport, current: EvaluationReport,
    ) -> CategoryDriftResult:
        """Return total variation distance when both runs contain data."""
        if not baseline.results or not current.results:
            return CategoryDriftResult(sufficient_data=False)
        baseline_distribution = self._distribution(baseline)
        current_distribution = self._distribution(current)
        categories = baseline_distribution.keys() | current_distribution.keys()
        distance = sum(
            abs(baseline_distribution.get(category, 0.0)
                - current_distribution.get(category, 0.0))
            for category in categories
        ) / 2
        return CategoryDriftResult(
            sufficient_data=True,
            total_variation_distance=distance,
            baseline_distribution=baseline_distribution,
            current_distribution=current_distribution,
        )

    @staticmethod
    def _distribution(report: EvaluationReport) -> dict[str, float]:
        """Compute category distribution using normalized categories.

        Uses normalized_category which applies case/whitespace folding and
        alias mapping. Unknown categories are preserved as-is (never silently
        remapped) and will appear in the distribution under their raw label.
        """
        total = len(report.results)
        counts: dict[str, int] = {}
        for result in report.results:
            # Use normalized_category for consistent drift measurement
            # Falls back to actual_category if normalized is empty (handled by model_validator)
            category = result.normalized_category or result.actual_category
            counts[category] = counts.get(category, 0) + 1
        return {category: count / total for category, count in counts.items()}