# AI Inference Reliability Lab

A production-like LLM inference service, built to intentionally break in
controlled ways, diagnosed with real metrics, fixed, and re-measured -
every claim in this repo is backed by a real, reproducible experiment, not
a simulation.

## The story (60-second version)

I built a small, realistic LLM inference service - FastAPI, a pluggable
backend interface (a mock backend for fast/deterministic testing, and a
real Hugging Face Transformers backend for actual CPU/GPU generation),
containerized with Docker, observed with Prometheus + Grafana. Then I used
it as a lab: I established a rigorous, repeatable measurement methodology
(fixed workload, pre/post settle buffers around every run, exact-window
Prometheus queries) and a healthy baseline *before* touching anything else.

From there, I deliberately broke things in controlled, reversible ways and
measured the blast radius with that same methodology every time:

- **Injected a fixed +500ms latency fault** - and the measurement itself
  turned out to have a real bug: Prometheus's `histogram_quantile` was
  overshooting p95/p99 by up to 170ms because the latency buckets were too
  coarse for the fault's actual range. I diagnosed it by hand-deriving the
  interpolation math (matched the real numbers to <1ms), fixed the bucket
  resolution, wrote a permanent regression test, and proved the fix with a
  ~7-9x reduction in measurement error against real Docker Compose reruns.
- **Pushed concurrency to 100** and compared unbounded concurrency against
  a deliberate admission-control limit (an `asyncio.Semaphore` capping
  concurrent generations at 10). The result was the most interesting
  finding of the project: unbounded concurrency's own tail latency
  (p99 ≈ 5.3s) was *worse* than the throttled version's (p99 ≈ 2.0s) -
  even though the backend does zero CPU work per request - because
  per-request overhead compounds into real scheduling contention at high
  concurrency. Admission control traded away half the throughput for a
  dramatically more predictable, bounded tail.

Nothing in this repo is invented: every number below links to a Markdown
report and a raw JSON artifact from a real run.

## Key results at a glance

| Phase | What was tested | Headline result |
|---|---|---|
| [3](experiments/phase3_gpu_benchmark.md) | CPU vs. NVIDIA A40 GPU inference (single run, `gpt2`, 100 tokens) | A40: **~13.1x** higher throughput, **~92.4%** lower latency |
| [5](experiments/phase5_healthy_baseline.md) | Healthy baseline (mock backend, 1000 req, concurrency 5) | ~24.9 req/s; p50 173ms / p95 286ms / p99 297ms; 0% errors - the reference every later phase compares against |
| [6](experiments/phase6_latency_fault.md) | +500ms fixed latency fault vs. Phase 5 | Throughput 23.7→6.9 req/s; p99 298→987ms; 0% errors; CPU *dropped* (confirms a pure latency fault, not CPU pressure) |
| [7](experiments/phase7_histogram_fix.md) | Fixed the measurement bug Phase 6 exposed | Prometheus/client p95/p99 gap 132ms/170ms → **17.4ms/19.0ms** (~7-9x smaller); healthy rerun reproduced Phase 5 exactly - confirms no regression |
| [8](experiments/phase8_concurrency_overload.md) | Concurrency 100: unlimited vs. semaphore-limited to 10 | Unlimited: 113 req/s but p99 **5274ms** (contention-driven tail). Limited: 55 req/s, p99 **1998ms** (bounded, predictable) - proven directly via Prometheus gauges: 100 in-flight, only 10 executing |

## Architecture

