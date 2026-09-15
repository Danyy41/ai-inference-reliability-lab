# AI Inference Reliability Lab

A production-like LLM inference system built to intentionally introduce
performance and infrastructure failures, diagnose them with metrics, fix
them, and benchmark the improvement.

This is a portfolio project developed in stages. **This README covers
Versions 0.1 and 0.2.**

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
│   └── huggingface.py       # HuggingFaceBackend implementation (real local model)
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
already-loaded model, it does not reload per request.

Either way:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me about reliability engineering.", "max_tokens": 32}'
```

The response includes `text`, `prompt_tokens`, `completion_tokens`,
`finish_reason`, and `latency_ms` regardless of which backend is active -
the API shape does not change between mock and real model mode.

### Configuration

All settings are environment variables prefixed `INFERENCE_LAB_` (see
`core/config.py` for the full list):

| Variable | Default | Meaning |
|---|---|---|
| `INFERENCE_LAB_BACKEND` | `mock` | `mock` or `huggingface` |
| `INFERENCE_LAB_LOG_LEVEL` | `INFO` | Python logging level |
| `INFERENCE_LAB_HUGGINGFACE_MODEL_NAME` | `sshleifer/tiny-gpt2` | Any causal-LM model on the Hugging Face Hub |
| `INFERENCE_LAB_HUGGINGFACE_DEVICE` | `cpu` | `cpu` or `cuda` (see CPU vs. GPU below) |
| `INFERENCE_LAB_HUGGINGFACE_MAX_NEW_TOKENS_CAP` | `256` | Hard ceiling on tokens generated per request |

### CPU vs. GPU

- **CPU** (the default): works everywhere with no extra setup. Generation is
  slow relative to a GPU, but that's fine for the tiny default model used
  here for local development.
- **GPU**: set `INFERENCE_LAB_HUGGINGFACE_DEVICE=cuda` to run the model on an
  NVIDIA GPU (requires a CUDA-capable GPU, drivers, and a CUDA-enabled
  `torch` build). A GPU does the matrix math generation needs in parallel,
  which matters a lot for larger models - not for `tiny-gpt2`, but it will
  for the bigger models this project moves to later. No GPU scheduling,
  containers, or orchestration are set up yet; this is just a device switch.

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

- **v0.3+**: Add a vLLM backend, containerize with Docker, add Prometheus
  metrics and Grafana dashboards.
- **Later**: Kubernetes deployment, GPU scheduling, deliberately induced
  failure scenarios (latency spikes, OOM, queueing/backpressure issues,
  autoscaling gaps) with before/after benchmarks.
