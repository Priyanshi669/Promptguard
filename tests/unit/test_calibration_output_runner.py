"""Unit tests for the calibration-output generator script."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from promptguard.domain.models import LLMOutput, LLMResponse

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "generate_calibration_outputs.py"
)


def load_script_module() -> Any:
    """Load the script module under test without executing __main__."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_calibration_outputs", SCRIPT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load calibration-output script.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_manifest(examples: list[dict[str, str]]) -> dict[str, Any]:
    """Build a minimal calibration manifest dict."""
    return {
        "version": "v1",
        "manifest_status": "provisional_card_pin_pending_review",
        "seed": "20261026",
        "prompt_path": "prompts/v1.yaml",
        "total_examples": len(examples),
        "examples": examples,
    }


def make_example(
    case_id: str = "banking77-test-0001",
    source_input: str = "How long does it take for a refund?",
    category: str = "Refund_not_showing_up",
    source_category: str = "Refund_not_showing_up",
    inclusion_status: str = "not_applicable",
    word_count: int = 8,
    length_band: str = "short",
) -> dict[str, Any]:
    """Build one manifest example dict."""
    return {
        "id": case_id,
        "source_split": "test",
        "category": category,
        "source_category": source_category,
        "source_input": source_input,
        "word_count": word_count,
        "length_band": length_band,
        "inclusion_status": inclusion_status,
    }


def make_llm_response(
    category: str = "Refund_not_showing_up",
    summary: str = "Customer asks about refund status.",
    latency_ms: float = 100.0,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> LLMResponse:
    """Build a successful LLMResponse."""
    return LLMResponse(
        output=LLMOutput(category=category, summary=summary),
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class TestLoadManifest:
    """Tests for manifest loading and validation."""

    def test_loads_valid_manifest(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example()])
        fake_path = type(
            "FakePath", (), {"read_text": lambda self, **kw: json.dumps(manifest)}
        )()
        result = module.load_manifest(fake_path)
        assert result["total_examples"] == 1

    def test_rejects_non_dict_payload(self) -> None:
        module = load_script_module()
        fake_path = type("FakePath", (), {"read_text": lambda self, **kw: "[1,2,3]"})()
        with pytest.raises(TypeError, match="JSON object"):
            module.load_manifest(fake_path)

    def test_rejects_empty_examples(self) -> None:
        module = load_script_module()
        manifest = make_manifest([])
        fake_path = type(
            "FakePath", (), {"read_text": lambda self, **kw: json.dumps(manifest)}
        )()
        with pytest.raises(ValueError, match="non-empty"):
            module.load_manifest(fake_path)


class TestExtractSystemPrompt:
    """Tests for system prompt extraction."""

    def test_extracts_valid_prompt(self) -> None:
        module = load_script_module()
        config = {"system_prompt": "Classify the customer message."}
        assert module.extract_system_prompt(config) == "Classify the customer message."

    def test_rejects_missing_prompt(self) -> None:
        module = load_script_module()
        with pytest.raises(ValueError, match="non-empty"):
            module.extract_system_prompt({})

    def test_rejects_empty_prompt(self) -> None:
        module = load_script_module()
        with pytest.raises(ValueError, match="non-empty"):
            module.extract_system_prompt({"system_prompt": "   "})


class TestManifestApproval:
    """Tests for the approved-manifest gate."""

    def test_rejects_provisional_manifest(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example()])
        with pytest.raises(ValueError, match="not approved"):
            module.ensure_manifest_approved(manifest)

    def test_rejects_pending_review_categories(self) -> None:
        module = load_script_module()
        manifest = make_manifest(
            [make_example(case_id=f"case-{i}") for i in range(100)]
        )
        manifest["manifest_status"] = "approved"
        manifest["pending_review_categories"] = ["card_pin"]
        with pytest.raises(ValueError, match="pending review"):
            module.ensure_manifest_approved(manifest)

    def test_rejects_wrong_case_count(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example()])
        manifest["manifest_status"] = "approved"
        with pytest.raises(ValueError, match="exactly 100"):
            module.ensure_manifest_approved(manifest)

    def test_accepts_approved_100_case_manifest(self) -> None:
        module = load_script_module()
        manifest = make_manifest(
            [make_example(case_id=f"case-{i}") for i in range(100)]
        )
        manifest["manifest_status"] = "approved"
        module.ensure_manifest_approved(manifest)


class TestPromptLoading:
    """Tests for prompt version derivation and repository loading."""

    def test_derives_prompt_version_from_manifest_path(self) -> None:
        module = load_script_module()
        assert (
            module.prompt_version_from_manifest({"prompt_path": "prompts/v1.yaml"})
            == "v1"
        )

    def test_rejects_blank_prompt_path(self) -> None:
        module = load_script_module()
        with pytest.raises(ValueError, match="prompt_path"):
            module.prompt_version_from_manifest({"prompt_path": "   "})

    def test_loads_checked_in_prompt(self) -> None:
        module = load_script_module()
        prompt = module.load_prompt("v1")
        assert isinstance(prompt.get("system_prompt"), str)
        assert prompt["system_prompt"].strip()