```
Client                          Prometheus (container)
  │  HTTP                          │  scrapes GET /metrics every 2s
  ▼                                ▼
FastAPI app (main.py) ──────────── observability/metrics.py
  │                                    ▲  (metric objects + record_*/set_* helpers)
  ├─ TimingMiddleware ───────────────┤  records HTTP metrics (path template labels)
  ├─ api/routes.py                   │
  │    GET /health, GET /metrics, POST /generate
  │    depends only on ──▶  backends/base.py (InferenceBackend interface)
  │    /generate also records inference metrics, gated by an optional
  │    asyncio.Semaphore (concurrency limit) ─┘
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
`INFERENCE_LAB_BACKEND=huggingface`). A future real backend (e.g. vLLM)
can be added as `backends/vllm_backend.py` implementing the same
interface and switched on with one config value - no changes to routes,
schemas, or middleware. `torch`/`transformers` are only imported when the
`huggingface` backend is actually selected, so a mock-only install never
needs them.

`observability/metrics.py` defines every Prometheus metric object as a
module-level singleton and exposes small helper functions
(`record_http_request`, `record_generation`, `set_backend_info`,
`set_model_load_seconds`); `TimingMiddleware`, `api/routes.py`, and
`backends/huggingface.py` call these helpers rather than touching
`prometheus_client` directly, keeping metric definitions in one place.

The generation-concurrency limit (Phase 8) is an `asyncio.Semaphore`
gating only the `backend.generate()` call inside `/generate` - never
`/health`/`/metrics`, and never request parsing/response serialization -
so any queueing it produces is a genuine capacity effect, not simulated
latency.

## Quickstart

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # mock mode only - no torch/transformers needed
# or: pip install -e ".[dev,huggingface]"   # to also enable the real model backend

uvicorn inference_lab.main:app --reload --port 8000
```

```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Tell me about reliability engineering.", "max_tokens": 32}'
```

The response includes `text`, `prompt_tokens`, `completion_tokens`,
`finish_reason`, `latency_ms`, `tokens_per_second`, and (real model
backend + CUDA only) `gpu_memory_*_mb` fields - the same shape regardless
of which backend is active.

