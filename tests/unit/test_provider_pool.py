"""Unit tests for the provider pool, failover, and rate limiting."""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from openai import (
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from promptguard.config import Settings
from promptguard.infrastructure.evaluation.evaluator_config import (
    build_evaluator_config,
)
from promptguard.infrastructure.evaluation.nvidia_judge import NVIDIAJudge
from promptguard.infrastructure.llm.client import NVIDIAClient
from promptguard.infrastructure.llm.errors import describe_provider_error
from promptguard.infrastructure.llm.pool import (
    ProviderRotator,
    ProviderSpec,
    QuotaExceededError,
    RateLimiter,
    build_provider_pool,
)


def spec(name: str) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        base_url=f"https://{name}.example/v1",
        api_key=f"key-{name}",
        model="test-model",
    )


def mock_settings() -> MagicMock:
    settings = MagicMock()
    settings.nvidia_api_key = "test-key"
    settings.nvidia_base_url = "https://example.com/v1"
    settings.nvidia_model = "test-model"
    settings.nvidia_request_timeout_seconds = 10.0
    settings.nvidia_min_request_interval_seconds = 0
    settings.nvidia_temperature = None
    settings.nvidia_seed = None
    settings.provider_pool = None
    return settings


def completion(content: str) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = content
    response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    return response


def rate_limit() -> RateLimitError:
    request = httpx.Request("POST", "https://example.com/v1")
    return RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=request),
        body=None,
    )


def auth_error() -> AuthenticationError:
    request = httpx.Request("POST", "https://example.com/v1")
    return AuthenticationError(
        "unauthorized",
        response=httpx.Response(401, request=request),
        body=None,
    )


def server_error() -> InternalServerError:
    request = httpx.Request("POST", "https://example.com/v1")
    return InternalServerError(
        "boom",
        response=httpx.Response(500, request=request),
        body=None,
    )


def attach_pool(
    client: NVIDIAClient,
    mocks: list[AsyncMock],
) -> None:
    client._providers = [spec(f"provider-{i}") for i in range(len(mocks))]
    client._clients = list(mocks)
    client._limiters = [RateLimiter(0) for _ in mocks]


def build_client(mocks: list[AsyncMock]) -> NVIDIAClient:
    with patch(
        "promptguard.infrastructure.llm.client.get_settings",
        return_value=mock_settings(),
    ):
        client = NVIDIAClient(client=mocks[0])
    attach_pool(client, mocks)
    return client


def test_build_provider_pool_defaults_to_single_nvidia_spec() -> None:
    """Without PROVIDER_POOL, the NVIDIA settings become the only provider."""
    pool = build_provider_pool(mock_settings())
    assert len(pool) == 1
    assert pool[0].name == "nvidia-primary"
    assert pool[0].base_url == "https://example.com/v1"
    assert pool[0].model == "test-model"


