"""Generate raw candidate summaries for calibration manifest cases.

Reads the approved calibration manifest, invokes the NVIDIA LLM client
with the configured prompt version, and persists raw model outputs with
full provenance metadata. Does not modify the production CLI, golden
dataset, or evaluation pipeline.

Each output record preserves the exact manifest case ID, source input,
and raw model-generated summary, along with execution metadata for
downstream human annotation and judge calibration.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from promptguard import config
from promptguard.infrastructure.llm.client import NVIDIAClient
from promptguard.infrastructure.prompts.yaml_repository import PromptRepository

CALIBRATION_DIR = Path("dataset/calibration/v1")
MANIFEST_PATH = CALIBRATION_DIR / "sample_manifest.json"
OUTPUT_PATH = CALIBRATION_DIR / "candidate_outputs.json"
EXPECTED_CASE_COUNT = 100


def load_manifest(path: Path) -> dict[str, Any]:
    """Load and minimally validate the calibration manifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("Calibration manifest must be a JSON object.")
    examples = payload.get("examples")
    if not isinstance(examples, list) or not examples:
        raise ValueError(
            "Calibration manifest must contain a non-empty 'examples' list."
        )
    return payload


def ensure_manifest_approved(manifest: dict[str, Any]) -> None:
    """Refuse to generate candidates from an unapproved manifest."""
    status = manifest.get("manifest_status")
    if status != "approved":
        raise ValueError(
            "Calibration manifest is not approved for candidate generation. "
            f"Current status: {status!r}."
        )

    pending = manifest.get("pending_review_categories") or []
    if pending:
        raise ValueError(
            f"Calibration manifest still has pending review categories: {pending}."
        )

    examples = manifest.get("examples")
    if not isinstance(examples, list) or len(examples) != EXPECTED_CASE_COUNT:
        raise ValueError(
            f"Calibration manifest must contain exactly {EXPECTED_CASE_COUNT} cases."
        )
    if manifest.get("total_examples") != EXPECTED_CASE_COUNT:
        raise ValueError(
            f"Calibration manifest total_examples must be {EXPECTED_CASE_COUNT}."
        )


def prompt_version_from_manifest(manifest: dict[str, Any]) -> str:
    """Derive the prompt version from the manifest's prompt path."""
    prompt_path = manifest.get("prompt_path", "prompts/v1.yaml")
    if not isinstance(prompt_path, str) or not prompt_path.strip():
        raise ValueError("Manifest prompt_path must be a non-empty string.")
    version = Path(prompt_path).stem
    if not version:
        raise ValueError("Manifest prompt_path must identify a prompt version.")
    return version


def load_prompt(prompt_version: str) -> dict[str, Any]:
    """Load prompt configuration including system prompt and category aliases."""
    prompts_dir = Path(__file__).resolve().parents[1] / "prompts"
    repository = PromptRepository(prompts_dir)
    return repository.load(prompt_version)


def extract_system_prompt(prompt_config: dict[str, Any]) -> str:
    """Extract and validate the system prompt from prompt configuration."""
    system_prompt = prompt_config.get("system_prompt")
    if not isinstance(system_prompt, str) or not system_prompt.strip():
        raise ValueError(
            "Prompt configuration must contain a non-empty 'system_prompt'."
        )
    return system_prompt


