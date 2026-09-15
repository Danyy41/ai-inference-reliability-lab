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
