"""Deterministic category normalization for evaluation results.

The normalizer maps raw model output categories to the canonical taxonomy
using exact matching and an explicit alias table.  Raw outputs are always
preserved; unknown or ambiguous labels are never silently remapped.
"""

from __future__ import annotations

import re


class CategoryNormalizer:
    """Normalize raw model category strings to canonical taxonomy values.

    Resolution order:
    1. Exact match against an allowed category (case/whitespace-insensitive)
       returns the canonical category with ``applied=False``.
    2. Match against an explicit alias table returns the canonical category
       with ``applied=True``.
    3. Otherwise the cleaned raw value is returned unchanged with
       ``applied=False`` -- unknown labels are never silently remapped.
    """

    def __init__(
        self,
        allowed_categories: set[str] | list[str],
        aliases: dict[str, str] | None = None,
    ) -> None:
        """Initialize the normalizer with the allowed taxonomy and aliases.

        Args:
            allowed_categories: Canonical category slugs (e.g. Banking77 IDs).
            aliases: Optional mapping of human-readable label -> canonical slug.
        """
        # Normalize whitespace in canonical categories too, so internal
        # inconsistencies do not break lookups.
        self._allowed: dict[str, str] = {}
        for category in allowed_categories:
            key = self._clean_key(category)
            self._allowed[key] = category

        self._aliases: dict[str, str] = {}
        if aliases:
            for alias, target in aliases.items():
                self._aliases[self._clean_key(alias)] = target

    @staticmethod
    def _clean_key(value: str) -> str:
        """Collapse whitespace and lowercase for comparison."""
        return re.sub(r"\s+", " ", value.strip()).lower()

    @staticmethod
    def _clean_raw(value: str) -> str:
        """Collapse whitespace but preserve original casing/content."""
        return re.sub(r"\s+", " ", value.strip())

    def normalize(self, raw: str) -> tuple[str, bool]:
        """Return ``(normalized_category, normalization_applied)``.

        ``normalization_applied`` is ``True`` only when the raw value was
        transformed (case fix or alias mapping) to reach a canonical category.
        Unknown labels are returned as-is with ``applied=False``.
        """
        if not raw or not raw.strip():
            return raw, False

        cleaned = self._clean_raw(raw)
        key = cleaned.lower()

        # 1. Direct category match (case/whitespace flexible).
        if key in self._allowed:
            canonical = self._allowed[key]
            if canonical == cleaned:
                return canonical, False
            return canonical, True

        # 2. Explicit alias mapping.
        if key in self._aliases:
            return self._aliases[key], True

        # 3. Unknown label -- preserve raw output, do not silently remap.
        return cleaned, False
