from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import APITimeoutError
from tenacity.wait import wait_none

from promptguard.infrastructure.evaluation.nvidia_judge import NVIDIAJudge


def completion(content: str) -> MagicMock:
    """Build a minimal chat-completion response."""
    response = MagicMock()
    response.choices[0].message.content = content
    return response


def test_judge_owns_retry_budget_and_timeout() -> None:
    """Judge clients must use the same retry and timeout policy as the LLM."""
    settings = MagicMock()
    settings.nvidia_api_key = "test-key"
    settings.nvidia_base_url = "https://example.com/v1"
    settings.nvidia_model = "nvidia/test-model"
    settings.nvidia_request_timeout_seconds = 10.0
    settings.nvidia_temperature = None
    settings.nvidia_seed = None

    with patch(
        "promptguard.infrastructure.evaluation.nvidia_judge.get_settings",
        return_value=settings,
    ):
        judge = NVIDIAJudge()

    assert judge._sync_client.max_retries == 0
    assert judge._async_client.max_retries == 0
    assert judge._async_client.timeout == 10.0


@pytest.mark.asyncio
async def test_judge_retries_transient_timeout() -> None:
    """A transient judge timeout should use the application retry policy."""
    request = httpx.Request(
        "POST",
        "https://example.com/chat/completions",
    )
    sync_client = MagicMock()
    async_client = MagicMock()
    async_client.chat.completions.create = AsyncMock(
        side_effect=[
            APITimeoutError(request=request),
            completion('{"score": 1}'),
        ]
    )
    judge = NVIDIAJudge(
        model="nvidia/test-model",
        sync_client=sync_client,
        async_client=async_client,
    )

    with patch.object(
        NVIDIAJudge.a_generate.retry,
        "wait",
        wait_none(),
    ):
        result = await judge.a_generate("Judge this output")

    assert result == '{"score": 1}'
    assert async_client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_judge_stops_after_three_attempts() -> None:
    """The judge should not exceed the shared retry budget."""
    request = httpx.Request(
        "POST",
        "https://example.com/chat/completions",
    )
    sync_client = MagicMock()
    async_client = MagicMock()
    async_client.chat.completions.create = AsyncMock(
        side_effect=APITimeoutError(request=request)
    )
    judge = NVIDIAJudge(
        model="nvidia/test-model",
        sync_client=sync_client,
        async_client=async_client,
    )

    with (
        patch.object(
            NVIDIAJudge.a_generate.retry,
            "wait",
            wait_none(),
        ),
        pytest.raises(APITimeoutError),
    ):
        await judge.a_generate("Judge this output")

    assert async_client.chat.completions.create.await_count == 3


@pytest.mark.asyncio
async def test_nvidia_judge_generates_json_with_async_client() -> None:
    """The DeepEval adapter should use NVIDIA's async chat endpoint."""
    sync_client = MagicMock()
    async_client = MagicMock()
    async_client.chat.completions.create = AsyncMock(
        return_value=completion('{"score": 1}')
    )

    judge = NVIDIAJudge(
        model="nvidia/test-model",
        sync_client=sync_client,
        async_client=async_client,
    )

    result = await judge.a_generate("Judge this output")

    assert result == '{"score": 1}'
    async_client.chat.completions.create.assert_awaited_once_with(
        model="nvidia/test-model",
        messages=[{"role": "user", "content": "Judge this output"}],
        response_format={"type": "json_object"},
    )


@pytest.mark.asyncio
async def test_nvidia_judge_forwards_sampling_controls() -> None:
    """Configured temperature and seed should reach the judge endpoint."""
    settings = MagicMock()
    settings.nvidia_api_key = "test-key"
    settings.nvidia_base_url = "https://example.com/v1"
    settings.nvidia_model = "nvidia/test-model"
    settings.nvidia_temperature = 0.0
    settings.nvidia_seed = 11

    sync_client = MagicMock()
    async_client = MagicMock()
    async_client.chat.completions.create = AsyncMock(
        return_value=completion('{"score": 1}')
    )

    with patch(
        "promptguard.infrastructure.evaluation.nvidia_judge.get_settings",
        return_value=settings,
    ):
        judge = NVIDIAJudge(
            sync_client=sync_client,
            async_client=async_client,
        )
        await judge.a_generate("Judge this output")

    request = async_client.chat.completions.create.await_args.kwargs
    assert request["temperature"] == 0.0
    assert request["seed"] == 11


def test_nvidia_judge_does_not_require_settings_with_injected_clients() -> None:
    """Injected test clients must work in a clean environment."""
    sync_client = MagicMock()
    async_client = MagicMock()

    with patch(
        "promptguard.infrastructure.evaluation.nvidia_judge.get_settings",
        side_effect=AssertionError("settings should not be loaded"),
    ):
        judge = NVIDIAJudge(
            model="nvidia/test-model",
            sync_client=sync_client,
            async_client=async_client,
        )

    assert judge.get_model_name() == "nvidia/test-model"


def test_nvidia_judge_rejects_empty_response() -> None:
    """The adapter should reject an empty judge completion."""
    sync_client = MagicMock()
    sync_client.chat.completions.create.return_value = completion("")
    async_client = MagicMock()

    judge = NVIDIAJudge(
        model="nvidia/test-model",
        sync_client=sync_client,
        async_client=async_client,
    )

    with pytest.raises(ValueError, match="empty response"):
        judge.generate("Judge this output")
