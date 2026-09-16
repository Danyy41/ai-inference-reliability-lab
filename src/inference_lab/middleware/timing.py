import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from inference_lab.observability.metrics import record_http_request

logger = logging.getLogger(__name__)


class TimingMiddleware(BaseHTTPMiddleware):
    """Measures wall-clock latency for every request, logs it, and records it
    as Prometheus metrics.

    Metrics are labeled by the route's path *template* (e.g. "/generate"),
    resolved from request.scope["route"] after routing has run - not the raw
    request path - so a bad/unmatched URL can't create unbounded label
    cardinality. Unmatched requests (404s) are labeled "unmatched" instead.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_seconds = time.perf_counter() - start
        duration_ms = duration_seconds * 1000

        route = request.scope.get("route")
        path_label = route.path if route is not None else "unmatched"

        response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"
        logger.info(
            "request method=%s path=%s status=%d duration_ms=%.2f",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        record_http_request(
            method=request.method,
            path=path_label,
            status=response.status_code,
            duration_seconds=duration_seconds,
        )
        return response
