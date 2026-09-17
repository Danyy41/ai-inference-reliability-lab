# AI Inference Reliability Lab

A production-like LLM inference system built to intentionally introduce
performance and infrastructure failures, diagnose them with metrics, fix
them, and benchmark the improvement.

This is a portfolio project developed in stages. **This README covers
Versions 0.1, 0.2, Phase 3A, the first Phase 3 GPU benchmark results,
Phase 4A (Docker containerization), Phase 4B (Prometheus + Grafana
observability), Phase 5 (the healthy-baseline load test), Phase 6
(controlled latency fault injection), and Phase 7 (the histogram bucket
resolution fix).**

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
- Not included yet (planned for later versions): vLLM, Kubernetes,
  GPU infrastructure, load balancing/scaling failures.

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
- Not included yet: Kubernetes, vLLM, cloud-provider-specific code.

## Phase 4A scope: Docker containerization

- A two-stage `Dockerfile` that packages the existing FastAPI service
  unchanged - no API, schema, or backend behavior changes.
- Both the mock backend and the Hugging Face **CPU** backend work inside
  the container; which one runs is still chosen entirely by the
  `INFERENCE_LAB_BACKEND` environment variable at `docker run` time, exactly
  as it is outside Docker.
- A container `HEALTHCHECK` against `GET /health`.
- Model weights and the Hugging Face cache are never baked into the image;
  `.env` files and other secrets are excluded via `.dockerignore`.
- `scripts/docker_smoke_test.sh` builds the image, runs it, waits for the
  health check, and hits `/health` and `/generate` to prove the container
  actually works.
- Deliberately designed so an NVIDIA/CUDA variant can be added later as an
  isolated change (new base image + CUDA torch build) rather than a
  rewrite - see "CPU vs. future GPU Docker image" below.
- Not included yet: actually building the GPU/CUDA image, Kubernetes, vLLM,
  failure injection.

## Phase 4B scope: Prometheus + Grafana observability

- Every request is now instrumented: a `GET /metrics` endpoint exposes
  Prometheus-format metrics alongside the existing `/health` and
  `/generate` (same port, no Docker/API surface change).
- HTTP-level metrics (request counts, latency histograms) are recorded by
  the existing `TimingMiddleware`, labeled by the route's **path template**
  (e.g. `/generate`), not the raw request path - see "HTTP path labels"
  below.
- Inference-level metrics (request outcome, generation latency, prompt/
  completion token counters, active backend/device/model, GPU memory) are
  recorded in `/generate`, backend-agnostic - mock and Hugging Face both
  populate the same metric names.
- A new `docker-compose.yml` runs the existing `inference-lab` image
  together with `prometheus` and `grafana` containers - still no
  Kubernetes.
- Prometheus's own scrape of `/metrics` is excluded from the Grafana
  dashboard's traffic/error-rate panels, so polling doesn't inflate the
  apparent request rate (see "Observability" below).
- A Prometheus datasource and a starter Grafana dashboard are
  pre-provisioned - no manual clicking needed after `docker compose up`.
- Not included yet: failure injection, Kubernetes, vLLM, GPU Docker.

## Phase 5 scope: healthy baseline

- Establishes the reference measurement every later failure-injection
  experiment gets compared against - a load-test run with no failures
  injected.
- `scripts/run_experiment.sh` + `scripts/capture_prometheus_metrics.py`:
  a reusable pair that runs a fixed workload against the Docker Compose
  stack and pulls request throughput, latency p50/p95/p99, error rate,
  token throughput, process CPU, process memory, and active backend
  straight from Prometheus for the exact `[start, end]` window of that
  run - the same pair every later experiment reuses unchanged.
- `monitoring/prometheus.yml`'s scrape interval lowered from 15s to 2s, so
  a workload lasting tens of seconds gets many real scrape samples instead
  of one or two.
- Fixed a real accuracy bug this validation caught: the latency
  histogram's original bucket boundaries made `histogram_quantile`
  overshoot p95/p99 by 40-60% for the mock backend's actual latency
  range - see `experiments/phase5_healthy_baseline.md` for the full
  writeup.
- **Complete**: the real healthy mock baseline has been run against the
  Docker Compose stack (1000 requests, concurrency 5, ~24 req/s
  throughput, p99 ~298ms, 0% errors) - this is the canonical reference
  every later failure-injection experiment compares against. Full numbers
  in `experiments/phase5_healthy_baseline.md`.
