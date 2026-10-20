"""Build a deterministic PromptGuard golden dataset from BANKING77."""

import csv
import json
from pathlib import Path

SOURCE_PATH = Path("dataset/source/banking77/train.csv")
OUTPUT_PATH = Path("dataset/v1/golden_dataset.json")

NUMBER_OF_CASES = 50

# Explicitly exclude examples that are known to be poor/noisy for evaluation.
# The key is the BANKING77 category and the value contains text fragments
# that should not be selected as the representative example.
EXCLUDED_EXAMPLES: dict[str, set[str]] = {
    "contactless_not_working": {
        "any charges applicable for new card?",
        "how many days processing new card?",
    },
    "activate_my_card": {
        (
            "you can do that in just a few seconds. go to account, hit "
            "activate, and follow the directions from there."
        ),
    },
    "declined_transfer": {
        (
            "why does my card continually get declined? i thought i had "
            "enough money in there but i've tried several times now and "
            "it still isn't working."
        ),
    },
}


def load_examples() -> list[dict[str, str]]:
    """Load examples from the BANKING77 training CSV."""

    if not SOURCE_PATH.exists():
        raise FileNotFoundError(
            f"BANKING77 dataset not found: {SOURCE_PATH}"
        )

    with SOURCE_PATH.open(
        "r",
        encoding="utf-16",
        newline="",
    ) as file:
        rows = list(csv.DictReader(file))

    if not rows:
        raise ValueError("BANKING77 training dataset is empty.")

    required_columns = {"text", "category"}
    actual_columns = set(rows[0].keys())

    if not required_columns.issubset(actual_columns):
        raise ValueError(
            "BANKING77 CSV must contain 'text' and 'category' columns."
        )

    examples: list[dict[str, str]] = []

    for row in rows:
        text = row["text"].strip()
        category = row["category"].strip()

        if not text or not category:
            continue

        examples.append(
            {
                "text": text,
                "category": category,
            }
        )

    if not examples:
        raise ValueError("No valid examples found in BANKING77 dataset.")

    return examples


def is_excluded(example: dict[str, str]) -> bool:
    """Return whether an example is explicitly excluded."""

    category = example["category"]
    normalized_text = " ".join(example["text"].lower().split())

    excluded = EXCLUDED_EXAMPLES.get(category, set())

    return normalized_text in excluded


def example_quality_score(example: dict[str, str]) -> tuple[int, int, str]:
    """Return a deterministic quality score for an example.

    Higher scores are preferred.

    The score favors:
    - examples that are not explicitly excluded
    - reasonably informative examples
    - examples containing multiple words

    The final text value provides deterministic tie-breaking.
    """

    text = example["text"]
    normalized_text = " ".join(text.lower().split())

    if is_excluded(example):
        exclusion_score = 0
    else:
        exclusion_score = 1

    word_count = len(normalized_text.split())

    # Prefer examples with enough context to evaluate an LLM response.
    informative_score = min(word_count, 20)

    return (
        exclusion_score,
        informative_score,
        normalized_text,
    )


def select_examples(
    examples: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Select one deterministic representative example per category."""

    by_category: dict[str, list[dict[str, str]]] = {}

    for example in examples:
        category = example["category"]
        by_category.setdefault(category, []).append(example)

    categories = sorted(by_category)

    if len(categories) < NUMBER_OF_CASES:
        raise ValueError(
            f"Dataset contains only {len(categories)} categories; "
            f"{NUMBER_OF_CASES} are required."
        )

    # Keep category selection deterministic.
    selected_categories = categories[:NUMBER_OF_CASES]

    selected: list[dict[str, str]] = []

    for category in selected_categories:
        category_examples = by_category[category]

        ranked_examples = sorted(
            category_examples,
            key=example_quality_score,
            reverse=True,
        )

        selected.append(ranked_examples[0])

    return selected


def build_golden_cases(
    selected: list[dict[str, str]],
) -> list[dict[str, object]]:
    """Convert selected BANKING77 examples into PromptGuard cases."""

    cases: list[dict[str, object]] = []

    for index, example in enumerate(selected, start=1):
        cases.append(
            {
                "id": f"banking77-{index:03d}",
                "input": example["text"],
                "expected_category": example["category"],
                "ideal_summary": "",
                "expected_difficulty": "medium",
                "notes": (
                    "Source: BANKING77. "
                    "Ideal summary and difficulty require human review."
                ),
            }
        )

    return cases


def validate_cases(cases: list[dict[str, object]]) -> None:
    """Validate the generated golden dataset."""

    if len(cases) != NUMBER_OF_CASES:
        raise ValueError(
            f"Expected {NUMBER_OF_CASES} cases, got {len(cases)}."
        )

    ids = [str(case["id"]) for case in cases]
    categories = [str(case["expected_category"]) for case in cases]

    if len(ids) != len(set(ids)):
        raise ValueError("Golden dataset contains duplicate IDs.")

    if len(categories) != len(set(categories)):
        raise ValueError(
            "Golden dataset contains duplicate categories."
        )

    for case in cases:
        if not str(case["input"]).strip():
            raise ValueError(
                f"Empty input found in case {case['id']}."
            )

        if not str(case["expected_category"]).strip():
            raise ValueError(
                f"Empty category found in case {case['id']}."
            )


def write_dataset(cases: list[dict[str, object]]) -> None:
    """Write the golden dataset JSON file."""

    OUTPUT_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with OUTPUT_PATH.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            cases,
            file,
            indent=2,
            ensure_ascii=False,
        )
        file.write("\n")


def main() -> None:
    """Build the PromptGuard golden dataset."""

    examples = load_examples()

    categories = {
        example["category"]
        for example in examples
    }

    selected = select_examples(examples)
    cases = build_golden_cases(selected)

    validate_cases(cases)
    write_dataset(cases)

    print(f"Source examples: {len(examples)}")
    print(f"Source categories: {len(categories)}")
    print(f"Golden cases: {len(cases)}")
    print(
        "Golden categories: "
        f"{len({case['expected_category'] for case in cases})}"
    )
    print(f"Output: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
