from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

_BYTES_PER_MB = 1024 * 1024

# Shared by both latency histograms below. Deliberately dense between 50ms
# and 500ms - Phase 5 baseline validation showed that the previous coarser
# buckets (a 250ms-500ms gap with zero real observations in it, since mock
# latency tops out at 300ms) made histogram_quantile's linear interpolation
# overshoot p95/p99 by 40-60% relative to the true client-observed values.
# Still covers up to 120s for slower real-model generation latency.
_LATENCY_BUCKETS_SECONDS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.075,
    0.1,
    0.15,
    0.2,
    0.25,
    0.3,
    0.4,
    0.5,
    0.75,
    1,
    1.5,
    2,
    3,
    5,
    7.5,
    10,
    15,
    20,
    30,
    45,
    60,
    90,
    120,
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled, labeled by method, route path template, and status code.",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds, labeled by method and route path template.",
    ["method", "path"],
    buckets=_LATENCY_BUCKETS_SECONDS,
)

INFERENCE_REQUESTS_TOTAL = Counter(
    "inference_requests_total",
    "Total /generate requests, labeled by backend and outcome (success/error).",
    ["backend", "status"],
)

INFERENCE_GENERATION_LATENCY_SECONDS = Histogram(
    "inference_generation_latency_seconds",
    "Time spent generating a completion, labeled by backend.",
    ["backend"],
    buckets=_LATENCY_BUCKETS_SECONDS,
)

INFERENCE_PROMPT_TOKENS_TOTAL = Counter(
    "inference_prompt_tokens_total",
    "Total prompt tokens processed, labeled by backend.",
    ["backend"],
)

INFERENCE_COMPLETION_TOKENS_TOTAL = Counter(
    "inference_completion_tokens_total",
    "Total completion tokens generated, labeled by backend.",
    ["backend"],
)

INFERENCE_BACKEND_INFO = Gauge(
    "inference_backend_info",
    "Static info about the active backend; value is always 1.",
    ["backend", "device", "model"],
)

INFERENCE_MODEL_LOAD_SECONDS = Gauge(
    "inference_model_load_seconds",
    "How long the active model took to load at startup, labeled by backend and model.",
    ["backend", "model"],
)

INFERENCE_GPU_MEMORY_ALLOCATED_BYTES = Gauge(
    "inference_gpu_memory_allocated_bytes",
    "GPU memory allocated after the most recent generation call, labeled by backend.",
    ["backend"],
)

INFERENCE_GPU_MEMORY_RESERVED_BYTES = Gauge(
    "inference_gpu_memory_reserved_bytes",
    "GPU memory reserved by the caching allocator after the most recent generation "
    "call, labeled by backend.",
    ["backend"],
)

INFERENCE_GPU_MEMORY_PEAK_BYTES = Gauge(
    "inference_gpu_memory_peak_bytes",
    "Peak GPU memory used during the most recent generation call, labeled by backend.",
    ["backend"],
)


def record_http_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    """Record one completed HTTP request.

    `path` must be the route's path *template* (e.g. "/generate"), not the
    raw request path, to keep the label's cardinality bounded even if
    parameterized routes are added later.
    """
    HTTP_REQUESTS_TOTAL.labels(method=method, path=path, status=str(status)).inc()
    HTTP_REQUEST_DURATION_SECONDS.labels(method=method, path=path).observe(duration_seconds)


def record_generation(
    backend: str,
    success: bool,
    duration_seconds: float,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    gpu_memory_allocated_mb: float | None = None,
    gpu_memory_reserved_mb: float | None = None,
    gpu_memory_peak_mb: float | None = None,
) -> None:
    """Record the outcome of one /generate call."""
    status = "success" if success else "error"
    INFERENCE_REQUESTS_TOTAL.labels(backend=backend, status=status).inc()

    if success:
        INFERENCE_GENERATION_LATENCY_SECONDS.labels(backend=backend).observe(duration_seconds)
        INFERENCE_PROMPT_TOKENS_TOTAL.labels(backend=backend).inc(prompt_tokens)
        INFERENCE_COMPLETION_TOKENS_TOTAL.labels(backend=backend).inc(completion_tokens)

    if gpu_memory_allocated_mb is not None:
        INFERENCE_GPU_MEMORY_ALLOCATED_BYTES.labels(backend=backend).set(
            gpu_memory_allocated_mb * _BYTES_PER_MB
        )
    if gpu_memory_reserved_mb is not None:
        INFERENCE_GPU_MEMORY_RESERVED_BYTES.labels(backend=backend).set(
            gpu_memory_reserved_mb * _BYTES_PER_MB
        )
    if gpu_memory_peak_mb is not None:
        INFERENCE_GPU_MEMORY_PEAK_BYTES.labels(backend=backend).set(
            gpu_memory_peak_mb * _BYTES_PER_MB
        )


def set_backend_info(backend: str, device: str, model: str) -> None:
    """Record which backend/device/model this process is running, once at startup."""
    INFERENCE_BACKEND_INFO.labels(backend=backend, device=device, model=model).set(1)


def set_model_load_seconds(backend: str, model: str, seconds: float) -> None:
    """Record how long the active model took to load, once at startup."""
    INFERENCE_MODEL_LOAD_SECONDS.labels(backend=backend, model=model).set(seconds)


def metrics_response() -> tuple[bytes, str]:
    """Return the current Prometheus exposition-format body and its content type."""
    return generate_latest(), CONTENT_TYPE_LATEST