- Not included yet: failure injection, Kubernetes, vLLM, GPU Docker.

## Phase 6 scope: controlled latency fault injection

- One deliberate, reversible fault: a fixed extra delay added on top of
  the mock backend's existing random latency, controlled entirely by
  `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS` (default `0` - rejected if
  negative). At the default, behavior is byte-for-byte identical to
  Phase 5.
- A `WARNING`-level startup log makes an active fault operationally
  obvious - never silent.
- Reuses the exact Phase 5 workload and tooling
  (`scripts/run_experiment.sh` + `scripts/capture_prometheus_metrics.py`)
  unchanged - only the fault differs between the two runs, not how
  either is measured.
- `docker-compose.yml` gets one new `environment:` line using Compose's
  `${VAR:-0}` substitution, so enabling/reverting the fault is a shell
  environment variable at `docker compose up` time, never a file edit -
  see "Failure injection" below for the exact commands (Linux/macOS and
  Windows PowerShell).
- **Complete**: the real 500ms fault has been run against the Docker
  Compose stack (throughput 23.7 → 6.9 req/s, p99 298ms → 987ms, 0%
  errors, CPU usage *dropped* rather than rising - confirming this is a
  pure latency fault, not a CPU-pressure one). Full comparison against
  the Phase 5 baseline, plus a genuine finding about histogram bucket
  resolution at higher latencies, in
  `experiments/phase6_latency_fault.md`.
