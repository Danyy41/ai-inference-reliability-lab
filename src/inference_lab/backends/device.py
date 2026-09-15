import logging

import torch

logger = logging.getLogger(__name__)


def detect_device(requested: str) -> str:
    """Resolve a requested device setting to an actual torch device string.

    "auto" picks CUDA when available, else CPU - this is what lets the same
    backend code run unmodified on a CPU-only laptop and on a cloud NVIDIA
    GPU. Explicit "cpu"/"cuda" are honored as-is, with "cuda" failing fast
    if no CUDA GPU is actually available rather than silently falling back.
    """
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA device requested but no CUDA GPU is available")
        return "cuda"
    raise ValueError(f"Unknown device: {requested}")


def get_device_info(device: str) -> dict:
    """Collect diagnostic info about the resolved device and CUDA environment."""
    cuda_available = torch.cuda.is_available()
    return {
        "device": device,
        "cuda_available": cuda_available,
        "gpu_name": torch.cuda.get_device_name(0) if cuda_available else None,
        "torch_cuda_version": torch.version.cuda if cuda_available else None,
    }


def log_device_info(device: str) -> None:
    """Log the resolved device and CUDA environment once at startup."""
    info = get_device_info(device)
    logger.info(
        "Inference device=%s cuda_available=%s gpu_name=%s torch_cuda_version=%s",
        info["device"],
        info["cuda_available"],
        info["gpu_name"],
        info["torch_cuda_version"],
    )


def reset_gpu_memory_stats(device: str) -> None:
    """Reset peak-memory tracking before a generation call, if on CUDA."""
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()


def get_gpu_memory_stats(device: str) -> dict | None:
    """Return current/peak GPU memory usage in MB, or None off CUDA."""
    if device != "cuda":
        return None
    bytes_per_mb = 1024 * 1024
    return {
        "gpu_memory_allocated_mb": torch.cuda.memory_allocated() / bytes_per_mb,
        "gpu_memory_reserved_mb": torch.cuda.memory_reserved() / bytes_per_mb,
        "gpu_memory_peak_mb": torch.cuda.max_memory_allocated() / bytes_per_mb,
    }
