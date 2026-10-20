from unittest.mock import AsyncMock, MagicMock

import pytest

from promptguard.infrastructure.evaluation.nvidia_judge import NVIDIAJudge


def completion(content: str) -> MagicMock:
    """Build a minimal chat-completion response."""
    response = MagicMock()
    response.choices[0].message.content = content
    return response


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
