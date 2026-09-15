from unittest.mock import AsyncMock

from inference_lab.backends.base import GenerationResult


async def test_generate_returns_completion(client):
    response = await client.post("/generate", json={"prompt": "hello world", "max_tokens": 16})

    assert response.status_code == 200
    body = response.json()
    assert body["text"]
    assert body["prompt_tokens"] == 2
    assert body["completion_tokens"] >= 1
    assert body["finish_reason"] in {"stop", "length"}
    assert body["latency_ms"] >= 0
    assert "X-Process-Time-Ms" in response.headers


async def test_generate_uses_default_max_tokens(client):
    response = await client.post("/generate", json={"prompt": "hi"})

    assert response.status_code == 200


async def test_generate_rejects_empty_prompt(client):
    response = await client.post("/generate", json={"prompt": "", "max_tokens": 16})

    assert response.status_code == 422


async def test_generate_rejects_missing_prompt(client):
    response = await client.post("/generate", json={"max_tokens": 16})

    assert response.status_code == 422


async def test_generate_rejects_too_many_max_tokens(client):
    response = await client.post("/generate", json={"prompt": "hi", "max_tokens": 999999})

    assert response.status_code == 422


async def test_generate_returns_502_on_backend_failure(app, client):
    app.state.backend.generate = AsyncMock(side_effect=RuntimeError("boom"))

    response = await client.post("/generate", json={"prompt": "hi", "max_tokens": 16})

    assert response.status_code == 502
    assert response.json() == {"detail": "Inference backend failed"}


async def test_generate_result_dataclass_defaults():
    result = GenerationResult(text="hi", prompt_tokens=1, completion_tokens=1)
    assert result.finish_reason == "stop"
