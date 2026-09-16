# AI Inference Reliability Lab

A production-like LLM inference system built to intentionally introduce
performance and infrastructure failures, diagnose them with metrics, fix
them, and benchmark the improvement.

This is a portfolio project developed in stages. **This README covers
Versions 0.1, 0.2, Phase 3A, and the first Phase 3 GPU benchmark results.**

## Version 0.1 scope

- FastAPI application with `GET /health` and `POST /generate`
- A mock inference backend standing in for a real model server
- Request latency measurement middleware
- Structured logging and centralized error handling
- A standalone async load-testing script (latency percentiles, throughput, error rate)
- Pytest test suite

## Version 0.2 scope

- A second, real inference backend using Hugging Face Transformers, selected
  via config alongside the existing mock backend (both are always available;
  neither was removed)
- The model is loaded once at process startup, not per request
- Real generated text, real token counts (from the tokenizer), real
  measured generation latency
- Tests for the new backend that only need a tiny (~3MB) model, not a
  multi-GB download
- Not included yet (planned for later versions): vLLM, Docker, Kubernetes,
  Prometheus/Grafana, GPU infrastructure, load balancing/scaling failures.

## Phase 3A scope: NVIDIA GPU awareness and performance metrics

- Automatic device selection for the Hugging Face backend: `auto` resolves to
  `cuda` if an NVIDIA GPU is available, otherwise `cpu` - the same backend
  code runs unmodified on a CPU-only laptop and on a cloud NVIDIA GPU.
- Startup logging of the selected device, CUDA availability, GPU name (if
  any), and the PyTorch CUDA build version.
- Every `/generate` response now reports `tokens_per_second`, for both
  backends.
- On a CUDA device, `/generate` responses also report GPU memory allocated,
  reserved, and peak-used (in MB) for that generation call; these are `null`
  on CPU or under the mock backend.
- New `tests/test_device.py` exercises the device-detection logic on any
  machine (via mocking `torch.cuda.is_available`) and skips a couple of
  real-GPU-memory checks when no CUDA GPU is present.
- Not included yet: actually running on a cloud GPU (Phase 3B), Docker,
  Kubernetes, Prometheus/Grafana, vLLM, cloud-provider-specific code.

## Architecture

```
Client
  │  HTTP
  ▼
FastAPI app (main.py)
  │
  ├─ TimingMiddleware        → measures & logs latency per request
  ├─ api/routes.py           → GET /health, POST /generate
  │     depends only on ──▶  backends/base.py (InferenceBackend interface)
  │
  ├─ backends/mock.py        → MockBackend (simulated latency + fake tokens)
  └─ backends/huggingface.py → HuggingFaceBackend (real local model via Transformers)
        │
        └─ backends/device.py → device detection, startup logging, GPU memory stats
```

The API layer never imports a concrete backend - only the abstract
`InferenceBackend` interface in `backends/base.py`. `main.py`'s
`build_backend()` picks the concrete implementation based on
`core/config.py` settings (`INFERENCE_LAB_BACKEND=mock` or
`INFERENCE_LAB_BACKEND=huggingface`). This means a future real backend
(e.g. vLLM) can be added as `backends/vllm_backend.py` implementing the
same interface, and switched on with one config value - no changes to
routes, schemas, or middleware. `torch`/`transformers` are only imported
when the `huggingface` backend is actually selected, so a mock-only install
never needs them.

## Project layout

```
src/inference_lab/
├── main.py                  # app factory, backend selection, exception handler
├── api/
│   ├── routes.py            # HTTP endpoints
│   └── schemas.py           # request/response models
├── backends/
│   ├── base.py              # InferenceBackend interface + GenerationResult
│   ├── mock.py               # MockBackend implementation
│   ├── huggingface.py       # HuggingFaceBackend implementation (real local model)
│   └── device.py             # CPU/CUDA detection, startup logging, GPU memory stats
├── core/
│   ├── config.py            # env-based settings
│   └── logging.py           # logging setup
└── middleware/
    └── timing.py             # per-request latency measurement

tests/                        # pytest suite
scripts/load_test.py          # async load-testing / benchmarking script
```

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