**Real model mode** (`sshleifer/tiny-gpt2` by default - a real, tiny
GPT-2 architecture used across the HF ecosystem specifically to prove a
pipeline works end-to-end without a multi-GB download; its text isn't
coherent, that's expected):

```bash
INFERENCE_LAB_BACKEND=huggingface uvicorn inference_lab.main:app --reload --port 8000
```

**Full stack with Docker Compose** (app + Prometheus + Grafana):

```bash
docker compose up --build
```

- App: http://localhost:8000
- Prometheus: http://localhost:9090 ("Status → Targets" should show `inference-lab` as `UP`)
- Grafana: http://localhost:3000 (`admin`/`admin`) - dashboard and datasource are pre-provisioned

See "Reproducing the experiments" below to run the same workload used in
every phase report above, and "Grafana dashboard" for what the
pre-provisioned dashboard shows.

## Grafana dashboard

The pre-provisioned **"AI Inference Reliability Lab"** dashboard
(`monitoring/grafana/dashboards/inference-lab.json`) has six panels:

1. Request rate (excludes Prometheus's own `/metrics` scrapes)
2. Error rate (same exclusion)
3. Generation latency (p50 / p95 / p99)
4. Token throughput
5. GPU memory
6. Active backend

**Screenshots**: not embedded in this repo yet - Grafana only shows data
while the stack is actually running and receiving traffic, and this
project's real experiment data (Phases 5-8) was captured on the repo
owner's own machine, not somewhere a screenshot could be taken from
inside this development environment. To add them:

1. `docker compose up --build`
2. Generate some real traffic, e.g. `scripts/run_experiment.sh --requests 1000 --concurrency 5 ...` (see below) or just a few `curl`/`load_test.py` calls.
3. Open http://localhost:3000 (`admin`/`admin`), open the dashboard, and screenshot each panel (or the whole dashboard).
4. Save the images under `docs/screenshots/` and embed them here, e.g.:
   ```markdown
   ![Request rate and latency](docs/screenshots/dashboard-overview.png)
   ```

## Reproducing the experiments

Every phase in "Key results" above was measured the same way, so
reproducing any of them is the same recipe with different parameters.

**1. Client-side load test** (quick, no Prometheus needed):

```bash
python scripts/load_test.py --url http://localhost:8000 --requests 200 --concurrency 20
```

Reports total requests, error rate, wall time, throughput, and latency
min/mean/p50/p95/p99/max. This is the secondary, client-observed
cross-check used throughout every phase report - not the authoritative
measurement, since it can't see server-side metrics (process CPU/memory,
token throughput) at all.

**2. Reproducible experiment (the authoritative, Prometheus-sourced
measurement)** - with the full stack up (`docker compose up --build`):

```bash
scripts/run_experiment.sh \
  --requests 1000 --concurrency 5 \
  --prompt "Tell me about reliability engineering." --max-tokens 64 \
  --url http://localhost:8000 --prometheus-url http://localhost:9090 \
  --backend mock \
  --out experiments/my_run_metrics.json
```

This is the exact command (parameters aside) behind every phase report:
it sleeps briefly so Prometheus has a fresh at-rest scrape before
starting; runs the load test, recording the real start/end timestamps;
sleeps again so Prometheus scrapes the fully-settled final state; then
pulls request throughput, latency p50/p95/p99, error rate, token
throughput, process CPU/memory, active backend, end-to-end HTTP
`/generate` latency, and (Phase 8+) max in-flight/active-generation
counts from Prometheus for that exact window.

Use `--duration-seconds 30` instead of `--requests` for a fixed-duration
run (needed at high concurrency - see Phase 8's report - since a fixed
request count can finish in a few seconds against an unbounded async
backend, too short for Prometheus to sample peak behavior). The two flags
are mutually exclusive; neither given preserves the historical
1000-request default.

**3. The two reversible fault/limit knobs**, both entirely via config -
no code changes, no checked-in file edits, always a shell environment
variable at `docker compose up` time so "healthy" always means "don't
pass an override":

| Knob | Default | What it does | Report |
|---|---|---|---|
| `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS` | `0` | Fixed extra delay added to every mock request | `experiments/phase6_latency_fault.md` |
| `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` | `0` (unlimited) | Max concurrent `backend.generate()` calls via a semaphore | `experiments/phase8_concurrency_overload.md` |

```bash
# Enable a fault/limit (Linux/macOS)
INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500 docker compose up -d --build inference-lab
# or: INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10 docker compose up -d --build inference-lab

# Enable (Windows PowerShell)
$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS="500"
docker compose up -d --build inference-lab

# Revert to healthy (Linux/macOS) - "unset", not "set to a different value"
unset INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS
docker compose up -d --build inference-lab

# Revert to healthy (Windows PowerShell)
$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=$null
docker compose up -d --build inference-lab
```

Both reject negative values (`pydantic.Field(ge=0)`) and log a
`WARNING`-level line at startup whenever active, so an active
fault/limit is never silent - check `docker compose logs inference-lab`
if you don't see the warning after enabling one (try `--force-recreate`).

`scripts/run_experiment.sh` itself needs a Bash-capable shell (WSL, Git
Bash) even on Windows - only the container's environment variable is
PowerShell-native above, not the experiment runner. This repo's
`.gitattributes` forces LF line endings for `.sh` files on checkout
regardless of a Windows `core.autocrlf` setting, so a fresh clone won't
hit the "CRLF breaks the bash shebang" problem; `git add --renormalize .`
recovers an existing affected checkout.

## Metrics & observability reference

Prometheus **pulls**: every 2 seconds it sends an HTTP GET to the app's
`/metrics` and stores whatever it finds as a time series. Grafana never
talks to the app directly - it only queries Prometheus's stored history
(via PromQL) and draws graphs from it.

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
| `inference_gpu_memory_allocated_bytes` / `_reserved_bytes` / `_peak_bytes` | Gauge | `backend` (CUDA only - absent otherwise) |
| `inference_requests_in_flight` (Phase 8+) | Gauge, no labels | queued + executing `/generate` requests |
| `inference_generations_active` (Phase 8+) | Gauge, no labels | requests currently executing `backend.generate()` |

Plus `prometheus_client`'s default collectors (process CPU/memory,
Python GC stats) for free.

`path` on the HTTP-layer metrics is the route's **path template**
(e.g. `/generate`), not the raw request URL - a raw path label would let
anyone create unbounded time series just by hitting made-up URLs;
unmatched requests are labeled `unmatched` instead. Prometheus's own
scrape of `/metrics` is real traffic and does get recorded, but the
dashboard's traffic/error-rate panels filter it out (`path!="/metrics"`)
so polling never inflates the apparent request rate.

**Example PromQL:**

```promql
# Generation latency p95, by backend
histogram_quantile(0.95, sum(rate(inference_generation_latency_seconds_bucket[5m])) by (le, backend))

# Live tokens/sec, by backend
sum(rate(inference_completion_tokens_total[5m])) by (backend)

# Error rate, excluding Prometheus's own scrape
sum(rate(http_requests_total{path!="/metrics", status=~"5.."}[5m]))

# Phase 8: max in-flight / actively-executing requests over a window
max_over_time(inference_requests_in_flight[30s])
max_over_time(inference_generations_active[30s])
```

