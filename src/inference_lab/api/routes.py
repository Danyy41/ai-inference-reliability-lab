import logging
import time

from fastapi import APIRouter, HTTPException, Request

from inference_lab.api.schemas import GenerateRequest, GenerateResponse, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.post("/generate", response_model=GenerateResponse)
async def generate(request: Request, body: GenerateRequest) -> GenerateResponse:
    backend = request.app.state.backend

    start = time.perf_counter()
    try:
        result = await backend.generate(prompt=body.prompt, max_tokens=body.max_tokens)
    except Exception:
        logger.exception("Backend generation failed")
        raise HTTPException(status_code=502, detail="Inference backend failed") from None
    latency_ms = (time.perf_counter() - start) * 1000
    tokens_per_second = (
        result.completion_tokens / (latency_ms / 1000) if latency_ms > 0 else 0.0
    )

    return GenerateResponse(
        text=result.text,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        finish_reason=result.finish_reason,
        latency_ms=latency_ms,
        tokens_per_second=tokens_per_second,
        gpu_memory_allocated_mb=result.gpu_memory_allocated_mb,
        gpu_memory_reserved_mb=result.gpu_memory_reserved_mb,
        gpu_memory_peak_mb=result.gpu_memory_peak_mb,
    )
