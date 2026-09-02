"""NVIDIA-backed LLM client implementation."""

import json
import time

from openai import AsyncOpenAI
from pydantic import ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from promptguard.config import get_settings
from promptguard.domain.models import LLMOutput, LLMResponse
from promptguard.infrastructure.llm.contract import LLMClient


class NVIDIAClient(LLMClient):
    """LLM client using NVIDIA's OpenAI-compatible API."""

    def __init__(
        self,
        client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize the NVIDIA client."""
        settings = get_settings()

        self._client = client or AsyncOpenAI(
            api_key=settings.nvidia_api_key,
            base_url=settings.nvidia_base_url,
        )
        self._model = settings.nvidia_model

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    async def _call_llm(
        self,
        system_prompt: str,
        user_input: str,
    ):
        """Call the NVIDIA API with retry handling."""
        return await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_input,
                },
            ],
            response_format={"type": "json_object"},
        )

    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """Generate and validate a structured LLM response."""
        start_time = time.perf_counter()

        response = await self._call_llm(
            system_prompt=system_prompt,
            user_input=user_input,
        )

        latency_ms = (time.perf_counter() - start_time) * 1000

        content = response.choices[0].message.content

        if not content:
            raise ValueError("LLM returned an empty response.")

        try:
            parsed_output = json.loads(content)
            output = LLMOutput.model_validate(parsed_output)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(
                "LLM returned invalid structured output."
            ) from exc

        usage = response.usage

        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        return LLMResponse(
            output=output,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )