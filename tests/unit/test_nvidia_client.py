"""Unit tests for the NVIDIA LLM client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from promptguard.domain.models import LLMOutput
from promptguard.infrastructure.llm.client import NVIDIAClient


@pytest.fixture
def mock_settings() -> MagicMock:
    """Return mocked application settings."""
    settings = MagicMock()
    settings.nvidia_api_key = "test-api-key"
    settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
    settings.nvidia_model = "test-model"
    return settings


@pytest.fixture
def mock_openai_client() -> AsyncMock:
    """Return a mocked OpenAI-compatible client."""
    return AsyncMock()


@pytest.mark.asyncio
async def test_generate_returns_structured_response(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should convert valid JSON into an LLMResponse."""
    mock_response = MagicMock()

    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Customer asks about a refund."}'
            )
        )
    ]

    mock_response.usage = MagicMock(
        prompt_tokens=20,
        completion_tokens=10,
    )

    mock_openai_client.chat.completions.create.return_value = mock_response

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)

        result = await client.generate(
            system_prompt="Classify the customer message.",
            user_input="I want a refund.",
        )

    assert result.output == LLMOutput(
        category="billing",
        summary="Customer asks about a refund.",
    )
    assert result.input_tokens == 20
    assert result.output_tokens == 10
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_generate_sends_correct_request(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should send the expected request to the LLM."""
    mock_response = MagicMock()

    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "technical", "summary": "Customer reports an error."}'
            )
        )
    ]

    mock_response.usage = MagicMock(
        prompt_tokens=15,
        completion_tokens=8,
    )

    mock_openai_client.chat.completions.create.return_value = mock_response

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)

        await client.generate(
            system_prompt="Classify this message.",
            user_input="The application crashes.",
        )

    mock_openai_client.chat.completions.create.assert_awaited_once_with(
        model="test-model",
        messages=[
            {
                "role": "system",
                "content": "Classify this message.",
            },
            {
                "role": "user",
                "content": "The application crashes.",
            },
        ],
        response_format={"type": "json_object"},
    )


@pytest.mark.asyncio
async def test_generate_rejects_invalid_json(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should reject malformed JSON."""
    mock_response = MagicMock()

    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content="this is not json"
            )
        )
    ]

    mock_response.usage = MagicMock(
        prompt_tokens=10,
        completion_tokens=5,
    )

    mock_openai_client.chat.completions.create.return_value = mock_response

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)

        with pytest.raises(
            ValueError,
            match="invalid structured output",
        ):
            await client.generate(
                system_prompt="Classify.",
                user_input="Test input.",
            )


@pytest.mark.asyncio
async def test_generate_rejects_invalid_output_schema(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should reject valid JSON with an invalid schema."""
    mock_response = MagicMock()

    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "", "summary": "Some summary."}'
            )
        )
    ]

    mock_response.usage = MagicMock(
        prompt_tokens=10,
        completion_tokens=5,
    )

    mock_openai_client.chat.completions.create.return_value = mock_response

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)

        with pytest.raises(
            ValueError,
            match="invalid structured output",
        ):
            await client.generate(
                system_prompt="Classify.",
                user_input="Test input.",
            )


@pytest.mark.asyncio
async def test_generate_rejects_empty_response(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should reject an empty model response."""
    mock_response = MagicMock()

    mock_response.choices = [
        MagicMock(
            message=MagicMock(content=None)
        )
    ]

    mock_response.usage = MagicMock(
        prompt_tokens=10,
        completion_tokens=0,
    )

    mock_openai_client.chat.completions.create.return_value = mock_response

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)

        with pytest.raises(
            ValueError,
            match="empty response",
        ):
            await client.generate(
                system_prompt="Classify.",
                user_input="Test input.",
            )
@pytest.mark.asyncio
async def test_generate_retries_on_connection_error(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should retry when the LLM connection fails."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Refund request."}'
            )
        )
    ]
    mock_response.usage = MagicMock(
        prompt_tokens=10,
        completion_tokens=5,
    )

    mock_openai_client.chat.completions.create.side_effect = [
        ConnectionError("temporary failure"),
        mock_response,
    ]

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)

        result = await client.generate(
            system_prompt="Classify.",
            user_input="I want a refund.",
        )

    assert result.output.category == "billing"
    assert mock_openai_client.chat.completions.create.await_count == 2


BACKSLASH = chr(92)
THINK_OPEN = BACKSLASH * 2 + "ang" + BACKSLASH * 2
THINK_CLOSE = BACKSLASH * 2 + "/ang" + BACKSLASH * 2


@pytest.mark.asyncio
async def test_generate_tolerates_reasoning_and_fences(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Reasoning blocks and markdown fences around JSON should be stripped."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content=(
                    THINK_OPEN + " hidden thoughts " + THINK_CLOSE + "\n"
                    '```json\n'
                    '{"category": "billing", "summary": "Customer wants a refund."}\n'
                    '```'
                )
            )
        )
    ]
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    mock_openai_client.chat.completions.create.return_value = mock_response

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)
        result = await client.generate(
            system_prompt="Classify.",
            user_input="I want a refund.",
        )

    assert result.output.category == "billing"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"a": 1}', '{"a": 1}'),
        (
            THINK_OPEN + " reasoning " + THINK_CLOSE + '\n{"a": 1}\nAfter text.',
            '{"a": 1}\nAfter text.',
        ),
        ('```json\n{"a": 1}\n```', '{"a": 1}'),
    ],
)
def test_clean_json_text_strips_reasoning_and_fences(
    mock_settings: MagicMock,
    raw: str,
    expected: str,
) -> None:
    """The JSON extractor should normalize common wrapper formats."""
    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        assert NVIDIAClient._clean_json_text(raw) == expected


@pytest.mark.asyncio
async def test_generate_stops_after_three_attempts(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should stop after three failed retry attempts."""
    mock_openai_client.chat.completions.create.side_effect = (
        ConnectionError("connection failed")
    )

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=mock_openai_client)

        with pytest.raises(ConnectionError, match="connection failed"):
            await client.generate(
                system_prompt="Classify.",
                user_input="Test input.",
            )

    assert mock_openai_client.chat.completions.create.await_count == 3                
