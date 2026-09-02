import pytest

from promptguard.domain.models import (
    LLMOutput,
    LLMResponse,
)
from promptguard.infrastructure.llm.client import (
    LLMClient,
)


class FakeLLMClient(LLMClient):
    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:

        return LLMResponse(
            output=LLMOutput(
                category="billing",
                summary="Customer reports a duplicate charge.",
            ),
            latency_ms=10.0,
            input_tokens=20,
            output_tokens=10,
        )


@pytest.mark.asyncio
async def test_llm_contract():
    client = FakeLLMClient()

    result = await client.generate(
        system_prompt="test",
        user_input="I was charged twice.",
    )

    assert result.output.category == "billing"
    assert result.output.summary == ("Customer reports a duplicate charge.")
    assert result.latency_ms == 10.0
