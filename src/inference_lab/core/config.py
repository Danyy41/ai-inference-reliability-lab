from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via environment variables (prefix: INFERENCE_LAB_)."""

    model_config = SettingsConfigDict(env_prefix="INFERENCE_LAB_", env_file=".env")

    app_name: str = "AI Inference Reliability Lab"
    backend: str = "mock"  # "mock" | "huggingface" (vLLM planned for a later version)
    log_level: str = "INFO"
    mock_min_latency_ms: int = 50
    mock_max_latency_ms: int = 300
    # Deliberate fault-injection knob (Phase 6): added on top of the normal
    # random mock latency. Default 0 means healthy behavior is byte-for-byte
    # unchanged when this is unset - see experiments/phase6_latency_fault.md.
    mock_extra_latency_ms: int = Field(default=0, ge=0)
    # Server-side concurrency limit on backend.generate() calls (Phase 8B):
    # bounds how many generations can execute at once via an asyncio.Semaphore,
    # so requests beyond the limit queue instead of running immediately.
    # Default 0 means unlimited (no semaphore, Phase 8A / pre-Phase-8 behavior)
    # - see experiments/phase8_concurrency_overload.md.
    max_concurrent_generations: int = Field(default=0, ge=0)

    huggingface_model_name: str = "sshleifer/tiny-gpt2"
    huggingface_device: str = "auto"  # "auto" | "cpu" | "cuda"
    huggingface_max_new_tokens_cap: int = 256


settings = Settings()
