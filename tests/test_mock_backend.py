import logging
import time

import pytest

from inference_lab.backends.mock import MockBackend
from inference_lab.core.config import Settings


async def test_default_extra_latency_is_zero_and_unwarned(caplog):
    with caplog.at_level(logging.WARNING):
        backend = MockBackend(min_latency_ms=0, max_latency_ms=0)
        result = await backend.generate(prompt="hello", max_tokens=8)

    assert result.text
    assert not any("fault injection" in record.message for record in caplog.records)


async def test_extra_latency_adds_real_wall_clock_time():
    backend = MockBackend(min_latency_ms=0, max_latency_ms=0, extra_latency_ms=100)

    start = time.perf_counter()
    await backend.generate(prompt="hello", max_tokens=8)
    elapsed_ms = (time.perf_counter() - start) * 1000

    # Generous tolerance - this only needs to prove the extra delay is real,
    # not measure it precisely.
    assert elapsed_ms >= 90


async def test_extra_latency_logs_a_warning_when_active(caplog):
    with caplog.at_level(logging.WARNING):
        MockBackend(min_latency_ms=0, max_latency_ms=0, extra_latency_ms=500)

    assert any("fault injection" in record.message for record in caplog.records)
    assert any("500" in record.message for record in caplog.records)


async def test_zero_extra_latency_behaves_like_no_fault():
    baseline = MockBackend(min_latency_ms=10, max_latency_ms=10, extra_latency_ms=0)

    start = time.perf_counter()
    await baseline.generate(prompt="hello", max_tokens=8)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert elapsed_ms < 50  # comfortably under any injected fault, not just the 10ms draw


def test_settings_rejects_negative_extra_latency(monkeypatch):
    monkeypatch.setenv("INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS", "-1")

    with pytest.raises(ValueError):
        Settings()


def test_settings_default_extra_latency_is_zero(monkeypatch):
    monkeypatch.delenv("INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS", raising=False)

    assert Settings().mock_extra_latency_ms == 0