- Not included yet: any fix for this fault (that's Phase 7), other
  failure types (random failures, memory/CPU pressure), Kubernetes,
  vLLM, GPU Docker.

## Phase 7 scope: histogram bucket resolution fix

- Diagnoses precisely why Phase 6's Prometheus-derived p95/p99 overshot
  the client-observed values by 130-170ms: hand-deriving Prometheus's own
  `histogram_quantile` linear-interpolation formula reproduces the real
  Phase 6 numbers to within a millisecond - the old buckets had only two
  boundaries (`0.75s`, `1.0s`) covering the fault's entire [550, 800]ms
  true range, so 100% of observations fell into two 250ms-wide buckets.
- Fixes it by densifying `_LATENCY_BUCKETS_SECONDS` with ~50-100ms steps
  between 500ms and 1.5s (27 → 39 buckets total); buckets below 500ms and
  above 1.5s are untouched since they were never the problem.
- A permanent regression test (`tests/test_metrics.py`) re-implements
  Prometheus's interpolation algorithm against a deterministic synthetic
  distribution shaped like the Phase 6 fault, and proves the new buckets
  keep p95/p99 error ≤30ms while the old buckets exceed 100ms error for
  the same distribution - so this class of bug can't silently reappear.
- Does **not** edit `experiments/phase5_healthy_baseline.md` or
  `experiments/phase6_latency_fault.md` - those stay as the historical
  record of the real runs that used the old buckets.
- See `experiments/phase7_histogram_fix.md` for the full diagnosis and a
  real (non-Docker, non-official) rehearsal that already shows the fix
  working: p95/p99 gaps against the client-side numbers shrank from
  Phase 6's 132ms/170ms down to ~7ms/~2ms. Official reruns against the
  Docker Compose stack (healthy + the same +500ms fault) are pending.
- Not included yet: any additional failure type, Kubernetes, vLLM, GPU
  Docker, autoscaling.

## Architecture

```
Client                          Prometheus (container)
  │  HTTP                          │  scrapes GET /metrics every 15s
  ▼                                ▼
FastAPI app (main.py) ──────────── observability/metrics.py
  │                                    ▲  (metric objects + record_*/set_* helpers)
  ├─ TimingMiddleware ───────────────┤  records HTTP metrics (path template labels)
  ├─ api/routes.py                   │
  │    GET /health, GET /metrics, POST /generate
  │    depends only on ──▶  backends/base.py (InferenceBackend interface)
  │    /generate also records inference metrics ─┘
  │
  ├─ backends/mock.py        → MockBackend (simulated latency + fake tokens)
  └─ backends/huggingface.py → HuggingFaceBackend (real local model via Transformers)
        │
        └─ backends/device.py → device detection, startup logging, GPU memory stats
                                                      ▲
                                                      │ PromQL queries
                                              Grafana (container) → dashboards
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

`observability/metrics.py` defines every Prometheus metric object as a
module-level singleton and exposes small helper functions
(`record_http_request`, `record_generation`, `set_backend_info`,
`set_model_load_seconds`); `TimingMiddleware`, `api/routes.py`, and
`backends/huggingface.py` call these helpers rather than touching
`prometheus_client` directly, keeping metric definitions in one place.

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
├── middleware/
│   └── timing.py             # per-request latency measurement + HTTP metrics
└── observability/
    └── metrics.py            # Prometheus metric objects + record_*/set_* helpers

tests/                        # pytest suite
scripts/
├── load_test.py              # async load-testing / benchmarking script
├── docker_smoke_test.sh      # build + run + health/generate check for the Docker image
├── run_experiment.sh         # reproducible experiment runner (baseline + future failure runs)
└── capture_prometheus_metrics.py  # pulls the 9 experiment metrics from Prometheus

Dockerfile                    # two-stage build (builder -> runtime)
.dockerignore                 # keeps secrets/tests/dev tooling out of the image
docker-compose.yml            # app + Prometheus + Grafana, together
monitoring/
├── prometheus.yml            # scrape config (2s interval)
└── grafana/
    ├── provisioning/
    │   ├── datasources/prometheus.yml  # auto-provisioned Prometheus datasource
    │   └── dashboards/dashboard.yml    # tells Grafana where to load dashboards from
    └── dashboards/inference-lab.json   # starter dashboard (traffic, latency, tokens, GPU)

experiments/
├── phase3_gpu_benchmark.md            # CPU vs. NVIDIA A40 benchmark
├── phase5_healthy_baseline.md         # reference measurement for failure-injection comparisons
├── phase5_healthy_baseline_metrics.json
├── phase6_latency_fault.md            # controlled extra-latency fault vs. the Phase 5 baseline
├── phase6_latency_fault_metrics.json
└── phase7_histogram_fix.md            # diagnosis + fix for Phase 6's p95/p99 measurement distortion
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
| `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS` | `0` | Fault-injection knob (Phase 6+): fixed extra delay added to every mock request; rejects negative values - see "Failure injection" below |
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
options (prompt text, max tokens, etc). This is the client-side view; for
the authoritative, Prometheus-sourced measurement (including process
CPU/memory and token throughput, which the client can't see at all), use
`scripts/run_experiment.sh` below instead.

## Running a reproducible experiment (baseline / failure comparisons)

`scripts/run_experiment.sh` wraps `load_test.py` with the timing and
Prometheus-capture steps needed for a real, comparable measurement. With
the full stack up (`docker compose up --build`):

```bash
scripts/run_experiment.sh \
  --requests 1000 --concurrency 5 \
  --prompt "Tell me about reliability engineering." --max-tokens 64 \
  --url http://localhost:8000 --prometheus-url http://localhost:9090 \
  --backend mock \
  --out experiments/phase5_healthy_baseline_metrics.json
```

It: sleeps briefly so Prometheus has a fresh at-rest scrape before
starting; runs the load test, recording the real start/end timestamps;
sleeps again so Prometheus scrapes the fully-settled final state; then
calls `scripts/capture_prometheus_metrics.py` to pull request throughput,
latency p50/p95/p99, error rate, token throughput, process CPU, process
memory, and the active backend from Prometheus for that exact window, and
prints (and optionally saves as JSON) the result.

This is the same pair of scripts every later failure-injection experiment
reuses - see `experiments/phase5_healthy_baseline.md` for the full
methodology and the healthy-baseline numbers this workload produces.

**Windows/WSL note:** `scripts/run_experiment.sh` must have LF (Unix) line
endings to run under WSL - a Windows Git checkout with
`core.autocrlf=true` can otherwise convert it to CRLF, which breaks the
`#!/usr/bin/env bash` shebang. This repo's `.gitattributes` forces LF for
`.sh` files (and other text files) on checkout regardless of that local
setting, so a fresh clone is unaffected. If you already have a CRLF copy
from before this fix, run `git add --renormalize .` (or re-clone) to pick
it up.

## Failure injection: controlled latency fault

`INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS` adds a fixed extra delay on top of
the mock backend's normal random latency, entirely via config - no code
changes needed to enable or disable it. Default `0` (rejected if
negative) means healthy behavior is unaffected unless you explicitly
override it. `docker-compose.yml` reads it as
`${INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS:-0}`, so the override is always a
shell environment variable at `docker compose up` time, never a file
edit.

**Enable the fault (Linux/macOS):**

```bash
INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500 docker compose up -d --build inference-lab
```

**Enable the fault (Windows PowerShell):**

```powershell
$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS="500"
docker compose up -d --build inference-lab
```

**Revert to healthy (Linux/macOS):**

```bash
unset INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS
docker compose up -d --build inference-lab
```

**Revert to healthy (Windows PowerShell):**

```powershell
$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=$null
docker compose up -d --build inference-lab
```

Assigning `$null` to a PowerShell environment variable removes it from
the process environment, so Compose's `:-0` default takes over on the
next `up` - the same "nothing to undo" property as the Linux `unset`.
`scripts/run_experiment.sh` itself still needs a Bash-capable shell (WSL,
Git Bash, etc.) even on Windows - only the container's environment
variable is PowerShell-native here, not the experiment runner.

When the fault is active, `docker compose logs inference-lab` shows a
`WARNING`-level line (`Mock backend fault injection active: +500ms extra
latency on every request`) at startup - if you don't see it after
enabling, the container didn't pick up the new value (try
`--force-recreate`).

