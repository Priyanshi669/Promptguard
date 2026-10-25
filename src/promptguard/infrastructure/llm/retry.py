"""Retry policies for LLM calls."""

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

RETRYABLE_EXCEPTIONS = (
    TimeoutError,
    ConnectionError,
    APIConnectionError,
    APITimeoutError,
    RateLimitError,
    InternalServerError,
)

LLM_RETRY_ATTEMPTS = 3
LLM_RETRY_WAIT = wait_exponential(
    multiplier=1,
    min=1,
    max=8,
)


def llm_retry_policy() -> AsyncRetrying:
    """Create the bounded policy for transient transport and provider errors.

    Invalid model outputs are deterministic and must not be retried as if they
    were network failures.
    """

    return AsyncRetrying(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        wait=LLM_RETRY_WAIT,
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        reraise=True,
    )
