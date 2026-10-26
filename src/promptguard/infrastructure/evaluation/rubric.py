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


def summary_quality_rubric_sha256() -> str:
    """Return a stable content hash for the summary-quality rubric."""
    return hashlib.sha256(
        SUMMARY_QUALITY_CRITERIA.encode("utf-8")
    ).hexdigest()
