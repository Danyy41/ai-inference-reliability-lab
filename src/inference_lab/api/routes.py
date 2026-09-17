import contextlib
import logging
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from inference_lab.api.schemas import GenerateRequest, GenerateResponse, HealthResponse
from inference_lab.core.config import settings
from inference_lab.observability.metrics import (
    INFERENCE_GENERATIONS_ACTIVE,
    INFERENCE_REQUESTS_IN_FLIGHT,
    metrics_response,
    record_generation,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/metrics")
async def metrics() -> Response:
    body, content_type = metrics_response()
    return Response(content=body, media_type=content_type)


@router.post("/generate", response_model=GenerateResponse)
async def generate(request: Request, body: GenerateRequest) -> GenerateResponse:
    backend = request.app.state.backend
    semaphore = request.app.state.generation_semaphore

    # Phase 8 concurrency observability - see
    # experiments/phase8_concurrency_overload.md. IN_FLIGHT counts every
    # /generate request from the moment it's accepted, including time spent
    # waiting for a generation slot; ACTIVE counts only requests that have
    # acquired a slot and are inside backend.generate(). The gap between the
    # two is the (approximate) queue depth.
    INFERENCE_REQUESTS_IN_FLIGHT.inc()
    try:
        gate = semaphore if semaphore is not None else contextlib.nullcontext()
        async with gate:
            INFERENCE_GENERATIONS_ACTIVE.inc()
            try:
                start = time.perf_counter()
                try:
                    result = await backend.generate(prompt=body.prompt, max_tokens=body.max_tokens)
                except Exception:
                    logger.exception("Backend generation failed")
                    record_generation(
                        backend=settings.backend,
                        success=False,
                        duration_seconds=time.perf_counter() - start,
                    )
                    raise HTTPException(
                        status_code=502, detail="Inference backend failed"
                    ) from None
            finally:
                INFERENCE_GENERATIONS_ACTIVE.dec()

        latency_ms = (time.perf_counter() - start) * 1000
        latency_s = latency_ms / 1000
        tokens_per_second = result.completion_tokens / latency_s if latency_s > 0 else 0.0

        record_generation(
            backend=settings.backend,
            success=True,
            duration_seconds=latency_s,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            gpu_memory_allocated_mb=result.gpu_memory_allocated_mb,
            gpu_memory_reserved_mb=result.gpu_memory_reserved_mb,
            gpu_memory_peak_mb=result.gpu_memory_peak_mb,
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
    finally:
        INFERENCE_REQUESTS_IN_FLIGHT.dec()
