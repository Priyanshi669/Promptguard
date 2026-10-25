"""NVIDIA-backed LLM client implementation."""

import json
import re
import time
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from promptguard.config import get_settings
from promptguard.domain.models import LLMOutput, LLMResponse
from promptguard.infrastructure.llm.contract import LLMClient
from promptguard.infrastructure.llm.retry import (
    LLM_RETRY_ATTEMPTS,
    LLM_RETRY_WAIT,
    RETRYABLE_EXCEPTIONS,
)


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
            timeout=settings.nvidia_request_timeout_seconds,
            max_retries=0,
        )
        self._model = settings.nvidia_model
        self._temperature = getattr(settings, "nvidia_temperature", None)
        self._seed = getattr(settings, "nvidia_seed", None)

    @retry(
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        wait=LLM_RETRY_WAIT,
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        reraise=True,
    )
    async def _call_llm(
        self,
        system_prompt: str,
        user_input: str,
    ) -> tuple[LLMOutput, int, int]:
        """Call the API, retrying only transient transport/provider failures.

        Invalid or empty model outputs are deterministic and surface directly
        as ``ValueError`` without another provider call.
        """
        request: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": user_input,
                },
            ],
            "response_format": {"type": "json_object"},
        }
        if self._temperature is not None:
            request["temperature"] = self._temperature
        if self._seed is not None:
            request["seed"] = self._seed

        response = await self._client.chat.completions.create(**request)

        content = response.choices[0].message.content

        if not content:
            raise ValueError("LLM returned an empty response.")

        try:
            parsed_output = json.loads(self._clean_json_text(content))
            output = LLMOutput.model_validate(parsed_output)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(
                "LLM returned invalid structured output: "
                f"{content[:300]!r}"
            ) from exc

        usage = response.usage

        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0

        return output, input_tokens, output_tokens

    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """Generate and validate a structured LLM response."""
        start_time = time.perf_counter()

        output, input_tokens, output_tokens = await self._call_llm(
            system_prompt=system_prompt,
            user_input=user_input,
        )

        return LLMResponse(
            output=output,
            latency_ms=(time.perf_counter() - start_time) * 1000,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    @staticmethod
    def _clean_json_text(content: str) -> str:
        """Strip reasoning blocks and markdown fences around JSON."""
        backslash = chr(92)
        think_tag = backslash + backslash + "ang" + backslash + backslash
        think_close_tag = backslash + backslash + "/ang" + backslash + backslash
        think_pattern = re.escape(think_tag) + ".*?" + re.escape(think_close_tag)
        text = re.sub(think_pattern, "", content, flags=re.DOTALL).strip()
        if text.startswith("```"):
            fence_start = "^```(?:json)?" + backslash + "s*"
            fence_end = backslash + "s*```$"
            text = re.sub(fence_start, "", text)
            text = re.sub(fence_end, "", text)
        return text.strip()