## Configuration reference

All settings are environment variables prefixed `INFERENCE_LAB_` (see
`core/config.py` for the full list):

| Variable | Default | Meaning |
|---|---|---|
| `INFERENCE_LAB_BACKEND` | `mock` | `mock` or `huggingface` |
| `INFERENCE_LAB_LOG_LEVEL` | `INFO` | Python logging level |
| `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS` | `0` | Fault-injection knob (Phase 6+) - see "Reproducing the experiments" |
| `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` | `0` | Concurrency-limit knob (Phase 8+) - see "Reproducing the experiments" |
| `INFERENCE_LAB_HUGGINGFACE_MODEL_NAME` | `sshleifer/tiny-gpt2` | Any causal-LM model on the Hugging Face Hub |
| `INFERENCE_LAB_HUGGINGFACE_DEVICE` | `auto` | `auto`, `cpu`, or `cuda` |
| `INFERENCE_LAB_HUGGINGFACE_MAX_NEW_TOKENS_CAP` | `256` | Hard ceiling on tokens generated per request |

**CPU vs. CUDA**: with `auto` (the default), `backends/device.py` checks
`torch.cuda.is_available()` and resolves to `cuda` if an NVIDIA GPU is
present, otherwise `cpu` - identical code either way. Setting `cuda`
explicitly raises a clear error immediately if no CUDA GPU is actually
available, rather than silently falling back. This repo was developed on
a CPU-only machine; the CUDA path is unit-tested with `torch.cuda` mocked
(`tests/test_device.py`), and a real benchmark has been run on a cloud
GPU - see Phase 3 in "Key results" above.

## Running with Docker (without Compose)

```bash
docker build -t inference-lab .
docker run --rm -p 8000:8000 -e INFERENCE_LAB_BACKEND=mock inference-lab
```

The build installs PyTorch explicitly from
[PyTorch's CPU-only wheel index](https://download.pytorch.org/whl/cpu)
before the rest of the dependencies - the default PyPI `torch` wheel for
Linux otherwise pulls in several **gigabytes** of NVIDIA CUDA packages
even on a machine with no GPU to use them, which was making the build
huge and slow. Any `INFERENCE_LAB_*` variable can be passed with `-e`,
exactly as without Docker - the image bakes in no backend choice, model,
or device (though this CPU image's `torch` build has no CUDA support at
all, so `auto` always resolves to `cpu` here regardless of `--gpus all`).

For the Hugging Face backend, mount a volume so the model persists across
restarts:

```bash
docker run --rm -p 8000:8000 \
  -e INFERENCE_LAB_BACKEND=huggingface \
  -v hf-cache:/home/appuser/.cache/huggingface \
  inference-lab
```

`scripts/docker_smoke_test.sh [huggingface]` builds, starts, waits for
the `HEALTHCHECK` to report healthy, then calls `/health` and `/generate`
and fails loudly if either doesn't respond correctly.

**What's kept out of the image**: model weights/HF cache (downloaded at
runtime, not baked in - mount a volume to avoid re-downloading);
secrets/`.env` files (`.dockerignore`); dev/test tooling (`pytest`,
`ruff` are never installed in the image).

**CPU vs. future GPU image**: deliberately structured so a CUDA variant
is an isolated later change, not a rewrite - a different base image
(`nvidia/cuda:...`) and a CUDA-enabled `torch` wheel instead of the
CPU-only one, run with `docker run --gpus all`. App code, the API, the
health check, and the `auto` device detection are already
hardware-agnostic (Phase 3A).

## Project layout

