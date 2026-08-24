from abc import ABC, abstractmethod

from promptguard.domain.models import LLMResponse


class LLMClient(ABC):
    """
    Contract for an LLM provider.

    The application layer depends on this abstraction,
    not on a specific provider such as OpenAI.
    """

    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        user_input: str,
    ) -> LLMResponse:
        """
        Generate a structured response from the LLM.
        """
        raise NotImplementedError