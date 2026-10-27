"""Unit tests for provider error message mapping."""

import httpx
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from promptguard.infrastructure.llm.errors import describe_provider_error

TEST_API_KEY = "sk-secret-test-key-1234"


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://example.com/v1/chat/completions")


def _status_error(
    error_type: type[Exception],
    status: int,
    message: str,
) -> Exception:
    return error_type(
        message,
        response=httpx.Response(status, request=_request()),
        body=None,
    )


def test_timeout_error_is_actionable() -> None:
    """Timeouts should point at the retry/timeout configuration."""
    message = describe_provider_error(
        APITimeoutError(request=_request()), api_key=TEST_API_KEY
    )
    assert "request timeout" in message
    assert "NVIDIA_REQUEST_TIMEOUT_SECONDS" in message
    assert TEST_API_KEY not in message


def test_builtin_timeout_error_is_actionable() -> None:
    """Plain TimeoutError should map to the same guidance."""
    message = describe_provider_error(TimeoutError(), api_key=TEST_API_KEY)
    assert "NVIDIA_REQUEST_TIMEOUT_SECONDS" in message


def test_authentication_error_names_the_credential_fix() -> None:
    """Auth failures should point at NVIDIA_API_KEY and echo no payload."""
    exc = _status_error(
        AuthenticationError, 401, "Unauthorized " + TEST_API_KEY
    )
    message = describe_provider_error(exc, api_key=TEST_API_KEY)
    assert "NVIDIA_API_KEY" in message
    assert "401" in message
    assert TEST_API_KEY not in message
    assert "Unauthorized" not in message


def test_rate_limit_error_mentions_backoff() -> None:
    """429 failures should recommend waiting or reducing volume."""
    message = describe_provider_error(
        _status_error(RateLimitError, 429, "rate limited"),
        api_key=TEST_API_KEY,
    )
    assert "rate limited" in message.lower() or "rate limit" in message.lower()
    assert "[HTTP 429]" in message
    assert TEST_API_KEY not in message


def test_connection_error_mentions_endpoint() -> None:
    """Connection failures should point at the network and base URL."""
    message = describe_provider_error(
        APIConnectionError(request=_request()), api_key=TEST_API_KEY
    )
    assert "NVIDIA_BASE_URL" in message
    assert TEST_API_KEY not in message


def test_internal_server_error_mentions_retries() -> None:
    """5xx failures should say retries were exhausted."""
    message = describe_provider_error(
        _status_error(InternalServerError, 500, "boom"),
        api_key=TEST_API_KEY,
    )
    assert "server error" in message.lower()
    assert "[HTTP 500]" in message


def test_unknown_exception_is_labeled_and_redacted() -> None:
    """Unknown failures keep their diagnostic context, minus secrets."""
    exc = RuntimeError(f"weird failure with {TEST_API_KEY} inside")
    message = describe_provider_error(exc, api_key=TEST_API_KEY)
    assert "RuntimeError" in message
    assert "weird failure" in message
    assert TEST_API_KEY not in message
    assert "[redacted]" in message


def test_unknown_exception_without_key_is_unchanged_context() -> None:
    """Fallback messages keep the type name and message when no key matches."""
    message = describe_provider_error(
        ValueError("dataset missing"), api_key=TEST_API_KEY
    )
    assert "ValueError" in message
    assert "dataset missing" in message


def test_long_unknown_exception_detail_is_truncated() -> None:
    """Fallback diagnostics must stay bounded."""
    exc = RuntimeError("x" * 2000)
    message = describe_provider_error(exc, api_key=TEST_API_KEY)
    assert len(message) < 400
