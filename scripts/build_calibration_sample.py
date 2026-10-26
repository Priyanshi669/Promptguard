"""Build a deterministic, unlabeled calibration sample manifest."""

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

SOURCE_PATH = Path("dataset/source/banking77/test.csv")
GOLDEN_PATH = Path("dataset/v1/golden_dataset.json")
PROMPT_PATH = Path("prompts/v1.yaml")
OUTPUT_PATH = Path("dataset/calibration/v1/sample_manifest.json")
REVIEW_PATH = Path("dataset/calibration/v1/card_pin_inclusion_review.csv")
DEFAULT_SEED = "20261026"
DEFAULT_PER_CATEGORY = 2

SOURCE_CATEGORY_OVERRIDES: dict[str, tuple[str, ...]] = {
    "card_pin": ("get_physical_card", "pin_blocked"),
}


def normalize_input(text: str) -> str:
    """Normalize text for duplicate and overlap checks."""
    return " ".join(text.lower().split())


def load_prompt_categories(path: Path) -> list[str]:
    """Load the ordered category taxonomy from a prompt file."""
    payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Prompt file '{path}' must contain a YAML object.")

    categories = payload.get("categories")
    if not isinstance(categories, list):
        raise TypeError(f"Prompt file '{path}' must contain a categories list.")

    normalized: list[str] = []
    for category in categories:
        if not isinstance(category, str) or not category.strip():
            raise ValueError(
                f"Prompt file '{path}' contains an invalid category."
            )
        normalized.append(category.strip())

    if len(normalized) != len(set(normalized)):
        raise ValueError(f"Prompt file '{path}' contains duplicate categories.")
    if not normalized:
        raise ValueError(f"Prompt file '{path}' contains no categories.")
    return normalized


def load_source_examples(path: Path) -> list[dict[str, str]]:
    """Load BANKING77 examples with stable source IDs."""
    with path.open("r", encoding="utf-16", newline="") as file:
        rows = list(csv.DictReader(file))

    if not rows:
        raise ValueError(f"Source dataset '{path}' is empty.")

    required_columns = {"text", "category"}
    if not required_columns.issubset(rows[0].keys()):
        raise ValueError(
            f"Source dataset '{path}' must contain 'text' and 'category'."
        )

    examples: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=1):
        text = (row.get("text") or "").strip()
        category = (row.get("category") or "").strip()
        if not text or not category:
            continue
        examples.append(
            {
                "source_id": f"banking77-test-{index:04d}",
                "input": text,
                "source_category": category,
                "normalized_input": normalize_input(text),
            }
        )
    return examples


def load_golden_inputs(path: Path) -> set[str]:
    """Load normalized golden-dataset inputs for overlap exclusion."""
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError(f"Golden dataset '{path}' must be a JSON list.")

    inputs: set[str] = set()
    for case in payload:
        if not isinstance(case, dict):
            raise TypeError(f"Golden dataset '{path}' contains a non-object.")
        text = case.get("input")
        if isinstance(text, str) and text.strip():
            inputs.add(normalize_input(text))
    return inputs


def load_candidate_generation_failures(
    output_path: Path,
) -> set[str]:
    """Return case ids whose candidate generation previously failed."""
    failure_ids: set[str] = set()

    failures_log = Path("dataset/calibration/v1/generation_failures.json")
    if failures_log.exists():
        payload = json.loads(failures_log.read_text(encoding="utf-8"))
        recorded = payload.get("recorded_failures")
        if isinstance(recorded, dict):
            failure_ids |= set(recorded)

    if not output_path.exists():
        return failure_ids
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        return failure_ids
    failure_ids |= {
        str(record["id"])
        for record in records
        if record.get("status") == "failure"
    }
    return failure_ids


def load_card_pin_review(
    path: Path,
) -> tuple[set[str], set[str]]:
    """Return human-excluded and human-included case ids from the review file.

    Only rows with a decision plus a reviewer id and timestamp count as
    human-verified; template rows with blank decision fields stay pending.
    """
    if not path.exists():
        return set(), set()

    with path.open("r", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))

    excluded = {
        row["id"]
        for row in rows
        if row.get("decision") == "exclude"
        and row.get("reviewer_id", "").strip()
        and row.get("reviewed_at", "").strip()
    }
    included = {
        row["id"]
        for row in rows
        if row.get("decision") == "include"
        and row.get("reviewer_id", "").strip()
        and row.get("reviewed_at", "").strip()
    }
    return excluded, included


