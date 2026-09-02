"""Retry policies for LLM calls."""

"""Retry policies for LLM calls."""

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


def llm_retry_policy() -> AsyncRetrying:
    """Create the retry policy used for LLM calls."""

    return AsyncRetrying(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        wait=wait_exponential(
            multiplier=1,
            min=1,
            max=8,
        ),
        stop=stop_after_attempt(3),
        reraise=True,
    )
