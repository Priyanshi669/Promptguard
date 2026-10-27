"""NVIDIA-backed LLM client implementation with provider failover."""

import json
import math
import re
import time
from typing import Any

from openai import AsyncOpenAI
from pydantic import ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt

from promptguard.config import get_settings
from promptguard.domain.models import LLMOutput, LLMResponse
from promptguard.infrastructure.llm.contract import LLMClient
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


class NVIDIAClient(LLMClient):
    """LLM client using OpenAI-compatible APIs with provider failover.

    Providers are tried in configured order. Rate limits and credential
    rejections fail over to the next provider; transient transport errors
    are retried in place first.
    """

    def __init__(
        self,
        client: AsyncOpenAI | None = None,
    ) -> None:
        """Initialize the NVIDIA client."""
        settings = get_settings()

        if client is not None:
            providers = [
                ProviderSpec(
                    name="injected",
                    base_url=str(getattr(settings, "nvidia_base_url", "injected")),
                    api_key=str(getattr(settings, "nvidia_api_key", "injected")),
                    model=str(settings.nvidia_model),
                ),
            ]
            self._clients = [client]
        else:
            pool = build_provider_pool(settings)
            providers = pool
            self._clients = [
                AsyncOpenAI(
                    api_key=spec.api_key,
                    base_url=spec.base_url,
                    timeout=settings.nvidia_request_timeout_seconds,
                    max_retries=0,
                )
                for spec in pool
            ]

        self._providers = providers
        min_interval = getattr(
            settings, "nvidia_min_request_interval_seconds", 1.0,
        )
        self._limiters = [
            RateLimiter(float(min_interval)) for _ in providers
        ]

    @retry(
        retry=retry_if_exception_type(TRANSIENT_EXCEPTIONS),
        wait=LLM_RETRY_WAIT,
        stop=stop_after_attempt(LLM_RETRY_ATTEMPTS),
        reraise=True,
    )
    async def _attempt(
        self,
        client: AsyncOpenAI,
        model: str,
        system_prompt: str,
        user_input: str,
    ) -> tuple[LLMOutput, int, int, float | None]:
        """Call one provider, retrying only transient transport failures."""
        request: dict[str, Any] = {
            "model": model,
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
        settings = get_settings()
        temperature = getattr(settings, "nvidia_temperature", None)
        seed = getattr(settings, "nvidia_seed", None)
        if temperature is not None:
            request["temperature"] = temperature
        if seed is not None:
            request["seed"] = seed

        response = await client.chat.completions.create(**request)

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
        cost_usd = self._extract_usage_cost(usage)

        return output, input_tokens, output_tokens, cost_usd

    async def _call_llm(
        self,
        system_prompt: str,
        user_input: str,
    ) -> tuple[LLMOutput, int, int, float | None]:
        """Call the pool in order, failing over on quota/credential errors.

        Invalid or empty model outputs are deterministic and surface
        directly as ``ValueError`` without another provider call.
        """
        rotator = ProviderRotator(self._providers)
        while True:
            spec = rotator.active
            index = rotator.index
            await self._limiters[index].acquire_async()
            try:
                return await self._attempt(
                    self._clients[index],
                    spec.model,
                    system_prompt,
                    user_input,
                )
            except FAIL_OVER_EXCEPTIONS as exc:
                rotator.fail_over(f"{type(exc).__name__}")
            except TRANSIENT_EXCEPTIONS as exc:
                if not rotator.has_next:
                    raise
                rotator.fail_over(f"{type(exc).__name__}")

    @staticmethod
    def _extract_usage_cost(usage: Any) -> float | None:
        """Return the provider-reported cost only when it is trustworthy.

        Some OpenAI-compatible providers attach a cost figure to the usage
        object; the NVIDIA endpoint tested here does not, which yields
        ``None`` instead of a fabricated price.
        """
        if usage is None:
            return None
        for source in (usage, getattr(usage, "model_extra", None)):
            if not isinstance(source, dict):
                candidate = getattr(source, "cost", None)
            else:
                candidate = source.get("cost", None)
            if isinstance(candidate, int | float):
                value = float(candidate)
                if math.isfinite(value) and value >= 0:
                    return value
        return None

    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """Generate and validate a structured LLM response."""
        start_time = time.perf_counter()

        output, input_tokens, output_tokens, cost_usd = await self._call_llm(
            system_prompt=system_prompt,
            user_input=user_input,
        )

        return LLMResponse(
            output=output,
            latency_ms=(time.perf_counter() - start_time) * 1000,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
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
