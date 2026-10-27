"""NVIDIA-backed DeepEval judge model with provider failover."""

from typing import Any

from deepeval.models import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from promptguard.config import get_settings
from promptguard.infrastructure.llm.pool import (
    FAIL_OVER_EXCEPTIONS,
    ProviderRotator,
    ProviderSpec,
    RateLimiter,
    build_provider_pool,
)
from promptguard.infrastructure.llm.retry import (
    LLM_RETRY_ATTEMPTS,
    LLM_RETRY_WAIT,
    TRANSIENT_EXCEPTIONS,
)


class NVIDIAJudge(DeepEvalBaseLLM):
    """Use OpenAI-compatible APIs as a DeepEval judge, failing over on quota.

    Providers are tried in configured order. Rate limits and credential
    rejections fail over to the next provider; transient transport errors
    are retried in place first.
    """

    def __init__(
        self,
        model: str | None = None,
        sync_client: OpenAI | None = None,
        async_client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize NVIDIA clients for synchronous and async evaluation."""
        settings = None
        if model is None or sync_client is None or async_client is None:
            settings = get_settings()

        if model is None:
            assert settings is not None
            model = settings.nvidia_model

        min_interval = getattr(
            settings, "nvidia_min_request_interval_seconds", 1.0,
        )

        if sync_client is not None and async_client is not None:
            self._specs = [
                ProviderSpec(
                    name="injected",
                    base_url=str(getattr(settings, "nvidia_base_url", "injected")),
                    api_key=str(getattr(settings, "nvidia_api_key", "injected")),
                    model=str(model),
                ),
            ]
            self._sync_clients = [sync_client]
            self._async_clients = [async_client]
        else:
            assert settings is not None
            self._specs = build_provider_pool(settings)
            self._sync_clients = [
                OpenAI(
                    api_key=spec.api_key,
                    base_url=spec.base_url,
                    timeout=settings.nvidia_request_timeout_seconds,
                    max_retries=0,
                )
                for spec in self._specs
            ]
            self._async_clients = [
                AsyncOpenAI(
                    api_key=spec.api_key,
                    base_url=spec.base_url,
                    timeout=settings.nvidia_request_timeout_seconds,
                    max_retries=0,
                )
                for spec in self._specs
            ]

        self._limiters = [
            RateLimiter(float(min_interval)) for _ in self._specs
        ]
        self._model_name = model
        self._temperature = (
            getattr(settings, "nvidia_temperature", None)
            if settings is not None
            else None
        )
        self._seed = (
            getattr(settings, "nvidia_seed", None)
            if settings is not None
            else None
        )

        super().__init__(model=self._model_name)

    def load_model(self, *args: Any, **kwargs: Any) -> Any:
        """Return the asynchronous client used by DeepEval."""
        return self._async_clients[0]

    @retry(
        retry=retry_if_exception_type(TRANSIENT_EXCEPTIONS),
        wait=LLM_RETRY_WAIT,
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        reraise=True,
    )
    def _attempt_sync(
        self,
        index: int,
        prompt: str,
        overrides: dict[str, Any],
    ) -> str:
        """Call one provider synchronously, retrying transient failures."""
        response = self._sync_clients[index].chat.completions.create(
            **self._request(self._specs[index].model, prompt, overrides),
        )
        return self._content(response)

    @retry(
        retry=retry_if_exception_type(TRANSIENT_EXCEPTIONS),
        wait=LLM_RETRY_WAIT,
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        reraise=True,
    )
    async def _attempt_async(
        self,
        index: int,
        prompt: str,
        overrides: dict[str, Any],
    ) -> str:
        """Call one provider asynchronously, retrying transient failures."""
        response = await self._async_clients[index].chat.completions.create(
            **self._request(self._specs[index].model, prompt, overrides),
        )
        return self._content(response)

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a JSON response, failing over on quota/credential errors."""
        overrides = self._overrides(kwargs)
        rotator = ProviderRotator(self._specs)
        while True:
            index = rotator.index
            self._limiters[index].acquire_sync()
            try:
                return self._attempt_sync(index, prompt, overrides)
            except FAIL_OVER_EXCEPTIONS as exc:
                rotator.fail_over(f"{type(exc).__name__}")
            except TRANSIENT_EXCEPTIONS as exc:
                if not rotator.has_next:
                    raise
                rotator.fail_over(f"{type(exc).__name__}")

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a JSON response, failing over on quota/credential errors."""
        overrides = self._overrides(kwargs)
        rotator = ProviderRotator(self._specs)
        while True:
            index = rotator.index
            await self._limiters[index].acquire_async()
            try:
                return await self._attempt_async(index, prompt, overrides)
            except FAIL_OVER_EXCEPTIONS as exc:
                rotator.fail_over(f"{type(exc).__name__}")
            except TRANSIENT_EXCEPTIONS as exc:
                if not rotator.has_next:
                    raise
                rotator.fail_over(f"{type(exc).__name__}")

    @staticmethod
    def _overrides(kwargs: dict[str, Any]) -> dict[str, Any]:
        """Extract per-call request overrides."""
        return {
            key: value
            for key, value in kwargs.items()
            if key in {"temperature", "seed"}
        }

    def get_model_name(self) -> str:
        """Return the primary model identifier for evaluation logs."""
        return self._model_name

    def _request(
        self,
        model: str,
        prompt: str,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        """Build judge request options, allowing per-call overrides."""
        request: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        if self._temperature is not None and "temperature" not in overrides:
            request["temperature"] = self._temperature
        if self._seed is not None and "seed" not in overrides:
            request["seed"] = self._seed
        request.update(overrides)
        return request

    @staticmethod
    def _content(response: Any) -> str:
        """Extract non-empty text content from a chat-completion response."""
        content = response.choices[0].message.content

        if not content:
            raise ValueError("NVIDIA judge returned an empty response.")

        return content
