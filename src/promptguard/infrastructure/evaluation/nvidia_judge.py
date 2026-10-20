"""NVIDIA-backed DeepEval judge model."""

from typing import Any

from deepeval.models import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI

from promptguard.config import get_settings


class NVIDIAJudge(DeepEvalBaseLLM):
    """Use NVIDIA's OpenAI-compatible API as a DeepEval judge."""

    def __init__(
        self,
        model: str | None = None,
        sync_client: OpenAI | None = None,
        async_client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize NVIDIA clients for synchronous and async evaluation."""
        settings = get_settings()

        self._model_name = model or settings.nvidia_model
        self._sync_client = sync_client or OpenAI(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
        )
        self._async_client = async_client or AsyncOpenAI(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
        )

        super().__init__(model=self._model_name)

    def load_model(self, *args: Any, **kwargs: Any) -> Any:
        """Return the asynchronous NVIDIA client used by DeepEval."""
        return self._async_client

    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a JSON response for synchronous DeepEval calls."""
        response = self._sync_client.chat.completions.create(
            model=self._model_name,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return self._content(response)

    async def a_generate(self, prompt: str, **kwargs: Any) -> str:
        """Generate a JSON response for asynchronous DeepEval calls."""
        response = await self._async_client.chat.completions.create(
            model=self._model_name,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
        )
        return self._content(response)

    def get_model_name(self) -> str:
        """Return the NVIDIA model identifier for evaluation logs."""
        return self._model_name

    @staticmethod
    def _content(response: Any) -> str:
        """Extract non-empty text content from a chat-completion response."""
        content = response.choices[0].message.content

        if not content:
            raise ValueError("NVIDIA judge returned an empty response.")

        return content