```
src/inference_lab/
├── main.py                  # app factory, backend selection, concurrency semaphore, exception handler
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
├── load_test.py              # async load-testing / benchmarking script (fixed-count or fixed-duration)
├── docker_smoke_test.sh      # build + run + health/generate check for the Docker image
├── run_experiment.sh         # reproducible experiment runner
└── capture_prometheus_metrics.py  # pulls the experiment metrics from Prometheus

Dockerfile                    # two-stage build (builder -> runtime)
.dockerignore                 # keeps secrets/tests/dev tooling out of the image
docker-compose.yml            # app + Prometheus + Grafana, together
monitoring/
├── prometheus.yml            # scrape config (2s interval)
└── grafana/
    ├── provisioning/         # auto-provisioned datasource + dashboard loader
    └── dashboards/inference-lab.json   # the dashboard described above

experiments/                  # one Markdown report + JSON artifact(s) per phase
├── phase3_gpu_benchmark.md
├── phase5_healthy_baseline.md (+ _metrics.json)
├── phase6_latency_fault.md (+ _metrics.json)
├── phase7_histogram_fix.md (+ two rerun _metrics.json)
└── phase8_concurrency_overload.md (+ two concurrency-100 _metrics.json)
```

## Testing & linting

```bash
pytest          # full suite; HF backend tests auto-skip if the extra isn't installed
ruff check .
```

`tests/test_device.py` exercises CPU/CUDA branching on any machine via
mocking `torch.cuda.is_available`; a couple of real-GPU-memory checks are
`skipif`'d and only run on an actual CUDA machine. `tests/test_metrics.py`
includes a deterministic (non-flaky) regression test reproducing the
Phase 6/7 histogram bug and proving the fix, using a synthetic
linearly-spaced distribution rather than randomness.

## Development log (phase by phase)

The detailed scope, design decisions, and validation for each phase -
"Key results" above is the summary; this is the full history.

### Version 0.1

FastAPI app with `GET /health` and `POST /generate`; a mock inference
backend; request-latency middleware; structured logging and centralized
error handling; an async load-testing script; a pytest suite.

### Version 0.2

A second, real backend using Hugging Face Transformers, selected via
config alongside the mock backend (both always available). The model
loads once at process startup, not per request. Real generated text, real
token counts, real measured latency. Tests need only a tiny (~3MB) model.

### Phase 3A: NVIDIA GPU awareness

Automatic device selection (`auto` → `cuda` if available, else `cpu` - the
same backend code runs unmodified on a CPU laptop or a cloud GPU). Startup
logging of device/CUDA/GPU name/torch CUDA version. Every `/generate`
response reports `tokens_per_second`; on CUDA, also GPU memory
allocated/reserved/peak (MB) - `null` on CPU or mock. `tests/test_device.py`
exercises this on any machine via mocking.

### Phase 3 results

