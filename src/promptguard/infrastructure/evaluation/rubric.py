"""Versioned rubric used by the summary-quality judge."""

import hashlib

SUMMARY_QUALITY_RUBRIC_VERSION = "v1"

SUMMARY_QUALITY_CRITERIA = (
    "Evaluate whether the actual summary accurately captures "
    "the meaning and important information in the expected "
    "summary. Penalize factual contradictions, invented "
    "information, and omission of important information. "
    "Allow reasonable paraphrasing and differences in wording."
)

SUMMARY_QUALITY_EVALUATION_STEPS = (
    (
        "Compare the actual summary against the expected summary and judge "
        "whether it accurately captures the same meaning and the same "
        "important information."
    ),
    (
        "Identify any statement in the actual summary that contradicts the "
        "expected summary and penalize that contradiction."
    ),
    (
        "Identify any detail stated in the actual summary but not in the "
        "expected summary and treat it as invented information."
    ),
    (
        "Identify any important information from the expected summary that "
        "is missing from the actual summary and treat it as an omission."
    ),
    (
        "Allow reasonable paraphrasing and differences in wording; do not "
        "lower the score solely because the wording differs when the same "
        "meaning and important information are preserved."
    ),
)


def summary_quality_rubric_sha256() -> str:
    """Return a stable content hash for the summary-quality rubric."""
    return hashlib.sha256(SUMMARY_QUALITY_CRITERIA.encode("utf-8")).hexdigest()


def summary_quality_evaluation_steps_sha256() -> str:
    """Return a stable content hash for the pinned evaluation steps."""
    joined = "\n".join(SUMMARY_QUALITY_EVALUATION_STEPS)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
