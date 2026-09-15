from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class GenerationResult:
    """Backend-agnostic result of a text generation call."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str = "stop"


class InferenceBackend(ABC):
    """Interface every inference backend must implement.

    The API layer only ever depends on this interface, never on a concrete
    backend, so a real model backend (e.g. vLLM) can be dropped in later by
    implementing this class and flipping a config value - no route changes
    required.
    """

    @abstractmethod
    async def generate(self, prompt: str, max_tokens: int) -> GenerationResult:
        """Generate text for the given prompt, bounded by max_tokens."""
        raise NotImplementedError
