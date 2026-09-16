from prometheus_client.parser import text_string_to_metric_families


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
