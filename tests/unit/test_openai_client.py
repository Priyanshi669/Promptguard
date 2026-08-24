from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from promptguard.domain.models import LLMOutput
from promptguard.infrastructure.llm.openai_client import OpenAIClient


@pytest.mark.asyncio
async def test_openai_client_returns_llm_response():
    parsed_output = LLMOutput(
        category="billing",
        summary="Customer reports a duplicate subscription charge.",
    )

    message = MagicMock()
    message.parsed = parsed_output

    usage = MagicMock()
    usage.prompt_tokens = 20
    usage.completion_tokens = 12

    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    response.usage = usage

    with patch(
        "promptguard.infrastructure.llm.openai_client.AsyncOpenAI"
    ) as mock_openai:
        mock_client = mock_openai.return_value

        mock_client.beta.chat.completions.parse = AsyncMock(
            return_value=response
        )

        client = OpenAIClient(
            api_key="test-key",
            model="test-model",
        )

        result = await client.generate(
            system_prompt="You classify customer messages.",
            user_input="I was charged twice.",
        )

    assert result.output.category == "billing"
    assert result.output.summary == (
        "Customer reports a duplicate subscription charge."
    )

    assert result.input_tokens == 20
    assert result.output_tokens == 12

    mock_client.beta.chat.completions.parse.assert_awaited_once()


@pytest.mark.asyncio
async def test_openai_client_retries_transient_failure():
    parsed_output = LLMOutput(
        category="billing",
        summary="Customer reports a duplicate charge.",
    )

    message = MagicMock()
    message.parsed = parsed_output

    usage = MagicMock()
    usage.prompt_tokens = 20
    usage.completion_tokens = 10

    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    response.usage = usage

    with patch(
        "promptguard.infrastructure.llm.openai_client.AsyncOpenAI"
    ) as mock_openai:
        mock_client = mock_openai.return_value

        mock_client.beta.chat.completions.parse = AsyncMock(
            side_effect=[
                TimeoutError("temporary failure"),
                response,
            ]
        )

        client = OpenAIClient(
            api_key="test-key",
            model="test-model",
        )

        result = await client.generate(
            system_prompt="test",
            user_input="I was charged twice.",
        )

    assert result.output.category == "billing"
    assert result.output.summary == (
        "Customer reports a duplicate charge."
    )

    assert (
        mock_client.beta.chat.completions.parse.await_count
        == 2
    )