This installs everything needed for **mock mode** (the default). Mock mode
has no dependency on `torch` or `transformers` at all.

### Optional: real model mode

To use the Hugging Face backend, install the extra dependencies (`torch` +
`transformers`) as well:

```bash
pip install -e ".[dev,huggingface]"
```

## Running the server

**Mock mode** (default, no extra install needed):

```bash
uvicorn inference_lab.main:app --reload --port 8000
```

**Real model mode**, using the small `sshleifer/tiny-gpt2` model by default:

```bash
INFERENCE_LAB_BACKEND=huggingface uvicorn inference_lab.main:app --reload --port 8000
```

The model downloads once (cached by Hugging Face afterwards) and loads into
memory at startup - the first request after startup already uses the
already-loaded model, it does not reload per request. At startup you'll see
a log line like:

```
Inference device=cpu cuda_available=False gpu_name=None torch_cuda_version=None
```

(on a cloud GPU this becomes `device=cuda cuda_available=True gpu_name=NVIDIA ...`).

Either way:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me about reliability engineering.", "max_tokens": 32}'
```

The response includes `text`, `prompt_tokens`, `completion_tokens`,
`finish_reason`, `latency_ms`, and `tokens_per_second` regardless of which
backend is active - the API shape does not change between mock and real
model mode. `gpu_memory_allocated_mb`, `gpu_memory_reserved_mb`, and
`gpu_memory_peak_mb` are also always present, but are `null` unless running
the Hugging Face backend on a CUDA device.

### Configuration

All settings are environment variables prefixed `INFERENCE_LAB_` (see
`core/config.py` for the full list):

| Variable | Default | Meaning |
|---|---|---|
| `INFERENCE_LAB_BACKEND` | `mock` | `mock` or `huggingface` |
| `INFERENCE_LAB_LOG_LEVEL` | `INFO` | Python logging level |
| `INFERENCE_LAB_HUGGINGFACE_MODEL_NAME` | `sshleifer/tiny-gpt2` | Any causal-LM model on the Hugging Face Hub |
| `INFERENCE_LAB_HUGGINGFACE_DEVICE` | `auto` | `auto`, `cpu`, or `cuda` (see CPU vs. GPU below) |
| `INFERENCE_LAB_HUGGINGFACE_MAX_NEW_TOKENS_CAP` | `256` | Hard ceiling on tokens generated per request |

### CPU mode

This is what runs on a machine with no NVIDIA GPU - e.g. a laptop with an
Intel Iris Xe. With `INFERENCE_LAB_HUGGINGFACE_DEVICE=auto` (the default),
`backends/device.py` checks `torch.cuda.is_available()`, finds no CUDA GPU,
and resolves to `cpu`. The model loads onto the CPU and every request runs
through ordinary CPU tensor ops. No extra setup needed; this is what's been
tested locally throughout this project. GPU memory fields in the response
are always `null` here, since there's no GPU to measure.

### CUDA mode

On a machine with an NVIDIA GPU, driver, and CUDA-enabled `torch` build, the
same `auto` setting resolves to `cuda` instead - no code or config change
needed, just different hardware underneath. The model loads onto the GPU,
generation runs there, and the response's `gpu_memory_*` fields get real
values. Setting `INFERENCE_LAB_HUGGINGFACE_DEVICE=cuda` explicitly forces
CUDA and raises a clear error immediately if no CUDA GPU is actually
available, rather than silently falling back to CPU.

**Note:** this repository has been developed on a CPU-only machine (no
local NVIDIA GPU). The CUDA code path is written and unit tested (with
`torch.cuda` calls mocked - see `tests/test_device.py`); a real GPU
benchmark has since been run on a cloud NVIDIA GPU - see
[Phase 3 results](#phase-3-results) below.

## Phase 3 results

A real CPU-vs-GPU benchmark was run on a [RunPod](https://runpod.io) GPU
pod (NVIDIA A40) using the base `gpt2` model, the same prompt, and the same
100-token completion on both devices. Full details, the comparison table,
interpretation, and limitations are in
[`experiments/phase3_gpu_benchmark.md`](experiments/phase3_gpu_benchmark.md).

| Metric | CPU | NVIDIA A40 |
|---|---|---|
| `latency_ms` | 15262.6069 | 1167.2952 |
| `tokens_per_second` | 6.55196 | 85.66813 |
| `gpu_memory_allocated_mb` | null | 484.2148 |

The A40 achieved **~13.1x higher generation throughput** and **~92.4%
lower latency** than CPU for this workload. This is a single run on a
small (124M-parameter) model with no batching or concurrency - see the
full report for limitations before drawing broader conclusions.

### What Phase 3 measures

Every `/generate` call now reports:

- **Input tokens** (`prompt_tokens`) and **output tokens** (`completion_tokens`)
- **Generation latency** (`latency_ms`)
- **Tokens per second** (`tokens_per_second`) - `completion_tokens` divided
  by `latency_ms`, computed the same way for both backends
- **GPU memory allocated / reserved / peak** (`gpu_memory_*_mb`) - only
  populated when generation actually ran on a CUDA device; `null` on CPU or
  under the mock backend

Startup logging (once, when the Hugging Face backend loads) additionally
reports the selected device, whether CUDA is available, the GPU name if
any, and the PyTorch CUDA build version - useful for confirming exactly what
hardware a given benchmark run used.

### Why `sshleifer/tiny-gpt2`

It's a real (if tiny, ~3MB) GPT-2 architecture used across the Hugging Face
ecosystem for exactly this purpose: proving an inference pipeline works
end-to-end (load once, tokenize, generate, decode, real token counts) without
downloading a multi-GB model. Its output text is not coherent - that's
expected, it's not meaningfully trained - v0.2's goal is correct plumbing,
not good text. Swapping to a coherent model (e.g. `distilgpt2`, or later a
small Llama/Qwen variant on GPU) is just changing
`INFERENCE_LAB_HUGGINGFACE_MODEL_NAME`.

## Running tests

```bash
pytest
```

The Hugging Face backend tests (`tests/test_huggingface_backend.py`) are
automatically skipped if the `huggingface` extra isn't installed. When it
is installed, running them downloads `sshleifer/tiny-gpt2` (~3MB, cached
after the first run) - no large model download is required.

The device tests (`tests/test_device.py`) run on any machine, including a
CPU-only one: CPU/CUDA branching logic is tested via mocking
`torch.cuda.is_available`, while a couple of tests that need to read real
GPU memory are marked `skipif(not torch.cuda.is_available())` and will only
actually run once this project is on a machine with an NVIDIA GPU.

## Running the load test

With the server running in one terminal, run the load test in another:

```bash
python scripts/load_test.py --url http://localhost:8000 --requests 200 --concurrency 20
```

This reports total requests, success/failure counts, error rate, wall time,
throughput, and latency min/mean/p50/p95/p99/max. Use `--help` to see all
options (prompt text, max tokens, etc). This script is the tool used in
later versions to benchmark the effect of introduced failures and their
fixes.

## Linting

```bash
ruff check .
```

## Roadmap

- **Phase 3B and beyond**: Larger models, batching/concurrency benchmarks,
  and repeated runs to build on the single-run Phase 3 result above.
- **Later**: Add a vLLM backend, containerize with Docker, add Prometheus
  metrics and Grafana dashboards, Kubernetes deployment, GPU scheduling,
  deliberately induced failure scenarios (latency spikes, OOM,
  queueing/backpressure issues, autoscaling gaps) with before/after
  benchmarks.