See `experiments/phase6_latency_fault.md` for the full experiment,
its expected diagnostic signature, and the real measured results.

## Running with Docker

Build the image:

```bash
docker build -t inference-lab .
```

The build installs PyTorch explicitly from
[PyTorch's CPU-only wheel index](https://download.pytorch.org/whl/cpu)
before installing the rest of the dependencies. This matters: the default
PyPI `torch` wheel for Linux pulls in several **gigabytes** of NVIDIA CUDA
packages (`nvidia-cudnn-*`, `nvidia-cufft-*`, `nvidia-nccl-*`, a whole
`cuda-toolkit` meta-package, etc.) as ordinary dependencies, even though
this CPU image has no GPU to use them with - that was making the build
huge and extremely slow. The explicit CPU-only install avoids all of it.

Run it in **mock mode** (default, no model download):

```bash
docker run --rm -p 8000:8000 -e INFERENCE_LAB_BACKEND=mock inference-lab
```

Run it with the **Hugging Face CPU backend**, persisting the downloaded
model across container restarts with a named volume:

```bash
docker run --rm -p 8000:8000 \
  -e INFERENCE_LAB_BACKEND=huggingface \
  -e INFERENCE_LAB_HUGGINGFACE_MODEL_NAME=sshleifer/tiny-gpt2 \
  -v hf-cache:/home/appuser/.cache/huggingface \
  inference-lab
```

Any `INFERENCE_LAB_*` variable (see the Configuration table above) can be
passed with `-e`, exactly as when running without Docker - the image bakes
in no backend choice, model, or device. Note that this CPU image's torch
build has no CUDA support at all (see above), so `INFERENCE_LAB_HUGGINGFACE_DEVICE=auto`
will always resolve to `cpu` here regardless of `--gpus all` - a future
CUDA image (see below) is what will make `auto` resolve to `cuda`.

Either way:

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me about reliability engineering.", "max_tokens": 32}'
```

The container also runs a `HEALTHCHECK` against `/health` every 30 seconds;
`docker ps` shows the container's health status, and `docker inspect
--format='{{.State.Health.Status}}' <container>` reports it directly.

### Docker smoke test

`scripts/docker_smoke_test.sh` builds the image, starts it, waits for the
health check to report healthy, then calls `/health` and `/generate` and
fails loudly if either doesn't respond correctly:

```bash
scripts/docker_smoke_test.sh              # mock backend (fast, no network needed)
scripts/docker_smoke_test.sh huggingface  # Hugging Face CPU backend (downloads a model)
```

### Verified locally

Phase 4A's Docker setup (including the CPU-only PyTorch fix above) has been
built and run end-to-end outside this repo's development sandbox, with all
of the following confirmed:

- The CPU-only image builds successfully, with no large NVIDIA/CUDA
  dependency downloads.
- The container starts successfully.
- The Docker `HEALTHCHECK` reaches `GET /health` and gets `200 OK`.
- `POST /generate` works from outside the container.
- The mock backend returns the expected response, including the
  `tokens_per_second` and `gpu_memory_*` performance fields.
- `scripts/docker_smoke_test.sh` passes.

### What's kept out of the image

- **Model weights and the Hugging Face cache** - downloaded at runtime into
  `$HF_HOME` (`/home/appuser/.cache/huggingface`) inside the container, not
  baked into any layer. Mount that path as a volume (as above) to avoid
  re-downloading on every container restart.
- **Secrets and local env files** - `.dockerignore` excludes `.env`/`.env.*`
  and everything not needed to run the service (tests, dev scripts, the
  benchmark report, this README's own source file is copied in only
  because `pyproject.toml` needs it to read package metadata at build
  time).
- **Dev/test tooling** - `pytest`, `ruff`, and the `dev` extra are never
  installed in the image; only production + the `huggingface` extra are.

### CPU vs. future GPU Docker image

This image and its `Dockerfile` are deliberately structured so a CUDA
variant is a small, isolated change later, not a rewrite:

| | CPU image (this phase) | GPU image (later) |
|---|---|---|
| Base image | `python:3.11-slim` | An NVIDIA CUDA base image (e.g. `nvidia/cuda:12.x-runtime-ubuntu22.04` with Python added) |
| `torch` build | CPU-only wheel, installed explicitly from `download.pytorch.org/whl/cpu` - no NVIDIA/CUDA packages at all | CUDA-enabled wheel installed from a CUDA-specific index, matching the base image's CUDA version |
| Run command | `docker run ...` | `docker run --gpus all ...`, requiring the NVIDIA Container Toolkit on the host |
| App code, API, health check, `INFERENCE_LAB_HUGGINGFACE_DEVICE=auto` device detection | Unchanged | Unchanged - Phase 3A already made the backend device-agnostic |

## Observability (Prometheus + Grafana)

Run the whole stack - the app, Prometheus, and Grafana together:

```bash
docker compose up --build
```

Then:

- **App**: http://localhost:8000 (`/health`, `/generate`, `/metrics` - unchanged from earlier phases)
- **Prometheus**: http://localhost:9090 (try the "Status → Targets" page - `inference-lab` should show as `UP`)
- **Grafana**: http://localhost:3000 (login `admin` / `admin`) - the Prometheus datasource and the
  "AI Inference Reliability Lab" dashboard are pre-provisioned; no manual setup needed.

### How Prometheus and Grafana fit together

Prometheus **pulls**: every 15 seconds it sends an HTTP GET to the app's
`/metrics` and stores whatever numbers it finds as a time series. Grafana
never talks to the app directly - it only queries Prometheus's stored
history (via PromQL) and draws graphs from it. So: the app just reports its
current numbers when asked; Prometheus is responsible for polling and
remembering; Grafana is responsible for querying and drawing.

### Metrics exposed at `/metrics`

**HTTP layer** (every route, recorded by `TimingMiddleware`):

| Metric | Type | Labels |
|---|---|---|
| `http_requests_total` | Counter | `method`, `path`, `status` |
| `http_request_duration_seconds` | Histogram | `method`, `path` |

**Inference layer** (recorded in `/generate`, identical for both backends):

| Metric | Type | Labels |
|---|---|---|
| `inference_requests_total` | Counter | `backend`, `status` (`success`/`error`) |
| `inference_generation_latency_seconds` | Histogram | `backend` |
| `inference_prompt_tokens_total` / `inference_completion_tokens_total` | Counter | `backend` |
| `inference_backend_info` | Gauge (always `1`) | `backend`, `device`, `model` |
| `inference_model_load_seconds` | Gauge | `backend`, `model` (Hugging Face only) |
| `inference_gpu_memory_allocated_bytes` / `_reserved_bytes` / `_peak_bytes` | Gauge | `backend` (only set on a CUDA device - absent otherwise, same "null becomes absent" pattern as the JSON API) |

Plus `prometheus_client`'s default collectors (process CPU/memory, Python
GC stats) for free.

### HTTP path labels

`path` on the HTTP-layer metrics is the route's **path template**
(`request.scope["route"].path`, e.g. `/generate`), not the raw request
URL. This is deliberate: a raw path label would let anyone create an
unbounded number of time series just by hitting made-up URLs (`/x`, `/y`,
`/z`, ...); the template label stays fixed to the handful of routes this
app actually defines. Requests that don't match any route are labeled
`unmatched` instead of leaking the arbitrary path that was requested.

### Excluding Prometheus's own scrape from traffic panels

Prometheus itself calls `GET /metrics` every 15 seconds, which is a real
HTTP request and does get recorded in `http_requests_total{path="/metrics"}`
- that's useful for noticing if scraping itself is slow or failing. But it
is not user/API traffic, so the dashboard's "Request rate" and "Error rate"
panels explicitly filter it out with `path!="/metrics"` in their PromQL, e.g.:

```promql
sum(rate(http_requests_total{path!="/metrics"}[5m])) by (path, status)
```

so Prometheus's own polling never inflates the apparent request rate shown
to a viewer.

### Example PromQL queries

```promql
# Generation latency p95, by backend
histogram_quantile(0.95, sum(rate(inference_generation_latency_seconds_bucket[5m])) by (le, backend))

# Live tokens/sec, by backend
sum(rate(inference_completion_tokens_total[5m])) by (backend)

# Error rate, excluding Prometheus's own scrape
sum(rate(http_requests_total{path!="/metrics", status=~"5.."}[5m]))
```

### Verified locally

The full observability setup - app, Prometheus, and Grafana together via
`docker compose up --build` - has been confirmed working outside this
repo's development sandbox:

- `GET /metrics` works and returns valid Prometheus exposition-format
  output.
- Prometheus successfully scrapes the inference service.
- Grafana runs and the pre-provisioned dashboard loads with no manual
  setup.
- `/generate` traffic appears in the request-rate panel.
- Generation latency series are present.
- Token-throughput series are present.
- The "Active backend" panel correctly shows `mock`.
- The error-rate panel correctly shows no data, since no errors were
  generated.
- The GPU-memory panel correctly shows no data, as expected in mock/CPU
  mode (see Phase 3A/4A - these gauges are only populated on a CUDA
  device).

This was additionally checked in this repo's own development sandbox,
where Docker Hub is blocked but a few individual pieces could still be
verified directly:

- `POST /generate` returns exactly the same response shape as before
  Phase 4B - unchanged behavior, confirmed live.
- The real Prometheus binary (v2.55.1, fetched directly from GitHub
  releases since Docker Hub is blocked here) was run locally against this
  app, and its `/api/v1/targets` API reported the target as
  **`"health": "up"`**.
- Every PromQL query used by the dashboard's panels was executed against
  that real Prometheus instance via its `/api/v1/query` API and returned
  `"status": "success"`.
- `monitoring/prometheus.yml` was validated with `promtool check config`.
- `docker-compose.yml` was validated with `docker compose config`.
- The dashboard JSON was validated for well-formed JSON and correct
  panel/target structure.

## Linting

```bash
ruff check .
```

## Roadmap

- **Phase 3B and beyond**: Larger models, batching/concurrency benchmarks,
  and repeated runs to build on the single-run Phase 3 result above.
- **Phase 4C**: NVIDIA/CUDA Docker image variant (see "CPU vs. future GPU
  Docker image" above), running the containerized service on a cloud GPU -
  with Prometheus/Grafana already in place to watch it.
- **Phase 6**: Controlled latency fault injection - complete. Real
  numbers measured and compared against the Phase 5 baseline in
  `experiments/phase6_latency_fault.md`.
- **Phase 7**: Histogram bucket resolution fix - diagnosis, fix, and
  regression test complete; official Docker Compose reruns (healthy +
  the same +500ms fault, to confirm no regression and prove the fix)
  pending. See `experiments/phase7_histogram_fix.md`.
- **Phase 8+**: Further deliberately induced failure scenarios (OOM,
  queueing/backpressure issues, autoscaling gaps), each measured with
  `scripts/run_experiment.sh` and compared directly against the Phase 5
  healthy baseline (re-measured under Phase 7's corrected buckets) - both
  as Markdown reports in `experiments/` and live in the Grafana dashboard
  built in Phase 4B.
- **Later**: Add a vLLM backend, Kubernetes deployment, GPU scheduling.
