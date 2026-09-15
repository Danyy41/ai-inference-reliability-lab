import pytest

pytest.importorskip("transformers")
pytest.importorskip("torch")

from inference_lab.backends.huggingface import HuggingFaceBackend  # noqa: E402

TINY_MODEL = "sshleifer/tiny-gpt2"


@pytest.fixture(scope="module")
def tiny_backend():
    return HuggingFaceBackend(model_name=TINY_MODEL, device="cpu", max_new_tokens_cap=32)


async def test_generate_returns_real_text_and_token_counts(tiny_backend):
    result = await tiny_backend.generate(prompt="Hello world", max_tokens=8)

    assert isinstance(result.text, str)
    assert result.prompt_tokens > 0
    assert 0 < result.completion_tokens <= 8
    assert result.finish_reason in {"stop", "length"}


async def test_generate_has_no_gpu_memory_fields_on_cpu(tiny_backend):
    result = await tiny_backend.generate(prompt="Hello world", max_tokens=8)

    assert result.gpu_memory_allocated_mb is None
    assert result.gpu_memory_reserved_mb is None
    assert result.gpu_memory_peak_mb is None


async def test_generate_respects_max_tokens_cap(tiny_backend):
    result = await tiny_backend.generate(prompt="Hi", max_tokens=1000)

    # max_new_tokens_cap=32 on the fixture backend must win over the larger request.
    assert result.completion_tokens <= 32


async def test_generate_marks_length_when_cap_hit(tiny_backend):
    result = await tiny_backend.generate(prompt="Hi", max_tokens=4)

    assert result.completion_tokens == 4
    assert result.finish_reason == "length"
