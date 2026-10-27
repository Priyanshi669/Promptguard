"""Actionable, secret-free reporting for provider failures."""

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    RateLimitError,
)

from promptguard.infrastructure.llm.pool import QuotaExceededError

KNOWN_PROVIDER_ERRORS = (
    APITimeoutError,
    TimeoutError,
    AuthenticationError,
    RateLimitError,
    APIConnectionError,
    ConnectionError,
    InternalServerError,
    QuotaExceededError,
)

MAX_DETAIL_LENGTH = 300


def _redact(text: str, api_key: str | None) -> str:
    """Remove the configured API key from any diagnostic text."""
    if not api_key or api_key not in text:
        return text
    return text.replace(api_key, "[redacted]")


def _detail(exc: BaseException, api_key: str | None) -> str:
    """Return the exception message truncated and key-redacted."""
    text = " ".join(str(exc).split())
    if len(text) > MAX_DETAIL_LENGTH:
        text = text[:MAX_DETAIL_LENGTH] + "..."
    return _redact(text, api_key)


def describe_provider_error(
    exc: BaseException,
    api_key: str | None = None,
) -> str:
    """Map a provider failure to an actionable, secret-free message.

    Known transport and provider failures get remediation guidance; the
    configured API key value is redacted from every message, including
    unknown-exception fallbacks that may embed arbitrary provider text.
    """
    message: str
    if isinstance(exc, APITimeoutError | TimeoutError):
        message = (
            "The model provider did not respond within the configured "
            "request timeout. Retry the run, or raise "
            "NVIDIA_REQUEST_TIMEOUT_SECONDS (allowed range: 1-60 seconds)."
        )
    elif isinstance(exc, QuotaExceededError):
        message = (
            "Every configured provider is rate limited or unauthorized. "
            + str(exc)
        )
    elif isinstance(exc, AuthenticationError):
        message = (
            "The provider rejected the credentials (HTTP 401). Check that "
            "NVIDIA_API_KEY in .env is valid; the key value is never "
            "printed."
        )
    elif isinstance(exc, RateLimitError):
        message = (
            "The provider rate limited the run (HTTP 429) after the "
            "configured retries. Reduce request volume or wait before "
            "retrying."
        )
    elif isinstance(exc, APIConnectionError | ConnectionError):
        message = (
            "Could not reach the provider endpoint. Check the network "
            "connection and NVIDIA_BASE_URL."
        )
    elif isinstance(exc, InternalServerError):
        message = (
            "The provider returned a server error after the configured "
            "retries. Retry the run later."
        )
    else:
        message = (
            f"Unexpected failure while contacting the provider "
            f"({type(exc).__name__}):"
        )
        detail = _detail(exc, api_key)
        if detail:
            message = f"{message} {detail}"
        return message

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        message = f"{message} [HTTP {status_code}]"
    return message
