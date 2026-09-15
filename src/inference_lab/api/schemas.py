from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8192)
    max_tokens: int = Field(default=64, ge=1, le=2048)


class GenerateResponse(BaseModel):
    text: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str
    latency_ms: float
    tokens_per_second: float
    gpu_memory_allocated_mb: float | None = None
    gpu_memory_reserved_mb: float | None = None
    gpu_memory_peak_mb: float | None = None
