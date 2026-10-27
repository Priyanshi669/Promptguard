"""Unit tests for the NVIDIA LLM client."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from tenacity.wait import wait_none

from promptguard.domain.models import LLMOutput
from promptguard.infrastructure.llm.client import NVIDIAClient
from promptguard.infrastructure.llm.pool import ProviderSpec, RateLimiter


@pytest.fixture
def mock_settings() -> MagicMock:
    """Return mocked application settings."""
    settings = MagicMock()
    settings.nvidia_api_key = "test-api-key"
    settings.nvidia_base_url = "https://integrate.api.nvidia.com/v1"
    settings.nvidia_model = "test-model"
    settings.nvidia_request_timeout_seconds = 10.0
    settings.nvidia_min_request_interval_seconds = 0
    settings.nvidia_temperature = None
    settings.nvidia_seed = None
    settings.provider_pool = None
    return settings


@pytest.fixture
def mock_openai_client() -> AsyncMock:
    """Return a mocked OpenAI-compatible client."""
    return AsyncMock()


def test_client_owns_retry_budget_and_timeout(
    mock_settings: MagicMock,
) -> None:
    """The SDK must not add retries or override the request timeout."""
    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient()

    assert client._clients[0].max_retries == 0
    assert client._clients[0].timeout == 10.0


@pytest.mark.asyncio
async def test_total_http_attempts_match_application_retry_budget(
    mock_settings: MagicMock,
) -> None:
    """A 5xx response should produce exactly three HTTP attempts."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            500,
            json={"error": {"message": "transient"}},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        sdk = AsyncOpenAI(
            api_key="test-api-key",
            base_url="https://example.invalid/v1",
            http_client=http_client,
            timeout=10.0,
            max_retries=0,
        )
        with patch(
            "promptguard.infrastructure.llm.client.get_settings",
            return_value=mock_settings,
        ):
            client = NVIDIAClient(client=sdk)

        with (
            patch.object(
                NVIDIAClient._attempt.retry,
                "wait",
                wait_none(),
            ),
            pytest.raises(InternalServerError),
        ):
            await client.generate(
                system_prompt="Classify.",
                user_input="Test input.",
            )

    assert attempts == 3


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
async def test_generate_captures_provider_reported_cost(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Client should capture a cost only when the provider attaches one."""
    mock_response = MagicMock()

    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Customer asks about a refund."}'
            )
        )
    ]
    usage = SimpleNamespace(
        prompt_tokens=20,
        completion_tokens=10,
        cost=0.0123,
    )
    mock_response.usage = usage

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

    assert result.cost_usd == pytest.approx(0.0123)


@pytest.mark.asyncio
async def test_generate_leaves_cost_absent_without_provider_figure(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Providers without a cost figure yield None, not a fabricated price."""
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

    assert result.cost_usd is None


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

    assert mock_openai_client.chat.completions.create.await_count == 1


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

    assert mock_openai_client.chat.completions.create.await_count == 1


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

    assert mock_openai_client.chat.completions.create.await_count == 1


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


@pytest.mark.asyncio
async def test_generate_retries_on_openai_connection_error(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """OpenAI SDK connection errors should use the same retry policy."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Refund request."}'
            )
        )
    ]
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    request = httpx.Request("POST", "https://example.com/chat/completions")

    mock_openai_client.chat.completions.create.side_effect = [
        APIConnectionError(request=request),
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


@pytest.mark.asyncio
async def test_generate_retries_on_timeout(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Timeouts are transient and should be retried."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Refund request."}'
            )
        )
    ]
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    request = httpx.Request("POST", "https://example.com/chat/completions")

    mock_openai_client.chat.completions.create.side_effect = [
        APITimeoutError(request=request),
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


@pytest.mark.asyncio
async def test_generate_retries_on_server_error(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Provider 5xx errors are transient and should be retried."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Refund request."}'
            )
        )
    ]
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    request = httpx.Request("POST", "https://example.com/chat/completions")
    response = httpx.Response(500, request=request)

    mock_openai_client.chat.completions.create.side_effect = [
        InternalServerError("server error", response=response, body=None),
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


def rate_limit_error() -> RateLimitError:
    """Build a 429 error matching the OpenAI client's exception type."""
    request = httpx.Request("POST", "https://example.com/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("rate limited", response=response, body=None)


@pytest.mark.asyncio
async def test_generate_fails_over_on_rate_limit_error(
    mock_settings: MagicMock,
) -> None:
    """A 429 must fail over to the next provider instead of retrying in place."""
    exhausted_client = AsyncMock()
    exhausted_client.chat.completions.create.side_effect = rate_limit_error()

    healthy_client = AsyncMock()
    response = MagicMock()
    response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Refund request."}'
            )
        )
    ]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    healthy_client.chat.completions.create.return_value = response

    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings,
    ):
        client = NVIDIAClient(client=exhausted_client)
        client._providers = [
            ProviderSpec(
                name="primary",
                base_url="https://primary.example/v1",
                api_key="k1",
                model="test-model",
            ),
            ProviderSpec(
                name="fallback",
                base_url="https://fallback.example/v1",
                api_key="k2",
                model="test-model",
            ),
        ]
        client._clients = [exhausted_client, healthy_client]
        client._limiters = [RateLimiter(0), RateLimiter(0)]

        result = await client.generate(
            system_prompt="Classify.",
            user_input="I want a refund.",
        )

    assert result.output.category == "billing"
    assert exhausted_client.chat.completions.create.await_count == 1
    assert healthy_client.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_generate_forwards_temperature_and_seed(
    mock_settings: MagicMock,
    mock_openai_client: AsyncMock,
) -> None:
    """Configured sampling controls should be sent to the provider."""
    mock_settings.nvidia_temperature = 0.0
    mock_settings.nvidia_seed = 42

    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='{"category": "billing", "summary": "Refund request."}'
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
        await client.generate(
            system_prompt="Classify.",
            user_input="I want a refund.",
        )

    request = mock_openai_client.chat.completions.create.await_args.kwargs
    assert request["temperature"] == 0.0
    assert request["seed"] == 42