def test_settings_parse_provider_pool_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PROVIDER_POOL must parse as JSON and override the single default."""
    pool_json = json.dumps(
        [
            {
                "name": "primary",
                "base_url": "https://a.example/v1",
                "api_key": "k1",
                "model": "m1",
            },
            {
                "name": "backup",
                "base_url": "https://b.example/v1",
                "api_key": "k2",
                "model": "m2",
            },
        ]
    )
    monkeypatch.setenv("PROVIDER_POOL", pool_json)
    settings = Settings(nvidia_api_key="k", nvidia_model="m")
    assert settings.provider_pool is not None
    assert [p.name for p in settings.provider_pool] == ["primary", "backup"]


def test_settings_without_pool_keeps_default(monkeypatch) -> None:
    monkeypatch.delenv("PROVIDER_POOL", raising=False)
    settings = Settings(nvidia_api_key="k", nvidia_model="m")
    assert settings.provider_pool is None


def test_rotator_fails_over_and_reports_exhaustion() -> None:
    rotator = ProviderRotator([spec("a"), spec("b")])
    assert rotator.active.name == "a"
    assert rotator.fail_over("RateLimitError").name == "b"
    with pytest.raises(QuotaExceededError) as excinfo:
        rotator.fail_over("AuthenticationError")
    message = str(excinfo.value)
    assert "All 2 configured providers" in message
    assert "a: RateLimitError" in message
    assert "b: AuthenticationError" in message
    assert "PROVIDER_POOL" in message


def test_rotator_rejects_empty_pool() -> None:
    with pytest.raises(ValueError, match="at least one provider"):
        ProviderRotator([])


@pytest.mark.asyncio
async def test_client_fails_over_on_auth_error() -> None:
    """A dead key on provider one must not sink the run."""
    dead_client = AsyncMock()
    dead_client.chat.completions.create.side_effect = auth_error()

    healthy_client = AsyncMock()
    healthy_client.chat.completions.create.return_value = completion(
        '{"category": "billing", "summary": "Refund request."}'
    )

    client = build_client([dead_client, healthy_client])
    result = await client.generate(
        system_prompt="Classify.", user_input="I want a refund."
    )

    assert result.output.category == "billing"
    assert dead_client.chat.completions.create.await_count == 1
    assert healthy_client.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_client_fails_over_after_transient_retries_exhausted() -> None:
    """Repeated 5xx on the primary must move the run to the fallback."""
    flaky = AsyncMock()
    flaky.chat.completions.create.side_effect = server_error()

    healthy = AsyncMock()
    healthy.chat.completions.create.return_value = completion(
        '{"category": "billing", "summary": "Refund request."}'
    )

    client = build_client([flaky, healthy])
    from tenacity.wait import wait_none

    with patch.object(NVIDIAClient._attempt.retry, "wait", wait_none()):
        result = await client.generate(
            system_prompt="Classify.", user_input="I want a refund."
        )

    assert result.output.category == "billing"
    assert flaky.chat.completions.create.await_count == 3
    assert healthy.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_client_raises_actionable_error_when_pool_exhausted() -> None:
    """Exhausting every provider must raise QuotaExceededError."""
    primary = AsyncMock()
    primary.chat.completions.create.side_effect = rate_limit()
    backup = AsyncMock()
    backup.chat.completions.create.side_effect = auth_error()

    client = build_client([primary, backup])

    with pytest.raises(QuotaExceededError, match="PROVIDER_POOL"):
        await client.generate(
            system_prompt="Classify.", user_input="I want a refund."
        )

    assert primary.chat.completions.create.await_count == 1
    assert backup.chat.completions.create.await_count == 1


def test_quota_error_message_is_actionable_and_secret_free() -> None:
    exc = QuotaExceededError(
        "All 2 configured providers are exhausted "
        "(primary: RateLimitError; backup: AuthenticationError). "
        "Wait for the provider quota window to reset, add another "
        "OpenAI-compatible provider to PROVIDER_POOL, or reduce request "
        "volume."
    )
    message = describe_provider_error(exc, api_key="key-primary")
    assert "Every configured provider" in message
    assert "PROVIDER_POOL" in message
    assert "key-primary" not in message


@pytest.mark.asyncio
async def test_judge_fails_over_on_rate_limit() -> None:
    """The DeepEval judge must fail over exactly like the candidate client."""
    from tenacity.wait import wait_none

    exhausted = AsyncMock()
    exhausted.chat.completions.create.side_effect = rate_limit()
    healthy = AsyncMock()
    healthy.chat.completions.create.return_value = completion('{"score": 1}')

    judge = NVIDIAJudge(
        model="nvidia/test-model",
        sync_client=MagicMock(),
        async_client=exhausted,
    )
    judge._specs = [spec("a"), spec("b")]
    judge._sync_clients = [MagicMock(), MagicMock()]
    judge._async_clients = [exhausted, healthy]
    judge._limiters = [RateLimiter(0), RateLimiter(0)]

    with patch.object(NVIDIAJudge._attempt_async.retry, "wait", wait_none()):
        result = await judge.a_generate("Judge this output")

    assert result == '{"score": 1}'
    assert exhausted.chat.completions.create.await_count == 1
    assert healthy.chat.completions.create.await_count == 1


@pytest.mark.asyncio
async def test_rate_limiter_spaces_async_requests() -> None:
    limiter = RateLimiter(0.05)
    start = time.perf_counter()
    await limiter.acquire_async()
    await limiter.acquire_async()
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.05


def test_rate_limiter_spaces_sync_requests() -> None:
    limiter = RateLimiter(0.05)
    start = time.perf_counter()
    limiter.acquire_sync()
    limiter.acquire_sync()
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.05


def test_rate_limiter_zero_interval_is_passive() -> None:
    limiter = RateLimiter(0)
    start = time.perf_counter()
    limiter.acquire_sync()
    assert time.perf_counter() - start < 0.05


def test_fingerprint_includes_pool_only_when_configured() -> None:
    """Single-provider identity stays stable; pools are fingerprinted."""
    without_pool = build_evaluator_config(mock_settings())
    assert "judge_provider_pool" not in without_pool

    settings = mock_settings()
    settings.provider_pool = [spec("a"), spec("b")]
    with_pool = build_evaluator_config(settings)
    assert with_pool["judge_provider_pool"] == [
        {"name": "a", "model": "test-model"},
        {"name": "b", "model": "test-model"},
    ]
