import pytest

pytest.importorskip("torch")

import torch  # noqa: E402

from inference_lab.backends.device import (  # noqa: E402
    detect_device,
    get_device_info,
    get_gpu_memory_stats,
    reset_gpu_memory_stats,
)


def test_detect_device_auto_falls_back_to_cpu_without_cuda(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    assert detect_device("auto") == "cpu"


def test_detect_device_auto_selects_cuda_when_available(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    assert detect_device("auto") == "cuda"


def test_detect_device_explicit_cpu_ignores_cuda_availability(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

    assert detect_device("cpu") == "cpu"


def test_detect_device_explicit_cuda_raises_when_unavailable(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    with pytest.raises(RuntimeError):
        detect_device("cuda")


def test_detect_device_rejects_unknown_value():
    with pytest.raises(ValueError):
        detect_device("tpu")


def test_get_device_info_reports_cuda_unavailable(monkeypatch):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)

    info = get_device_info("cpu")

    assert info == {
        "device": "cpu",
        "cuda_available": False,
        "gpu_name": None,
        "torch_cuda_version": None,
    }


def test_get_gpu_memory_stats_is_none_on_cpu():
    assert get_gpu_memory_stats("cpu") is None


def test_reset_gpu_memory_stats_is_a_noop_on_cpu():
    # Must not raise or touch CUDA when there is no GPU.
    reset_gpu_memory_stats("cpu")


@pytest.mark.skipif(not torch.cuda.is_available(), reason="No CUDA GPU available")
def test_get_device_info_reports_real_gpu_when_cuda_available():
    info = get_device_info("cuda")

    assert info["cuda_available"] is True
    assert info["gpu_name"]
    assert info["torch_cuda_version"]


@pytest.mark.skipif(not torch.cuda.is_available(), reason="No CUDA GPU available")
def test_get_gpu_memory_stats_reports_real_values_on_cuda():
    reset_gpu_memory_stats("cuda")
    tensor = torch.zeros(1024, 1024, device="cuda")  # noqa: F841

    stats = get_gpu_memory_stats("cuda")

    assert stats is not None
    assert stats["gpu_memory_allocated_mb"] > 0
    assert stats["gpu_memory_reserved_mb"] >= stats["gpu_memory_allocated_mb"]
    assert stats["gpu_memory_peak_mb"] >= stats["gpu_memory_allocated_mb"]