def rank_key(seed: str, category: str, normalized_input: str) -> str:
    """Return a deterministic SHA-256 ranking key."""
    return hashlib.sha256(
        f"{seed}\0{category}\0{normalized_input}".encode()
    ).hexdigest()


def select_from_band(
    examples: list[dict[str, str]],
    seed: str,
    category: str,
) -> dict[str, str]:
    """Select the highest-ranked example from one length band."""
    return min(
        examples,
        key=lambda example: (
            rank_key(seed, category, example["normalized_input"]),
            example["source_id"],
        ),
    )


def build_manifest(
    source_examples: list[dict[str, str]],
    categories: list[str],
    golden_inputs: set[str],
    seed: str,
    per_category: int,
    excluded_ids: set[str],
    included_ids: set[str],
) -> list[dict[str, object]]:
    """Select a deterministic, stratified, unlabeled calibration sample.

    Human-excluded ids are ineligible, so the ranked selection naturally
    promotes the next-ranked eligible case in the same category and
    length band without any manual indices.
    """
    by_source_category: dict[str, list[dict[str, str]]] = defaultdict(list)
    for example in source_examples:
        by_source_category[example["source_category"]].append(example)

    selected: list[dict[str, object]] = []
    for category in categories:
        source_categories = SOURCE_CATEGORY_OVERRIDES.get(
            category,
            (category,),
        )
        candidates = [
            example
            for source_category in source_categories
            for example in by_source_category.get(source_category, [])
        ]
        if category == "card_pin":
            candidates = [
                example
                for example in candidates
                if "pin" in example["normalized_input"]
            ]

        if not candidates:
            raise ValueError(
                f"Category '{category}' has no eligible source examples from "
                + ", ".join(source_categories)
            )

        candidates = [
            example
            for example in candidates
            if example["normalized_input"] not in golden_inputs
            and example["source_id"] not in excluded_ids
        ]

        unique_candidates: dict[str, dict[str, str]] = {}
        for example in candidates:
            unique_candidates.setdefault(
                example["normalized_input"],
                example,
            )
        candidates = list(unique_candidates.values())

        if len(candidates) < per_category:
            raise ValueError(
                f"Category '{category}' has only {len(candidates)} eligible "
                f"examples; {per_category} are required."
            )

        word_counts = sorted(
            len(example["normalized_input"].split())
            for example in candidates
        )
        median_word_count = word_counts[(len(word_counts) - 1) // 2]
        short_band = [
            example
            for example in candidates
            if len(example["normalized_input"].split()) <= median_word_count
        ]
        long_band = [
            example
            for example in candidates
            if len(example["normalized_input"].split()) > median_word_count
        ]

        if per_category != 2:
            raise ValueError(
                "This plan currently supports two examples per category."
            )
        if not short_band or not long_band:
            raise ValueError(
                f"Category '{category}' does not have both length bands."
            )

        category_selection = [
            (select_from_band(short_band, seed, category), "short"),
            (select_from_band(long_band, seed, category), "long"),
        ]

        for example, band in category_selection:
            entry: dict[str, object] = {
                "id": example["source_id"],
                "source_split": "test",
                "category": category,
                "source_category": example["source_category"],
                "source_input": example["input"],
                "word_count": len(example["normalized_input"].split()),
                "length_band": band,
            }
            if category == "card_pin":
                entry["inclusion_status"] = (
                    "included"
                    if example["source_id"] in included_ids
                    else "pending_human_review"
                )
            selected.append(entry)

    return selected


def validate_manifest(
    selected: list[dict[str, object]],
    categories: list[str],
    per_category: int,
) -> None:
    """Validate manifest coverage and uniqueness."""
    expected_total = len(categories) * per_category
    if len(selected) != expected_total:
        raise ValueError(
            f"Expected {expected_total} examples, got {len(selected)}."
        )

    ids = [str(example["id"]) for example in selected]
    if len(ids) != len(set(ids)):
        raise ValueError("Manifest contains duplicate IDs.")

    normalized_inputs = [
        normalize_input(str(example["source_input"])) for example in selected
    ]
    if len(normalized_inputs) != len(set(normalized_inputs)):
        raise ValueError("Manifest contains duplicate source inputs.")

    by_category: dict[str, list[dict[str, object]]] = defaultdict(list)
    for example in selected:
        by_category[str(example["category"])].append(example)

    for category in categories:
        examples = by_category.get(category, [])
        if len(examples) != per_category:
            raise ValueError(
                f"Category '{category}' has {len(examples)} examples; "
                f"{per_category} are required."
            )
        bands = sorted(str(example["length_band"]) for example in examples)
        if bands != ["long", "short"]:
            raise ValueError(
                f"Category '{category}' must contain one short and one long example."
            )


def write_manifest(
    output_path: Path,
    selected: list[dict[str, object]],
    seed: str,
    source_path: Path,
    golden_path: Path,
    prompt_path: Path,
) -> dict[str, object]:
    """Write the unlabeled sampling manifest."""
    pending_categories = [
        category
        for category in SOURCE_CATEGORY_OVERRIDES
        if any(
            entry.get("inclusion_status") == "pending_human_review"
            for entry in selected
            if entry["category"] == category
        )
    ]
    card_pin_entries = [
        entry for entry in selected if entry["category"] == "card_pin"
    ]
    card_pin_reviewed = bool(card_pin_entries) and all(
        entry.get("inclusion_status") == "included"
        for entry in card_pin_entries
    )
    if card_pin_reviewed:
        manifest_status = "approved"
    else:
        manifest_status = "provisional_card_pin_pending_review"

    payload = {
        "version": "v1",
        "manifest_status": manifest_status,
        "seed": seed,
        "source_path": source_path.as_posix(),
        "golden_dataset_path": golden_path.as_posix(),
        "prompt_path": prompt_path.as_posix(),
        "source_category_overrides": {
            category: list(source_categories)
            for category, source_categories in SOURCE_CATEGORY_OVERRIDES.items()
        },
        "pending_review_categories": pending_categories,
        "total_examples": len(selected),
        "examples": selected,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    return payload


def append_pending_review_rows(
    review_path: Path,
    selected: list[dict[str, object]],
) -> list[str]:
    """Append blank-decision review rows for any yet-unreviewed card_pin case.

    Existing rows are preserved verbatim; only template rows with blank
    human decision fields are added, keeping the file a human record.
    """
    fields = [
        "id",
        "source_category",
        "source_input",
        "decision",
        "primary_intent",
        "rationale",
        "reviewer_id",
        "reviewed_at",
    ]
    if not any(entry["category"] == "card_pin" for entry in selected):
        return []

    existing: dict[str, dict[str, str]] = {}
    if review_path.exists():
        with review_path.open("r", encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                existing[row["id"]] = row

    appended: list[str] = []
    for entry in selected:
        if entry["category"] != "card_pin":
            continue
        entry_id = str(entry["id"])
        if entry_id in existing:
            continue
        row = {field: "" for field in fields}
        row["id"] = entry_id
        row["source_category"] = str(entry["source_category"])
        row["source_input"] = str(entry["source_input"])
        existing[entry_id] = row
        appended.append(entry_id)

    if not appended:
        return []

    with review_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in existing.values():
            writer.writerow(row)
    return appended


def main() -> None:
    """Build the calibration sample manifest from CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Build a deterministic unlabeled calibration sample."
    )
    parser.add_argument("--source", type=Path, default=SOURCE_PATH)
    parser.add_argument("--golden", type=Path, default=GOLDEN_PATH)
    parser.add_argument("--prompt", type=Path, default=PROMPT_PATH)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument(
        "--per-category",
        type=int,
        default=DEFAULT_PER_CATEGORY,
    )
    args = parser.parse_args()

    categories = load_prompt_categories(args.prompt)
    source_examples = load_source_examples(args.source)
    golden_inputs = load_golden_inputs(args.golden)
    excluded_ids, included_ids = load_card_pin_review(REVIEW_PATH)
    excluded_ids |= load_candidate_generation_failures(
        Path("dataset/calibration/v1/candidate_outputs.json")
    )
    selected = build_manifest(
        source_examples,
        categories,
        golden_inputs,
        args.seed,
        args.per_category,
        excluded_ids,
        included_ids,
    )
    validate_manifest(selected, categories, args.per_category)
    manifest = write_manifest(
        args.output,
        selected,
        args.seed,
        args.source,
        args.golden,
        args.prompt,
    )
    appended = append_pending_review_rows(REVIEW_PATH, selected)

    print(f"Categories: {len(categories)}")
    print(f"Examples: {len(selected)}")
    print(f"Manifest: {args.output}")
    print(f"Manifest status: {manifest['manifest_status']}")
    print(f"Pending review categories: {manifest['pending_review_categories']}")
    if appended:
        print(f"New card_pin review rows: {', '.join(appended)}")


if __name__ == "__main__":
    main()
