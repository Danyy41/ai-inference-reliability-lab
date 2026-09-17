from prometheus_client import CollectorRegistry, Histogram, generate_latest
from prometheus_client.parser import text_string_to_metric_families

from inference_lab.observability.metrics import _LATENCY_BUCKETS_SECONDS


def _parse(body: str) -> dict:
    """Parse Prometheus exposition text into {sample_name: [sample, ...]}.

    Indexed by the exact sample name (e.g. "http_requests_total"), not the
    family name - the parser strips suffixes like "_total" from the family
    name but keeps them on the individual samples.
    """
    samples_by_name: dict = {}
    for family in text_string_to_metric_families(body):
        for sample in family.samples:
            samples_by_name.setdefault(sample.name, []).append(sample)
    return samples_by_name


async def test_metrics_endpoint_returns_prometheus_format(client):
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    samples = _parse(response.text)
    assert "http_requests_total" in samples
    assert "http_request_duration_seconds_bucket" in samples
    assert "inference_requests_total" in samples
    assert "inference_generation_latency_seconds_bucket" in samples
    assert "inference_backend_info" in samples
    assert "inference_requests_in_flight" in samples
    assert "inference_generations_active" in samples


async def test_in_flight_and_active_gauges_settle_to_zero_after_a_request(client):
    await client.post("/generate", json={"prompt": "gauge settle test", "max_tokens": 8})

    samples = _parse((await client.get("/metrics")).text)
    assert samples["inference_requests_in_flight"][0].value == 0
    assert samples["inference_generations_active"][0].value == 0


async def test_backend_info_reported_at_startup(client):
    response = await client.get("/metrics")

    samples = _parse(response.text)["inference_backend_info"]
    assert any(s.labels.get("backend") == "mock" and s.value == 1 for s in samples)


async def test_generate_increments_inference_metrics(client):
    await client.post("/generate", json={"prompt": "metrics test", "max_tokens": 8})

    samples = _parse((await client.get("/metrics")).text)

    mock_success = [
        s
        for s in samples["inference_requests_total"]
        if s.labels.get("backend") == "mock" and s.labels.get("status") == "success"
    ]
    assert mock_success and mock_success[0].value >= 1

    token_samples = [
        s for s in samples["inference_completion_tokens_total"] if s.labels.get("backend") == "mock"
    ]
    assert token_samples and token_samples[0].value >= 1


async def test_http_metrics_use_route_path_template_not_raw_path(client):
    await client.post("/generate", json={"prompt": "hi", "max_tokens": 4})

    samples = _parse((await client.get("/metrics")).text)["http_requests_total"]
    paths = {s.labels.get("path") for s in samples}

    assert "/generate" in paths
    # No raw/unexpected path variants should leak in for a route with no parameters.
    assert all(p in {"/generate", "/health", "/metrics", "unmatched"} for p in paths)


async def test_metrics_endpoint_itself_is_recorded_as_http_request(client):
    await client.get("/metrics")

    samples = _parse((await client.get("/metrics")).text)["http_requests_total"]
    metrics_path_samples = [s for s in samples if s.labels.get("path") == "/metrics"]

    assert metrics_path_samples and metrics_path_samples[0].value >= 1


# --- Phase 7 regression test: histogram bucket resolution -------------------
#
# Phase 6's +500ms fault experiment produced latencies uniform on
# [550ms, 800ms]. The pre-Phase-7 buckets had only 0.75s and 1.0s as
# boundaries in that range, so 100% of observations fell into two
# 250ms-wide buckets and histogram_quantile's linear interpolation (which
# assumes observations are spread evenly across a bucket) overshot the real
# p95/p99 by 130-170ms. See experiments/phase7_histogram_fix.md for the full
# diagnosis. These tests reproduce the bug against the OLD buckets and prove
# the NEW buckets keep the same interpolation error small, using a
# deterministic synthetic distribution (no randomness, so no flakiness).

_OLD_BUCKETS_SECONDS_WITH_500MS_1S_GAP = (
    0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5,
    0.75, 1, 1.5, 2, 3, 5, 7.5, 10, 15, 20, 30, 45, 60, 90, 120,
)  # fmt: skip


def _linspace(start: float, stop: float, num: int) -> list[float]:
    step = (stop - start) / (num - 1)
    return [start + step * i for i in range(num)]


def _true_percentile(sorted_values: list[float], p: float) -> float:
    """Linear-interpolation percentile of an already-sorted sample (the same
    convention scripts/load_test.py uses), used here as ground truth."""
    k = (len(sorted_values) - 1) * (p / 100)
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _histogram_quantile(buckets: list[tuple[float, float]], p: float) -> float:
    """Re-implements Prometheus's histogram_quantile linear interpolation for
    a classic histogram, given (le, cumulative_count) pairs sorted ascending
    by le - the exact algorithm responsible for the Phase 6 distortion."""
    total_count = buckets[-1][1]
    rank = total_count * p
    lower_le, lower_count = 0.0, 0.0
    for le, cumulative_count in buckets:
        if cumulative_count >= rank:
            if cumulative_count == lower_count:
                return le
            fraction = (rank - lower_count) / (cumulative_count - lower_count)
            return lower_le + (le - lower_le) * fraction
        lower_le, lower_count = le, cumulative_count
    return buckets[-1][0]


def _observe_and_extract_buckets(name: str, buckets: tuple[float, ...], values: list[float]):
    registry = CollectorRegistry()
    histogram = Histogram(name, "test histogram", buckets=buckets, registry=registry)
    for v in values:
        histogram.observe(v)

    body = generate_latest(registry).decode()
    bucket_samples = [
        s
        for family in text_string_to_metric_families(body)
        for s in family.samples
        if s.name == f"{name}_bucket"
    ]
    return sorted(((float(s.labels["le"]), s.value) for s in bucket_samples), key=lambda pr: pr[0])


def test_new_buckets_keep_p95_p99_interpolation_error_small_for_the_phase6_fault_shape():
    values = _linspace(0.55, 0.80, 1000)  # matches Phase 6's ~[550ms, 800ms] fault distribution
    buckets = _observe_and_extract_buckets(
        "test_new_latency_seconds", _LATENCY_BUCKETS_SECONDS, values
    )

    for p, tolerance_ms in [(0.95, 30), (0.99, 30)]:
        estimated = _histogram_quantile(buckets, p)
        true_value = _true_percentile(values, p * 100)
        error_ms = abs(estimated - true_value) * 1000
        assert error_ms <= tolerance_ms, (
            f"p{p * 100:.0f} interpolation error {error_ms:.1f}ms exceeds "
            f"{tolerance_ms}ms tolerance (estimated={estimated * 1000:.1f}ms, "
            f"true={true_value * 1000:.1f}ms)"
        )


def test_old_buckets_would_have_overshot_p99_for_the_phase6_fault_shape():
    """Documents the bug this phase fixed: with the pre-Phase-7 bucket
    boundaries, the same distribution produces a much larger interpolation
    error - the empirical reproduction of the real Phase 6 distortion."""
    values = _linspace(0.55, 0.80, 1000)
    buckets = _observe_and_extract_buckets(
        "test_old_latency_seconds", _OLD_BUCKETS_SECONDS_WITH_500MS_1S_GAP, values
    )

    estimated = _histogram_quantile(buckets, 0.99)
    true_value = _true_percentile(values, 99)
    error_ms = abs(estimated - true_value) * 1000

    # Asserts the bug is real (error clearly exceeds the 30ms target for the
    # new buckets), not a specific value, so it stays robust to minor
    # arithmetic tweaks elsewhere.
    assert error_ms > 100
