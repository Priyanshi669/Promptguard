import time

from openai import AsyncOpenAI

from promptguard.domain.models import (
    LLMOutput,
    LLMResponse,
)
from promptguard.infrastructure.llm.client import LLMClient
from promptguard.infrastructure.llm.retry import llm_retry_policy


class OpenAIClient(LLMClient):
    """
    OpenAI implementation of the LLMClient contract.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
    ):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model

    async def _call_llm(
        self,
        system_prompt: str,
        user_input: str,
    ):
        async for attempt in llm_retry_policy():
            with attempt:
                return await self.client.beta.chat.completions.parse(
                    model=self.model,
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
                    response_format=LLMOutput,
                )

        raise RuntimeError("LLM retry policy exited unexpectedly.")

    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:

        start_time = time.perf_counter()

        response = await self._call_llm(
            system_prompt=system_prompt,
            user_input=user_input,
        )

        latency_ms = (
            time.perf_counter() - start_time
        ) * 1000

        message = response.choices[0].message

        if message.parsed is None:
            raise ValueError(
                "LLM response could not be parsed."
            )

        usage = response.usage

        return LLMResponse(
            output=message.parsed,
            latency_ms=latency_ms,
            input_tokens=(
                usage.prompt_tokens
                if usage
                else 0
            ),
            output_tokens=(
                usage.completion_tokens
                if usage
                else 0
            ),
        )