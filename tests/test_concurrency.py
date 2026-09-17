import asyncio
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from inference_lab.core.config import Settings, settings
from inference_lab.main import create_app
from inference_lab.observability.metrics import (
    INFERENCE_GENERATIONS_ACTIVE,
    INFERENCE_REQUESTS_IN_FLIGHT,
)


def _gauge_value(gauge) -> float:
    return gauge.collect()[0].samples[0].value


def _build_client(monkeypatch, *, max_concurrent_generations, min_latency_ms=0, max_latency_ms=0):
    monkeypatch.setattr(settings, "max_concurrent_generations", max_concurrent_generations)
    monkeypatch.setattr(settings, "mock_min_latency_ms", min_latency_ms)
    monkeypatch.setattr(settings, "mock_max_latency_ms", max_latency_ms)
    app = create_app()
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_default_unlimited_behavior_is_unchanged(monkeypatch):
    """max_concurrent_generations=0 (the default) must behave exactly like
    pre-Phase-8: no semaphore, every request succeeds, gauges settle to 0."""
    async with _build_client(monkeypatch, max_concurrent_generations=0) as client:
        responses = await asyncio.gather(
            *(client.post("/generate", json={"prompt": "hi", "max_tokens": 4}) for _ in range(10))
        )

    assert all(r.status_code == 200 for r in responses)
    assert _gauge_value(INFERENCE_GENERATIONS_ACTIVE) == 0
    assert _gauge_value(INFERENCE_REQUESTS_IN_FLIGHT) == 0


async def test_semaphore_bounds_concurrent_generations_and_the_rest_queue(monkeypatch):
    """With a limit of 2 and 6 concurrent requests (each held open ~100ms by
    the mock backend), inference_generations_active must never exceed 2,
    while inference_requests_in_flight should reach well above 2 - the gap
    is the queued-but-not-yet-executing requests."""
    peak_active = 0
    peak_in_flight = 0
    stop_event = asyncio.Event()

    async def poll() -> None:
        nonlocal peak_active, peak_in_flight
        while not stop_event.is_set():
            peak_active = max(peak_active, _gauge_value(INFERENCE_GENERATIONS_ACTIVE))
            peak_in_flight = max(peak_in_flight, _gauge_value(INFERENCE_REQUESTS_IN_FLIGHT))
            await asyncio.sleep(0.005)

    async with _build_client(
        monkeypatch, max_concurrent_generations=2, min_latency_ms=100, max_latency_ms=100
    ) as client:
        poller = asyncio.create_task(poll())
        responses = await asyncio.gather(
            *(client.post("/generate", json={"prompt": "hi", "max_tokens": 4}) for _ in range(6))
        )
        stop_event.set()
        await poller

    assert all(r.status_code == 200 for r in responses)
    assert peak_active <= 2
    assert peak_in_flight >= 3  # more requests queued than could execute at once
    assert _gauge_value(INFERENCE_GENERATIONS_ACTIVE) == 0
    assert _gauge_value(INFERENCE_REQUESTS_IN_FLIGHT) == 0


async def test_warning_logged_when_limit_active(monkeypatch, caplog):
    monkeypatch.setattr(settings, "max_concurrent_generations", 5)

    with caplog.at_level(logging.WARNING):
        create_app()

    assert any("concurrency limit active" in record.message for record in caplog.records)
    assert any("5" in record.message for record in caplog.records)


async def test_no_warning_when_unlimited(monkeypatch, caplog):
    monkeypatch.setattr(settings, "max_concurrent_generations", 0)

    with caplog.at_level(logging.WARNING):
        create_app()

    assert not any("concurrency limit active" in record.message for record in caplog.records)


def test_settings_rejects_negative_max_concurrent_generations(monkeypatch):
    monkeypatch.setenv("INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS", "-1")

    with pytest.raises(ValueError):
        Settings()


def test_settings_default_max_concurrent_generations_is_zero(monkeypatch):
    monkeypatch.delenv("INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS", raising=False)

    assert Settings().max_concurrent_generations == 0