class TestGenerateOutputs:
    """Tests for LLM output generation against mocked client."""

    @pytest.mark.asyncio
    async def test_successful_generation_for_all_cases(self) -> None:
        """Every case should produce a success record with provenance metadata."""
        module = load_script_module()
        manifest = make_manifest(
            [
                make_example(case_id="banking77-test-0001"),
                make_example(case_id="banking77-test-0002"),
            ]
        )

        mock_client = AsyncMock()
        mock_client.generate.side_effect = [
            make_llm_response(summary="Refund status inquiry summary."),
            make_llm_response(summary="Card activation request summary."),
        ]

        settings = MagicMock()
        settings.nvidia_model = "nvidia/nemotron-3-ultra-550b-a55b"
        settings.nvidia_temperature = 0.0
        settings.nvidia_seed = 7

        client = SimpleNamespace(generate=mock_client.generate)
        with (
            patch("promptguard.config.get_settings", return_value=settings),
            patch.object(module, "NVIDIAClient", return_value=client),
        ):
            records = await module.generate_outputs(manifest, "system prompt", "v1")

        assert len(records) == 2
        for record in records:
            assert record["status"] == "success"
            assert record["actual_summary"] is not None
            assert record["actual_summary"].strip() != ""
            assert record.get("error_type") is None
            assert record.get("error_message") is None
            assert record["prompt_version"] == "v1"
            assert record["temperature"] == 0.0
            assert record["seed"] == 7
            assert "run_id" in record
            assert "generated_at" in record

    @pytest.mark.asyncio
    async def test_api_failure_recorded_not_dropped(self) -> None:
        """A failed API call should produce a failure record, not disappear."""
        module = load_script_module()
        manifest = make_manifest([make_example(case_id="banking77-test-0001")])

        mock_client = AsyncMock()
        mock_client.generate.side_effect = ConnectionError("provider unavailable")

        settings = MagicMock()
        settings.nvidia_model = "test-model"
        settings.nvidia_temperature = 0.0
        settings.nvidia_seed = 7

        client = SimpleNamespace(generate=mock_client.generate)
        with (
            patch("promptguard.config.get_settings", return_value=settings),
            patch.object(module, "NVIDIAClient", return_value=client),
        ):
            records = await module.generate_outputs(manifest, "system prompt", "v1")

        assert len(records) == 1
        record = records[0]
        assert record["status"] == "failure"
        assert record["error_type"] == "ConnectionError"
        assert "provider unavailable" in record["error_message"]
        assert "actual_summary" not in record


class TestValidateOutputs:
    """Tests for output validation against the manifest."""

    def test_passes_when_ids_match_and_no_failures(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example(case_id="case-1")])
        records = [
            {
                "id": "case-1",
                "status": "success",
                "actual_summary": "summary",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            }
        ]
        module.validate_outputs(records, manifest)

    def test_raises_when_output_missing_case(self) -> None:
        module = load_script_module()
        manifest = make_manifest(
            [make_example(case_id="case-1"), make_example(case_id="case-2")]
        )
        records = [
            {
                "id": "case-1",
                "status": "success",
                "actual_summary": "summary",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            }
        ]
        with pytest.raises(ValueError, match="Missing"):
            module.validate_outputs(records, manifest)

    def test_raises_when_extra_output_case(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example(case_id="case-1")])
        records = [
            {
                "id": "case-1",
                "status": "success",
                "actual_summary": "summary",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            },
            {
                "id": "case-2",
                "status": "success",
                "actual_summary": "summary",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            },
        ]
        with pytest.raises(ValueError, match="Extra"):
            module.validate_outputs(records, manifest)

    def test_raises_when_duplicate_ids(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example(case_id="case-1")])
        records = [
            {
                "id": "case-1",
                "status": "success",
                "actual_summary": "summary",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            },
            {
                "id": "case-1",
                "status": "success",
                "actual_summary": "summary",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            },
        ]
        with pytest.raises(ValueError, match="duplicate"):
            module.validate_outputs(records, manifest)

    def test_raises_when_case_failed(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example(case_id="case-1")])
        records = [
            {
                "id": "case-1",
                "status": "failure",
                "error_type": "ConnectionError",
                "error_message": "provider unavailable",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            }
        ]
        with pytest.raises(ValueError, match="failed generation"):
            module.validate_outputs(records, manifest)

    def test_raises_when_success_summary_is_empty(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example(case_id="case-1")])
        records = [
            {
                "id": "case-1",
                "status": "success",
                "actual_summary": "   ",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
            }
        ]
        with pytest.raises(ValueError, match="empty summary"):
            module.validate_outputs(records, manifest)


class TestWriteOutputs:
    """Tests for output file writing."""

    def test_writes_complete_json_payload(self, tmp_path: Path) -> None:
        module = load_script_module()
        records = [
            {
                "id": "case-1",
                "status": "success",
                "actual_summary": "summary",
                "run_id": "run-1",
                "generated_at": "2026-01-01T00:00:00Z",
                "model": "test-model",
                "latency_ms": 100.0,
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ]
        output_path = tmp_path / "candidate_outputs.json"
        module.write_outputs(records, output_path)

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert payload["version"] == "v1"
        assert payload["run_id"] == "run-1"
        assert payload["total_cases"] == 1
        assert payload["successful_cases"] == 1
        assert payload["failed_cases"] == 0
        assert len(payload["records"]) == 1
        assert payload["records"][0]["id"] == "case-1"
        assert payload["records"][0]["actual_summary"] == "summary"


class TestFailureGate:
    """Tests that failed generation records are rejected."""

    @pytest.mark.asyncio
    async def test_failed_case_is_rejected_before_annotation(self) -> None:
        module = load_script_module()
        manifest = make_manifest([make_example(case_id="banking77-test-0001")])

        mock_client = AsyncMock()
        mock_client.generate.side_effect = ConnectionError("provider unavailable")

        settings = MagicMock()
        settings.nvidia_model = "test-model"
        settings.nvidia_temperature = 0.0
        settings.nvidia_seed = 7

        client = SimpleNamespace(generate=mock_client.generate)
        with (
            patch("promptguard.config.get_settings", return_value=settings),
            patch.object(module, "NVIDIAClient", return_value=client),
        ):
            records = await module.generate_outputs(manifest, "system prompt", "v1")

        with pytest.raises(ValueError, match="failed generation"):
            module.validate_outputs(records, manifest)
