"""Provider pool with quota failover and request-rate limiting."""

import asyncio
import threading
import time
from typing import Any

from openai import AuthenticationError, RateLimitError
from pydantic import BaseModel, Field

FAIL_OVER_EXCEPTIONS = (RateLimitError, AuthenticationError)


class QuotaExceededError(RuntimeError):
    """Raised when every configured provider is exhausted."""


class ProviderSpec(BaseModel):
    """One OpenAI-compatible provider endpoint and its credentials."""

    name: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    api_key: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1)


def build_provider_pool(settings: Any) -> list[ProviderSpec]:
    """Return the configured provider pool.

    ``PROVIDER_POOL`` (JSON list) takes precedence; otherwise a single
    provider is derived from the ``NVIDIA_*`` settings, preserving the
    original single-provider configuration.
    """
    pool = getattr(settings, "provider_pool", None)
    if isinstance(pool, list) and pool:
        return list(pool)
    return [
        ProviderSpec(
            name="nvidia-primary",
            base_url=str(settings.nvidia_base_url),
            api_key=str(settings.nvidia_api_key),
            model=str(settings.nvidia_model),
        ),
    ]


class RateLimiter:
    """Space request starts at least a minimum interval apart."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._async_lock = asyncio.Lock()
        self._sync_lock = threading.Lock()
        self._next_start = 0.0

    async def acquire_async(self) -> None:
        """Reserve the next spaced request slot."""
        if self._min_interval <= 0:
            return
        async with self._async_lock:
            loop = asyncio.get_running_loop()
            wait = self._next_start - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_start = loop.time() + self._min_interval

    def acquire_sync(self) -> None:
        """Reserve the next spaced request slot from sync code."""
        if self._min_interval <= 0:
            return
        with self._sync_lock:
            now = time.monotonic()
            wait = self._next_start - now
            if wait > 0:
                time.sleep(wait)
            self._next_start = time.monotonic() + self._min_interval


class ProviderRotator:
    """Track the active provider and fail over when it is exhausted."""

    def __init__(self, providers: list[ProviderSpec]) -> None:
        if not providers:
            raise ValueError("Provider pool must contain at least one provider.")
        self._providers = providers
        self._index = 0
        self._exhausted: list[str] = []

    @property
    def active(self) -> ProviderSpec:
        return self._providers[self._index]

    @property
    def index(self) -> int:
        return self._index

    @property
    def exhausted_names(self) -> list[str]:
        return list(self._exhausted)

    @property
    def has_next(self) -> bool:
        """Return True when another provider remains after the active one."""
        return self._index + 1 < len(self._providers)

    def fail_over(self, reason: str) -> ProviderSpec:
        """Mark the active provider exhausted and return the next one."""
        self._exhausted.append(f"{self.active.name}: {reason}")
        if self._index + 1 >= len(self._providers):
            raise QuotaExceededError(self._exhaustion_message())
        self._index += 1
        return self.active

    def _exhaustion_message(self) -> str:
        reasons = "; ".join(self._exhausted)
        return (
            f"All {len(self._providers)} configured providers are "
            f"exhausted ({reasons}). Wait for the provider quota window "
            "to reset, add another OpenAI-compatible provider to "
            "PROVIDER_POOL, or reduce request volume."
        )