A real CPU-vs-GPU benchmark on a [RunPod](https://runpod.io) NVIDIA A40,
base `gpt2`, same prompt, same 100-token completion on both devices:

| Metric | CPU | NVIDIA A40 |
|---|---|---|
| `latency_ms` | 15262.6069 | 1167.2952 |
| `tokens_per_second` | 6.55196 | 85.66813 |
| `gpu_memory_allocated_mb` | null | 484.2148 |

**~13.1x higher throughput, ~92.4% lower latency.** Single run, small
(124M-param) model, no batching/concurrency - see
`experiments/phase3_gpu_benchmark.md` for limitations before drawing
broader conclusions. `sshleifer/tiny-gpt2` (used elsewhere in this repo)
is deliberately not used for this benchmark's comparison; a coherent
model is just a config change (`INFERENCE_LAB_HUGGINGFACE_MODEL_NAME`).

### Phase 4A: Docker containerization

A two-stage `Dockerfile` packaging the service unchanged. Both backends
work inside the container, chosen the same way as outside Docker. A
`HEALTHCHECK` against `/health`. Model weights and secrets never baked
in. `scripts/docker_smoke_test.sh` proves the container actually works.
Structured so a CUDA variant is later an isolated change, not a rewrite.
**Verified locally** (outside this repo's dev sandbox): CPU-only image
builds with no large CUDA downloads; container starts; healthcheck
passes; `/generate` works from outside the container with expected
fields; smoke test passes.

### Phase 4B: Prometheus + Grafana observability

`GET /metrics` exposes Prometheus-format metrics alongside `/health` and
`/generate`. HTTP metrics recorded by `TimingMiddleware`, labeled by path
*template*; inference metrics recorded in `/generate`, backend-agnostic.
`docker-compose.yml` runs the app with `prometheus` and `grafana`
containers. Prometheus's own scrape is excluded from traffic panels. A
datasource and starter dashboard are pre-provisioned. **Verified locally**
(outside sandbox): full stack up, `/metrics` valid, Prometheus scrapes
successfully, dashboard loads with no manual setup, all six panels show
expected data (or expected *no* data, for GPU memory under mock/CPU).
Also checked inside this sandbox (Docker Hub blocked here, so validated
against a real Prometheus binary fetched from GitHub releases instead):
target reported `"health": "up"`, every dashboard PromQL query returned
`"status": "success"`, `promtool check config` and `docker compose
config` both passed.

### Phase 5: healthy baseline

Establishes the reference every later failure-injection experiment
compares against. `scripts/run_experiment.sh` +
`scripts/capture_prometheus_metrics.py` introduced here, reused unchanged
by every later phase. Scrape interval lowered from 15s to 2s so short
workloads get real samples. **Fixed a real accuracy bug this validation
caught**: the original histogram buckets made `histogram_quantile`
overshoot p95/p99 by 40-60% for the mock backend's actual latency range.
Complete - see "Key results" above and `experiments/phase5_healthy_baseline.md`.

### Phase 6: controlled latency fault injection

One deliberate, reversible fault (`INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS`,
default `0`, rejected if negative) added on top of the mock backend's
existing random latency. `WARNING`-level startup log when active. Reuses
the Phase 5 workload/tooling unchanged. Complete - see "Key results"
above. This run is also where the Phase 7 measurement bug was first
noticed: the Prometheus-derived p95/p99 shifted more than p50 relative to
the client-side cross-check, a genuine finding investigated rather than
glossed over.

### Phase 7: histogram bucket resolution fix

Diagnosed and fixed the Phase 6 measurement distortion: hand-derived
`histogram_quantile`'s interpolation formula reproduced the real Phase 6
numbers to <1ms, confirming the old buckets (only `0.75s`/`1.0s`
boundaries covering the fault's whole [550,800]ms range) were the cause.
Densified `_LATENCY_BUCKETS_SECONDS` from 27 to 39 buckets (500ms-1.5s
only - buckets outside that range were never the problem). A permanent
regression test reproduces the bug and proves the fix using a
deterministic synthetic distribution. Does not edit the Phase 5/6
reports - those remain the historical record under the old buckets.
Complete - see "Key results" above.

### Phase 8: overload / backpressure experiment

Two controlled parts against the same fixed-duration workload: **8A**
(no server-side limit - does the async mock backend saturate on its
own?) and **8B** (`INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10` - does a
real execution-slot ceiling produce genuine queueing?). The limit is an
`asyncio.Semaphore` gating only `backend.generate()`, not added latency.
Two new label-free gauges (`inference_requests_in_flight`,
`inference_generations_active`) make queue depth directly observable; no
new histogram was needed since the gap between the existing HTTP and
generation-latency histograms (once the generation timer moved to start
only after the slot is acquired) already serves as an approximate
queue-wait signal. `scripts/load_test.py` gained a fixed-duration mode
for this. **Methodology finding**: the original 5-level sweep plan was
confounded by a WSL→Windows→Docker-Desktop transport bottleneck at
concurrency 100, so the decisive comparison was run with the load
generator *inside* the container instead - see the report for why.
Complete - see "Key results" above, and
`experiments/phase8_concurrency_overload.md` for the full tail-latency
analysis. Deliberately did not use a Docker CPU limit: the mock backend's
non-blocking `asyncio.sleep` means CPU limiting wouldn't create a
representative inference-capacity bottleneck.

## Roadmap

- **Phase 3B and beyond**: Larger models, batching/concurrency benchmarks,
  repeated runs to build on the single-run Phase 3 result.
- **Phase 4C**: NVIDIA/CUDA Docker image variant, running the containerized
  service on a cloud GPU with Prometheus/Grafana already in place.
- **Phase 9+**: Further deliberately induced failure scenarios (OOM,
  autoscaling gaps), each measured with `scripts/run_experiment.sh` and
  compared against the Phase 5 baseline (re-measured under Phase 7's
  corrected buckets).
- **Later**: Add a vLLM backend, Kubernetes deployment, GPU scheduling.
