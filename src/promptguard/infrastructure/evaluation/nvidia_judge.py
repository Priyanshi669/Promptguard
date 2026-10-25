"""NVIDIA-backed DeepEval judge model."""

from typing import Any

from deepeval.models import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from promptguard.config import get_settings
from promptguard.infrastructure.llm.retry import (
    LLM_RETRY_ATTEMPTS,
    LLM_RETRY_WAIT,
    RETRYABLE_EXCEPTIONS,
)


class NVIDIAJudge(DeepEvalBaseLLM):
    """Use NVIDIA's OpenAI-compatible API as a DeepEval judge."""

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
        if sync_client is None:
            assert settings is not None
            sync_client = OpenAI(
                api_key=settings.nvidia_api_key,
                base_url=settings.nvidia_base_url,
                timeout=settings.nvidia_request_timeout_seconds,
                max_retries=0,
            )
        if async_client is None:
            assert settings is not None
            async_client = AsyncOpenAI(
                api_key=settings.nvidia_api_key,
                base_url=settings.nvidia_base_url,
                timeout=settings.nvidia_request_timeout_seconds,
                max_retries=0,
            )

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
        self._sync_client = sync_client
        self._async_client = async_client

        super().__init__(model=self._model_name)

    def load_model(self, *args: Any, **kwargs: Any) -> Any:
        """Return the asynchronous NVIDIA client used by DeepEval."""
        return self._async_client

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        wait=LLM_RETRY_WAIT,
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        reraise=True,
    )
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a JSON response for synchronous DeepEval calls."""
        response = self._sync_client.chat.completions.create(
            **self._request(prompt, kwargs),
        )
        return self._content(response)

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        wait=LLM_RETRY_WAIT,
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        reraise=True,
    )
    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a JSON response for asynchronous DeepEval calls."""
        response = await self._async_client.chat.completions.create(
            **self._request(prompt, kwargs),
        )
        return self._content(response)

    def get_model_name(self) -> str:
        """Return the NVIDIA model identifier for evaluation logs."""
        return self._model_name

    def _request(self, prompt: str, overrides: dict[str, Any]) -> dict[str, Any]:
        """Build judge request options, allowing per-call overrides."""
        request: dict[str, Any] = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        if self._temperature is not None and "temperature" not in overrides:
            request["temperature"] = self._temperature
        if self._seed is not None and "seed" not in overrides:
            request["seed"] = self._seed
        request.update(
            {
                key: value
                for key, value in overrides.items()
                if key in {"temperature", "seed"}
            }
        )
        return request

    @staticmethod
    def _content(response: Any) -> str:
        """Extract non-empty text content from a chat-completion response."""
        content = response.choices[0].message.content

        if not content:
            raise ValueError("NVIDIA judge returned an empty response.")

        return content
