# AI Inference Reliability Lab

A production-like LLM inference system built to intentionally introduce
performance and infrastructure failures, diagnose them with metrics, fix
them, and benchmark the improvement.

This is a portfolio project developed in stages. **This README covers
Version 0.1**, the foundation everything else builds on.

## Version 0.1 scope

- FastAPI application with `GET /health` and `POST /generate`
- A mock inference backend standing in for a real model server
- Request latency measurement middleware
- Structured logging and centralized error handling
- A standalone async load-testing script (latency percentiles, throughput, error rate)
- Pytest test suite
- Not included yet (planned for later versions): vLLM, Docker, Kubernetes,
  Prometheus/Grafana, GPU inference, load balancing/scaling failures.

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
  └─ backends/mock.py        → MockBackend (simulated latency + fake tokens)
```

The API layer never imports a concrete backend - only the abstract
`InferenceBackend` interface in `backends/base.py`. `main.py` picks the
concrete implementation based on `core/config.py` settings. This means a
future real backend (e.g. vLLM) can be added as `backends/vllm_backend.py`
implementing the same interface, and switched on with one config value -
no changes to routes, schemas, or middleware.

## Project layout

```
src/inference_lab/
├── main.py                  # app factory, backend selection, exception handler
├── api/
│   ├── routes.py            # HTTP endpoints
│   └── schemas.py           # request/response models
├── backends/
│   ├── base.py              # InferenceBackend interface + GenerationResult
│   └── mock.py               # MockBackend implementation
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

## Running the server

```bash
uvicorn inference_lab.main:app --reload --port 8000
```

Then:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me about reliability engineering.", "max_tokens": 32}'
```

Configuration is via environment variables prefixed `INFERENCE_LAB_`, e.g.
`INFERENCE_LAB_BACKEND=mock`, `INFERENCE_LAB_LOG_LEVEL=DEBUG` (see
`core/config.py` for all options).

## Running tests

```bash
pytest
```

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

- **v0.2+**: Replace the mock backend with a real model server (vLLM),
  containerize with Docker, add Prometheus metrics and Grafana dashboards.
- **Later**: Kubernetes deployment, GPU scheduling, deliberately induced
  failure scenarios (latency spikes, OOM, queueing/backpressure issues,
  autoscaling gaps) with before/after benchmarks.
