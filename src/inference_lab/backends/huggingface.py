import asyncio
import logging
import time

from inference_lab.backends.base import GenerationResult, InferenceBackend
from inference_lab.observability.metrics import set_model_load_seconds

logger = logging.getLogger(__name__)


class HuggingFaceBackend(InferenceBackend):
    """Real local inference backend using a Hugging Face Transformers model.

    The model and tokenizer are loaded once, at construction time, and reused
    for every request - loading a model per-request would make latency
    dominated by disk/network I/O rather than generation. Generation itself
    is synchronous/CPU-bound, so it runs on a worker thread to avoid blocking
    the FastAPI event loop.

    The requested device ("auto", "cpu", or "cuda") is resolved once here via
    device.detect_device(), so the same code runs unmodified on a CPU-only
    machine and on a cloud NVIDIA GPU.
    """

    def __init__(
        self,
        model_name: str,
        device: str = "auto",
        max_new_tokens_cap: int = 256,
    ) -> None:
        # Imported lazily so a mock-only install never needs torch/transformers.
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        from inference_lab.backends.device import detect_device, log_device_info

        self._torch = torch
        self._device = detect_device(device)
        self._max_new_tokens_cap = max_new_tokens_cap

        log_device_info(self._device)

        logger.info("Loading Hugging Face model %s on %s", model_name, self._device)
        start = time.perf_counter()

        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        if self._tokenizer.pad_token is None:
            self._tokenizer.pad_token = self._tokenizer.eos_token

        self._model = AutoModelForCausalLM.from_pretrained(model_name)
        self._model.to(self._device)
        self._model.eval()

        load_time_ms = (time.perf_counter() - start) * 1000
        logger.info("Loaded Hugging Face model %s in %.2f ms", model_name, load_time_ms)
        set_model_load_seconds(backend="huggingface", model=model_name, seconds=load_time_ms / 1000)

    @property
    def device(self) -> str:
        """The resolved device ("cpu" or "cuda") this backend is actually running on."""
        return self._device

    async def generate(self, prompt: str, max_tokens: int) -> GenerationResult:
        max_tokens = min(max_tokens, self._max_new_tokens_cap)
        return await asyncio.to_thread(self._generate_sync, prompt, max_tokens)

    def _generate_sync(self, prompt: str, max_tokens: int) -> GenerationResult:
        from inference_lab.backends.device import get_gpu_memory_stats, reset_gpu_memory_stats

        torch = self._torch
        reset_gpu_memory_stats(self._device)
        start = time.perf_counter()

        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
        prompt_tokens = inputs["input_ids"].shape[-1]

        with torch.no_grad():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                pad_token_id=self._tokenizer.pad_token_id,
            )

        completion_ids = output_ids[0][prompt_tokens:]
        completion_tokens = completion_ids.shape[-1]
        text = self._tokenizer.decode(completion_ids, skip_special_tokens=True)

        generation_ms = (time.perf_counter() - start) * 1000
        gpu_memory = get_gpu_memory_stats(self._device)

        logger.info(
            "Generated %d completion tokens in %.2f ms (%.2f tok/s)%s",
            completion_tokens,
            generation_ms,
            completion_tokens / (generation_ms / 1000) if generation_ms > 0 else 0.0,
            f" gpu_memory={gpu_memory}" if gpu_memory else "",
        )

        finish_reason = "length" if completion_tokens >= max_tokens else "stop"

        return GenerationResult(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            finish_reason=finish_reason,
            **(gpu_memory or {}),
        )
