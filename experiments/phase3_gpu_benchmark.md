# Phase 3 GPU Benchmark Report

Single-request CPU vs. NVIDIA A40 comparison for the Hugging Face backend
added in v0.2, using the device auto-detection and performance metrics added
in Phase 3A (`backends/device.py`, `/generate` response fields
`tokens_per_second` and `gpu_memory_*_mb`).

## Environment

| Field | Value |
|---|---|
| Cloud provider | RunPod |
| Environment type | RunPod GPU Pod, PyTorch template |
| GPU | NVIDIA A40, 48 GB VRAM |
| NVIDIA driver version | 580.159.04 |
| CUDA level reported by `nvidia-smi` | 13.0 |
| PyTorch CUDA build (`torch.version.cuda`) | 12.8 |
| CPU | Not recorded |
| PyTorch package version | Not recorded |

**Note on the two CUDA numbers:** `nvidia-smi` reports the maximum CUDA
version the installed driver *supports* (13.0). `torch.version.cuda`
reports the CUDA version the installed PyTorch *build* actually targets
(12.8). These are expected to differ and both are valid to report - the
driver being newer than the PyTorch build's target CUDA version is normal
and not an error condition.

The CPU run was performed on the **same RunPod pod** as the GPU run, by
changing only `INFERENCE_LAB_HUGGINGFACE_DEVICE=cpu` (GPU run used the
default `auto`, which resolved to `cuda` on this pod). Everything else -
cloud environment, model, prompt, code path - was identical between the two
runs; the only variable was the compute device.

## Model

`gpt2` - the base Hugging Face GPT-2 checkpoint (124M parameters), set via:

```
INFERENCE_LAB_HUGGINGFACE_MODEL_NAME=gpt2
```

Not `distilgpt2`, not `sshleifer/tiny-gpt2` (the default used for v0.2's
plumbing tests) - this benchmark used the real base GPT-2 model.

## Prompt

```
Explain artificial intelligence simply.
```

Identical prompt used for both the CPU and GPU run.

## Workload

| Field | Value |
|---|---|
| `max_tokens` requested | 100 |
| `completion_tokens` generated | 100 (both runs) |
| Endpoint | `POST /generate` |
| Code path | Identical for both runs (`HuggingFaceBackend`) - only `INFERENCE_LAB_HUGGINGFACE_DEVICE` changed |

## CPU result

| Field | Value |
|---|---|
| `latency_ms` | 15262.6069 |
| `tokens_per_second` | 6.55196 |
| `gpu_memory_allocated_mb` | null |
| `gpu_memory_reserved_mb` | null |
| `gpu_memory_peak_mb` | null |

## GPU result (NVIDIA A40)

| Field | Value |
|---|---|
| `latency_ms` | 1167.2952 |
| `tokens_per_second` | 85.66813 |
| `gpu_memory_allocated_mb` | 484.2148 |
| `gpu_memory_reserved_mb` | 540.0 |
| `gpu_memory_peak_mb` | 492.8276 |

## Comparison table

| Metric | CPU | NVIDIA A40 | Change |
|---|---|---|---|
| `latency_ms` | 15262.6069 | 1167.2952 | ~13.1x lower (~92.4% reduction) |
| `tokens_per_second` | 6.55196 | 85.66813 | ~13.1x higher |
| `gpu_memory_allocated_mb` | null | 484.2148 | n/a - no GPU on CPU run |
| `gpu_memory_reserved_mb` | null | 540.0 | n/a |
| `gpu_memory_peak_mb` | null | 492.8276 | n/a |

## Interpretation

- For the same 100-token completion of the same prompt with the same
  `gpt2` model, the A40 generated tokens about **13.1x faster** than CPU
  (85.66813 vs. 6.55196 tokens/sec), and total request latency dropped by
  about **92.4%** (15.26 seconds to 1.17 seconds).
- This is a large, expected gap for autoregressive generation: each of the
  100 output tokens requires a full forward pass through the model, and a
  GPU's parallel matrix-multiply throughput vastly outperforms CPU for this
  workload even at gpt2's small (124M parameter) size.
- GPU memory usage for `gpt2` was small relative to the A40's 48GB of VRAM
  (under 500MB allocated and at peak) - unsurprising for a 124M-parameter
  model, but it confirms the memory-measurement code added in Phase 3A
  (`get_gpu_memory_stats`) reads real, sane values from the actual device
  rather than placeholder numbers.
- Reserved memory (540.0MB) being somewhat higher than allocated
  (484.2148MB) reflects PyTorch's caching allocator holding a bit of extra
  memory rather than releasing it back to the driver immediately - normal
  allocator behavior, not a leak.
- Functionally, this run validates the Phase 3A device-detection work
  end-to-end: the identical `HuggingFaceBackend` code, unmodified, ran
  correctly on both CPU and CUDA on the same machine, and the API correctly
  reported `null` GPU-memory fields on CPU and real ones on GPU.

## Limitations

- **Single run, no repeats.** Each device was measured exactly once. There
  is no averaging, no variance/error bars, and no attempt to characterize
  run-to-run noise (e.g. thermal state, other tenants on the shared pod,
  cold-start effects on either device).
- **Small model.** `gpt2` (124M parameters) is far smaller than the models
  this project will eventually target. The GPU-vs-CPU advantage generally
  grows with model size (more parallel work per token), so this ~13x figure
  should not be extrapolated to larger models without separately measuring
  them.
- **No batching or concurrency.** Both runs measured a single sequential
  request end-to-end. Production serving workloads involve concurrent
  requests and batched generation, which change GPU utilization and
  effective throughput substantially - none of that is captured here.
- **CPU hardware not recorded.** The exact CPU model on the RunPod pod was
  not recorded, so the CPU-side numbers cannot be tied to specific CPU
  hardware for reproduction on a different machine.
- **Exact PyTorch package version not recorded.** Only the CUDA build
  version PyTorch reported (`torch.version.cuda` = 12.8) is known; the full
  `torch` package version (e.g. `2.x.y`) was not recorded.
- **Default generation settings only.** No exploration of sampling
  parameters, batch sizes, or prompt/output lengths beyond this one fixed
  100-token workload.

## Reproducing this run

```bash
pip install -e ".[dev,huggingface]"
INFERENCE_LAB_HUGGINGFACE_MODEL_NAME=gpt2 \
INFERENCE_LAB_HUGGINGFACE_DEVICE=auto \
  uvicorn inference_lab.main:app --port 8000
# then, on a machine with an NVIDIA GPU, INFERENCE_LAB_HUGGINGFACE_DEVICE=auto
# resolves to cuda; set INFERENCE_LAB_HUGGINGFACE_DEVICE=cpu to force CPU.

curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain artificial intelligence simply.", "max_tokens": 100}'
```
