from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, overridable via environment variables (prefix: INFERENCE_LAB_)."""

    model_config = SettingsConfigDict(env_prefix="INFERENCE_LAB_", env_file=".env")

    app_name: str = "AI Inference Reliability Lab"
    backend: str = "mock"  # swap to "vllm" once that backend exists
    log_level: str = "INFO"
    mock_min_latency_ms: int = 50
    mock_max_latency_ms: int = 300


settings = Settings()