async def generate_outputs(
    manifest: dict[str, Any],
    system_prompt: str,
    prompt_version: str,
) -> list[dict[str, Any]]:
    """Generate candidate summaries for every manifest case.

    Each record preserves the manifest ID, source input, raw model output,
    and execution metadata. Failed cases are recorded with a status and
    error message rather than being silently dropped.
    """
    settings = config.get_settings()
    client = NVIDIAClient()

    run_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC).isoformat()

    records: list[dict[str, Any]] = []
    for example in manifest["examples"]:
        case_id = str(example["id"])
        source_input = str(example["source_input"])

        record: dict[str, Any] = {
            "id": case_id,
            "source_input": source_input,
            "prompt_version": prompt_version,
            "model": settings.nvidia_model,
            "temperature": getattr(settings, "nvidia_temperature", None),
            "seed": getattr(settings, "nvidia_seed", None),
            "run_id": run_id,
            "generated_at": timestamp,
        }

        try:
            response = await client.generate(
                system_prompt=system_prompt,
                user_input=source_input,
            )
            record["status"] = "success"
            record["actual_summary"] = response.output.summary
            record["category"] = response.output.category
            record["latency_ms"] = response.latency_ms
            record["input_tokens"] = response.input_tokens
            record["output_tokens"] = response.output_tokens
        except Exception as exc:  # noqa: BLE001 - record per-case failures
            record["status"] = "failure"
            record["error_type"] = type(exc).__name__
            record["error_message"] = str(exc)[:1000]

        records.append(record)

    if hasattr(client, "close"):
        await client.close()
    return records


def validate_outputs(records: list[dict[str, Any]], manifest: dict[str, Any]) -> None:
    """Validate output completeness and uniqueness against the manifest."""
    manifest_ids = {str(example["id"]) for example in manifest["examples"]}
    output_ids = {str(record["id"]) for record in records}

    if output_ids != manifest_ids:
        missing = sorted(manifest_ids - output_ids)
        extra = sorted(output_ids - manifest_ids)
        raise ValueError(
            "Output ID set does not match manifest ID set. "
            f"Missing: {missing[:5]}{'...' if len(missing) > 5 else ''}. "
            f"Extra: {extra[:5]}{'...' if len(extra) > 5 else ''}."
        )

    if len(records) != len({str(r["id"]) for r in records}):
        raise ValueError("Output records contain duplicate case IDs.")

    failed = [r for r in records if r.get("status") != "success"]
    if failed:
        failed_ids = [str(r["id"]) for r in failed[:5]]
        raise ValueError(
            f"{len(failed)} case(s) failed generation: {failed_ids}. "
            "Do not proceed to annotation until failures are resolved."
        )

    empty = [
        r
        for r in records
        if not isinstance(r.get("actual_summary"), str)
        or not r["actual_summary"].strip()
    ]
    if empty:
        empty_ids = [str(r["id"]) for r in empty[:5]]
        raise ValueError(
            f"{len(empty)} case(s) produced an empty summary: {empty_ids}."
        )


def write_outputs(records: list[dict[str, Any]], path: Path) -> None:
    """Write candidate outputs as JSON with provenance metadata."""
    payload = {
        "version": "v1",
        "run_id": records[0]["run_id"] if records else None,
        "generated_at": records[0]["generated_at"] if records else None,
        "model": records[0]["model"] if records else None,
        "total_cases": len(records),
        "successful_cases": sum(1 for r in records if r["status"] == "success"),
        "failed_cases": sum(1 for r in records if r["status"] == "failure"),
        "records": records,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


async def main() -> None:
    """Run the calibration-output generation pipeline."""
    manifest = load_manifest(MANIFEST_PATH)
    try:
        ensure_manifest_approved(manifest)
    except ValueError as exc:
        print(f"Candidate generation blocked: {exc}")
        sys.exit(1)

    prompt_version = prompt_version_from_manifest(manifest)
    prompt_config = load_prompt(prompt_version)
    system_prompt = extract_system_prompt(prompt_config)

    records = await generate_outputs(
        manifest,
        system_prompt,
        prompt_version,
    )
    write_outputs(records, OUTPUT_PATH)

    successful = sum(1 for r in records if r.get("status") == "success")
    failed = sum(1 for r in records if r.get("status") == "failure")
    print(f"Generated {successful} successful, {failed} failed case outputs.")
    print(f"Output written to {OUTPUT_PATH}")

    try:
        validate_outputs(records, manifest)
    except ValueError as exc:
        print(f"Output validation failed: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
