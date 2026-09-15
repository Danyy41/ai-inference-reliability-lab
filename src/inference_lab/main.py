import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.requests import Request

from inference_lab.api.routes import router
from inference_lab.backends.base import InferenceBackend
from inference_lab.backends.mock import MockBackend
from inference_lab.core.config import settings
from inference_lab.core.logging import configure_logging
from inference_lab.middleware.timing import TimingMiddleware

configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


def build_backend() -> InferenceBackend:
    """Select the inference backend implementation based on config.

    This is the single place that decides which backend the app runs with.
    Adding a real backend later (e.g. vLLM) means adding a branch here and
    an implementation class - the API and middleware layers are untouched.
    """
    if settings.backend == "mock":
        return MockBackend(
            min_latency_ms=settings.mock_min_latency_ms,
            max_latency_ms=settings.mock_max_latency_ms,
        )
    raise ValueError(f"Unknown backend: {settings.backend}")


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name)

    app.add_middleware(TimingMiddleware)
    app.include_router(router)
    app.state.backend = build_backend()

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    return app


app = create_app()
