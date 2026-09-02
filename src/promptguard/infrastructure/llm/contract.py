"""LLM client contract."""

from abc import ABC, abstractmethod

from promptguard.domain.models import LLMResponse


class LLMClient(ABC):
    """Abstract contract for LLM providers."""

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """Generate a structured response from an LLM."""
        raise NotImplementedError