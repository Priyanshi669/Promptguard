"""Unit tests for CategoryNormalizer.

Covers exact matches, case/spacing variations, known aliases,
invalid categories, and unknown labels.
"""

import pytest

from promptguard.domain.normalization import CategoryNormalizer


@pytest.fixture
def normalizer() -> CategoryNormalizer:
    """Return a CategoryNormalizer seeded with Banking77 categories and aliases."""
    categories = {
        "Refund_not_showing_up",
        "activate_my_card",
        "age_limit",
        "apple_pay_or_google_pay",
        "atm_support",
    }
    aliases = {
        "Refund Status Inquiry": "Refund_not_showing_up",
        "Card Activation": "activate_my_card",
        "Age Limit": "age_limit",
        "Apple Pay or Google Pay": "apple_pay_or_google_pay",
        "ATM Support": "atm_support",
    }
    return CategoryNormalizer(allowed_categories=categories, aliases=aliases)


# --- Exact matches ---


def test_exact_match_returns_canonical_category(normalizer: CategoryNormalizer) -> None:
    """A category that exactly matches a taxonomy value passes through unchanged."""
    normalized, applied = normalizer.normalize("Refund_not_showing_up")
    assert normalized == "Refund_not_showing_up"
    assert applied is False


# --- Case / spacing variations ---


def test_case_variation_normalizes_to_canonical(
    normalizer: CategoryNormalizer,
) -> None:
    """Lowercase input is mapped to the canonical slug; normalization is applied."""
    normalized, applied = normalizer.normalize("refund_not_showing_up")
    assert normalized == "Refund_not_showing_up"
    assert applied is True


def test_uppercase_variation_normalizes_to_canonical(
    normalizer: CategoryNormalizer,
) -> None:
    normalized, applied = normalizer.normalize("ACTIVATE_MY_CARD")
    assert normalized == "activate_my_card"
    assert applied is True


def test_mixed_case_variation_normalizes_to_canonical(
    normalizer: CategoryNormalizer,
) -> None:
    normalized, applied = normalizer.normalize("Activate_My_Card")
    assert normalized == "activate_my_card"
    assert applied is True


def test_leading_trailing_whitespace_is_collapsed(
    normalizer: CategoryNormalizer,
) -> None:
    normalized, applied = normalizer.normalize("  Refund_not_showing_up  ")
    assert normalized == "Refund_not_showing_up"
    assert applied is False


def test_internal_whitespace_collapsed(
    normalizer: CategoryNormalizer,
) -> None:
    normalized, applied = normalizer.normalize("Refund_not_showing_up")
    assert normalized == "Refund_not_showing_up"
    assert applied is False


# --- Known aliases ---


def test_known_alias_maps_to_canonical_category(
    normalizer: CategoryNormalizer,
) -> None:
    """A human-readable alias is mapped to its canonical slug."""
    normalized, applied = normalizer.normalize("Refund Status Inquiry")
    assert normalized == "Refund_not_showing_up"
    assert applied is True


def test_alias_case_insensitive(normalizer: CategoryNormalizer) -> None:
    normalized, applied = normalizer.normalize("refund status inquiry")
    assert normalized == "Refund_not_showing_up"
    assert applied is True


def test_alias_with_extra_whitespace(normalizer: CategoryNormalizer) -> None:
    normalized, applied = normalizer.normalize("  Card Activation  ")
    assert normalized == "activate_my_card"
    assert applied is True


def test_all_aliases_resolve(normalizer: CategoryNormalizer) -> None:
    """Every alias in the table maps to a distinct canonical category."""
    for alias, expected in normalizer._aliases.items():
        normalized, applied = normalizer.normalize(alias)
        assert normalized == expected, f"Alias {alias!r} did not map to {expected}"
        assert applied is True


# --- Invalid / unknown labels ---


def test_unknown_label_is_preserved_as_is(
    normalizer: CategoryNormalizer,
) -> None:
    """An unknown label is returned unchanged and not silently remapped."""
    normalized, applied = normalizer.normalize("Completely made up category")
    assert normalized == "Completely made up category"
    assert applied is False


def test_invalid_category_is_preserved_as_is(
    normalizer: CategoryNormalizer,
) -> None:
    """An invalid category string is preserved verbatim."""
    normalized, applied = normalizer.normalize("NotACategory")
    assert normalized == "NotACategory"
    assert applied is False


def test_empty_string_is_preserved(normalizer: CategoryNormalizer) -> None:
    normalized, applied = normalizer.normalize("")
    assert normalized == ""
    assert applied is False


def test_whitespace_only_is_preserved(normalizer: CategoryNormalizer) -> None:
    normalized, applied = normalizer.normalize("   ")
    assert normalized == "   "
    assert applied is False


def test_unknown_with_punctuation_is_preserved(
    normalizer: CategoryNormalizer,
) -> None:
    normalized, applied = normalizer.normalize("Refund??? Not a category!!!")
    assert normalized == "Refund??? Not a category!!!"
    assert applied is False


# --- Determinism ---


def test_normalize_is_deterministic(normalizer: CategoryNormalizer) -> None:
    """Repeated calls with the same input return identical results."""
    input_value = "  Refund Status Inquiry  "
    results = [normalizer.normalize(input_value) for _ in range(10)]
    assert all(r == results[0] for r in results)