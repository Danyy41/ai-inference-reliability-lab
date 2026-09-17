import asyncio
import logging
import random

from inference_lab.backends.base import GenerationResult, InferenceBackend

logger = logging.getLogger(__name__)


class MockBackend(InferenceBackend):
    """Fake inference backend that simulates latency and token generation.

    Stands in for a real model server (e.g. vLLM) so the rest of the system
    (API, middleware, load testing, benchmarking) can be built and exercised
    before any real model is wired up.
    """

    def __init__(
        self,
        min_latency_ms: int = 50,
        max_latency_ms: int = 300,
        extra_latency_ms: int = 0,
    ) -> None:
        self._min_latency_ms = min_latency_ms
        self._max_latency_ms = max_latency_ms
        self._extra_latency_ms = extra_latency_ms

        if extra_latency_ms > 0:
            logger.warning(
                "Mock backend fault injection active: +%dms extra latency on every request",
                extra_latency_ms,
            )

    async def generate(self, prompt: str, max_tokens: int) -> GenerationResult:
        latency_ms = random.uniform(self._min_latency_ms, self._max_latency_ms)
        latency_ms += self._extra_latency_ms
        await asyncio.sleep(latency_ms / 1000)

        completion_tokens = min(max_tokens, max(1, len(prompt.split())))
        text = f"[mock completion for prompt of {len(prompt)} chars]"

        return GenerationResult(
            text=text,
            prompt_tokens=len(prompt.split()),
            completion_tokens=completion_tokens,
            finish_reason="length" if completion_tokens >= max_tokens else "stop",
        )
