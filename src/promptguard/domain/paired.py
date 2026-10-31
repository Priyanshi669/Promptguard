"""Paired baseline analysis: exact McNemar and paired bootstrap.

All computations are per matched case, aligned by test case id (never by
array position). Results are informational evidence and never participate
in release-gate decisions.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

from promptguard.domain.statistics import (
    BootstrapConfig,
    BootstrapEstimate,
    bootstrap_interval,
)

if TYPE_CHECKING:
    from promptguard.domain.models import EvaluationResult


def exact_mcnemar_p_value(
    regressions: int,
    fixes: int,
) -> float | None:
    """Return the two-sided exact McNemar p-value for discordant counts.

    ``regressions`` counts cases only the baseline answered correctly;
    ``fixes`` counts cases only the candidate answered correctly. The
    p-value is the exact two-sided binomial probability of seeing a
    split at least this unbalanced under a symmetric null. Returns
    ``None`` when there are no discordant cases: with zero disagreements
    the test cannot distinguish the runs and must not claim an effect.
    """
    discordant = regressions + fixes
    if discordant == 0:
        return None
    tail = sum(
        math.comb(discordant, k)
        for k in range(min(regressions, fixes) + 1)
    )
    return min(1.0, 2 * tail / 2**discordant)


class PairedAnalysis(BaseModel):
    """Paired per-case comparison of a candidate run against its baseline.

    ``baseline_only_correct`` counts cases the candidate regressed on and
    ``candidate_only_correct`` counts cases the candidate fixed; both
    correct/incorrect counts are the concordant remainder.
    """

    matched_cases: int = Field(ge=0)
    both_correct: int = Field(ge=0, default=0)
    both_incorrect: int = Field(ge=0, default=0)
    baseline_only_correct: int = Field(ge=0, default=0)
    candidate_only_correct: int = Field(ge=0, default=0)
    mcnemar_p_value: float | None = Field(default=None, ge=0.0, le=1.0)
    mcnemar_uninformative: bool = Field(
        default=False,
        description=(
            "True when no case pairs disagree: the runs make identical "
            "correctness decisions, so McNemar's test has no evidence."
        ),
    )
    summary_change_interval: BootstrapEstimate | None = None
    unavailable_reason: str | None = Field(default=None)

    @model_validator(mode="after")
    def _require_consistent_counts(self) -> PairedAnalysis:
        """Discordant evidence must add up to the matched case count."""
        if self.unavailable_reason is not None:
            return self
        counted = (
            self.both_correct
            + self.both_incorrect
            + self.baseline_only_correct
            + self.candidate_only_correct
        )
        if counted != self.matched_cases:
            raise ValueError(
                "paired analysis counts must add up to matched_cases"
            )
        if self.mcnemar_uninformative != (self.mcnemar_p_value is None):
            raise ValueError(
                "mcnemar_uninformative must agree with the p-value presence"
            )
        return self

    @classmethod
    def unavailable(cls, reason: str) -> PairedAnalysis:
        """Return an explicitly unavailable analysis with its reason."""
        return cls(
            matched_cases=0,
            unavailable_reason=reason,
        )


def _normalized_expected(result: EvaluationResult) -> str:
    """Return the normalized expected category used for pairing checks."""
    return result.expected_category.strip().lower()


def paired_baseline_analysis(
    baseline_results: list[EvaluationResult],
    candidate_results: list[EvaluationResult],
    bootstrap: BootstrapConfig | None = None,
) -> PairedAnalysis:
    """Compare baseline and candidate per matched case.

    Cases are aligned by test case id. When the runs cannot be aligned
    reliably, the analysis is returned as explicitly unavailable with a
    reason rather than silently skipping or mispairing cases.
    """
    baseline = _unique_by_id(baseline_results)
    if isinstance(baseline, str):
        return PairedAnalysis.unavailable(
            f"baseline results: {baseline}"
        )
    candidate = _unique_by_id(candidate_results)
    if isinstance(candidate, str):
        return PairedAnalysis.unavailable(
            f"candidate results: {candidate}"
        )

    missing = sorted(set(baseline) - set(candidate))
    added = sorted(set(candidate) - set(baseline))
    if missing or added:
        return PairedAnalysis.unavailable(
            "case id mismatch: "
            f"missing from candidate={missing[:5]}, "
            f"not in baseline={added[:5]}"
        )

    mismatched = sorted(
        case_id
        for case_id in baseline
        if _normalized_expected(baseline[case_id])
        != _normalized_expected(candidate[case_id])
    )
    if mismatched:
        return PairedAnalysis.unavailable(
            f"expected categories differ for cases {mismatched[:5]}"
        )

    both_correct = 0
    both_incorrect = 0
    regressions = 0
    fixes = 0
    differences: list[float] = []
    for case_id in baseline:
        was_correct = baseline[case_id].category_match
        is_correct = candidate[case_id].category_match
        if was_correct and is_correct:
            both_correct += 1
        elif not was_correct and not is_correct:
            both_incorrect += 1
        elif was_correct:
            regressions += 1
        else:
            fixes += 1
        differences.append(
            candidate[case_id].summary_score
            - baseline[case_id].summary_score
        )

    p_value = exact_mcnemar_p_value(regressions, fixes)
    return PairedAnalysis(
        matched_cases=len(baseline),
        both_correct=both_correct,
        both_incorrect=both_incorrect,
        baseline_only_correct=regressions,
        candidate_only_correct=fixes,
        mcnemar_p_value=p_value,
        mcnemar_uninformative=p_value is None,
        summary_change_interval=(
            bootstrap_interval(differences, bootstrap) if bootstrap else None
        ),
    )


def _unique_by_id(
    results: list[EvaluationResult],
) -> dict[str, EvaluationResult] | str:
    """Index results by test case id, refusing duplicates and emptiness."""
    if not results:
        return "no evaluated cases"
    indexed: dict[str, EvaluationResult] = {}
    for result in results:
        if result.test_case_id in indexed:
            return f"duplicate test case id {result.test_case_id}"
        indexed[result.test_case_id] = result
    return indexed
