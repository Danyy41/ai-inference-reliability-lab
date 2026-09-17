# Phase 8: Overload / Backpressure Experiment

Determines how the inference service behaves when request concurrency
exceeds its healthy operating range, in two controlled parts against the
exact same fixed-duration concurrency sweep:

- **Phase 8A**: natural concurrency sweep against the unmodified service
  (no server-side limit) - does the async mock backend saturate on its own?
- **Phase 8B**: the same sweep with a deliberate, reversible server-side
  generation concurrency limit (`INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10`)
  - does a real execution-slot ceiling produce genuine queueing?

## Status

**PENDING official Docker Compose runs.** Implementation is complete: the
concurrency limit, both new gauges, fixed-duration load generation, and the
expanded Prometheus capture are all in place and covered by
`tests/test_concurrency.py` / `tests/test_metrics.py` (36 passed, 2 skipped
- no local GPU; Ruff clean). A non-Docker rehearsal in this sandbox (see
"Rehearsal" below) confirms the whole pipeline produces the expected
signature end-to-end. The tables in "Official results" below are
placeholders - **nothing in this report is a real measured number yet**;
they will be filled in once the real Docker Compose sweeps are run.

## Architecture: why this needed two parts, not one

`MockBackend.generate()` is `await asyncio.sleep(latency_ms / 1000)` -
fully non-blocking, zero CPU consumption. Combined with a single
uvicorn/asyncio worker process, there is no natural bottleneck for pure
request concurrency to hit in the 5-100 range: the event loop can trivially
interleave that many pending sleeps. A concurrency sweep against the
*unmodified* service is expected to show throughput scaling with
concurrency and flat latency - not because nothing was tested, but because
this backend has no compute/GPU contention to saturate, unlike a real model
server. Phase 8A measures and documents this directly rather than assuming
it. Phase 8B then adds the one thing a real inference server actually has -
a bounded number of concurrent execution slots - implemented as an
`asyncio.Semaphore`, not as added latency, so any queueing it produces is a
genuine consequence of exceeding a capacity limit.

## The concurrency limit (Phase 8B)

- **What**: `asyncio.Semaphore(N)` gating only the call to
  `backend.generate()` - never `/health` or `/metrics`, and never request
  parsing/response serialization on `/generate` itself.
- **Control**: `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` (default `0` -
  rejected if negative). At `0`, no semaphore is created at all and
  `/generate` behaves exactly as before Phase 8 - this is what Phase 8A
  runs against.
- **Value for Phase 8B**: `10`.
- **Visibility**: a `WARNING`-level log line (`Server-side generation
  concurrency limit active: max 10 concurrent generations`) is emitted
  once at startup whenever the value is nonzero.
- **Where**: `src/inference_lab/main.py` builds
  `app.state.generation_semaphore`; `src/inference_lab/api/routes.py`
  gates only the `backend.generate()` call with it (or
  `contextlib.nullcontext()` when unlimited), and starts the
  generation-latency timer *after* the gate is entered - see "Metrics" below
  for why that placement matters.

## Metrics

Two new label-free gauges (`src/inference_lab/observability/metrics.py`),
incremented/decremented with `try/finally` so they can never drift:

- **`inference_requests_in_flight`**: incremented the instant a `/generate`
  request is accepted, *before* it waits for a generation slot; decremented
  once the whole request is done. Represents queued + executing requests.
- **`inference_generations_active`**: incremented only after a request has
  acquired a generation slot (or immediately, in unlimited mode);
  decremented as soon as `backend.generate()` returns. Represents only
  requests actually executing.

No dedicated queue-wait histogram was added. Two existing histograms
already decompose into "wait + serve" vs. "serve only" once the generation
timer moved inside the gate:

- `http_request_duration_seconds{path="/generate"}` (unchanged
  `TimingMiddleware`, wraps the whole request) = queue wait + generation +
  overhead.
- `inference_generation_latency_seconds{backend="mock"}` (timer now starts
  after the semaphore is acquired) = generation only, post-queue.

The gap between their percentiles for the same window is the (approximate)
queueing delay - `HTTP p95 - generation p95`, etc. This is a **diagnostic
proxy, not an exact decomposition**: each side is already an independent
`histogram_quantile` interpolation (the same class of approximation
discussed in `experiments/phase7_histogram_fix.md`), so subtracting the two
doesn't yield the true wait-time distribution's exact quantile - it's
sufficient to detect and characterize the presence and rough scale of
queueing, which is all this phase needs. Likewise, `in_flight - active` at
any instant is an approximate queue depth, not an exact point-in-time
maximum, since `max_in_flight_requests` and `max_active_generations` are
independently sampled gauge maxima over the window and may not have peaked
at the same instant.

**PromQL**:
```promql
max_over_time(inference_requests_in_flight[<range_seconds>s])
max_over_time(inference_generations_active[<range_seconds>s])

histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{path="/generate"}[<range_seconds>s])) by (le))
  -
histogram_quantile(0.95, sum(rate(inference_generation_latency_seconds_bucket{backend="mock"}[<range_seconds>s])) by (le))
```

## Workload: fixed-duration sweep

A **fixed request count is unsuitable here**: at concurrency 50-100, the
unbounded async mock backend could finish 1000 requests in a few seconds -
too short for Prometheus's 2s scrape interval to sample peak in-flight/CPU
behavior. `scripts/load_test.py` gained a second mode
(`--duration-seconds`, mutually exclusive with `--requests`) that runs
exactly `--concurrency` looping workers for a fixed wall-clock duration
instead of a fixed count; `scripts/run_experiment.sh` passes through
whichever mode is given and fails clearly if both are supplied. Every
historical Phase 5-7 command is unaffected (they always pass `--requests`
explicitly; the 1000-request default is preserved when neither flag is
given).

| Parameter | Value |
|---|---|
| Backend | `mock` (`INFERENCE_LAB_BACKEND=mock`) |
| Duration per level | 30 seconds |
| Concurrency levels | 5, 10, 25, 50, 100 |
| Prompt (fixed) | `"Tell me about reliability engineering."` |
| `max_tokens` (fixed) | 64 |
| Phase 8A | `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` unset (0, unlimited) |
| Phase 8B | `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10` |

`httpx.AsyncClient` in `load_test.py` now sets
`Limits(max_connections=concurrency + 20, max_keepalive_connections=concurrency)`
for both load-test modes, so the client's own default 100-connection pool
cap can never become the bottleneck at concurrency=100.

## How to run it

**Linux/macOS - Phase 8A (unlimited):**

```bash
docker compose up -d --build inference-lab

for c in 5 10 25 50 100; do
  scripts/run_experiment.sh \
    --duration-seconds 30 --concurrency "$c" \
    --prompt "Tell me about reliability engineering." --max-tokens 64 \
    --url http://localhost:8000 --prometheus-url http://localhost:9090 \
    --backend mock \
    --out "experiments/phase8a_concurrency_${c}_metrics.json"
done
```

**Linux/macOS - Phase 8B (limit=10):**

```bash
INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10 docker compose up -d --build inference-lab

for c in 5 10 25 50 100; do
  scripts/run_experiment.sh \
    --duration-seconds 30 --concurrency "$c" \
    --prompt "Tell me about reliability engineering." --max-tokens 64 \
    --url http://localhost:8000 --prometheus-url http://localhost:9090 \
    --backend mock \
    --out "experiments/phase8b_concurrency_${c}_metrics.json"
done

# Revert to healthy afterward
unset INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS
docker compose up -d --build inference-lab
```

**Windows PowerShell** (same pattern as Phase 6/7):

```powershell
$env:INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS="10"
docker compose up -d --build inference-lab
# ... run the sweep via a Bash-capable shell (WSL, Git Bash) as before ...
$env:INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=$null
docker compose up -d --build inference-lab
```

## Success criteria

**Phase 8A**: as concurrency rises, throughput should scale roughly
linearly with it and generation/HTTP latency should stay roughly flat
(both close to the Phase 5 baseline's ~175-300ms range) - `max_in_flight`
should equal `max_active_generations` at every level (no queueing possible
without a limit). If this *doesn't* hold, that's still a valid, reportable
finding, not a failed experiment - it would mean something else (OS/uvicorn
connection limits, GC pauses, etc.) became a bottleneck well before
expected.

**Phase 8B**: once requested concurrency exceeds 10, throughput should
approach a ceiling (~10 / average generation time), the HTTP/generation
latency gap should grow (queueing delay), `max_active_generations` should
plateau at 10 while `max_in_flight` continues rising with concurrency, and
the error rate should stay near zero (this is intentional backpressure, not
a crash) unless concurrency is pushed so high that the httpx client's
30-second timeout is threatened.

**Both**: full test suite / Ruff / `promtool check config` / `docker
compose config` stay green (see "Validation" below); no invented numbers -
every result below in "Official results" is either a real Docker Compose
run or is left as PENDING.

## Official results

**PENDING** - to be filled in with the real Docker Compose sweep, both
sub-phases, 5 concurrency levels each (10 runs total, 10 JSON artifacts:
`phase8a_concurrency_{5,10,25,50,100}_metrics.json`,
`phase8b_concurrency_{5,10,25,50,100}_metrics.json`).

### Phase 8A: natural concurrency sweep

| Concurrency | Throughput (req/s) | Gen p50/p95/p99 (ms) | HTTP p50/p95/p99 (ms) | Max in-flight | Max active |
|---|---|---|---|---|---|
| 5 | PENDING | PENDING | PENDING | PENDING | PENDING |
| 10 | PENDING | PENDING | PENDING | PENDING | PENDING |
| 25 | PENDING | PENDING | PENDING | PENDING | PENDING |
| 50 | PENDING | PENDING | PENDING | PENDING | PENDING |
| 100 | PENDING | PENDING | PENDING | PENDING | PENDING |

### Phase 8B: controlled backpressure (limit=10)

| Concurrency | Throughput (req/s) | Gen p50/p95/p99 (ms) | HTTP p50/p95/p99 (ms) | Max in-flight | Max active | Queue-wait proxy (HTTP p95 - gen p95) |
|---|---|---|---|---|---|---|
| 5 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 10 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 25 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 50 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |
| 100 | PENDING | PENDING | PENDING | PENDING | PENDING | PENDING |

### Did the contrast hold?

PENDING - to be answered once the official results above are in:
concurrency rising → throughput scaling with flat latency (8A) vs.
throughput approaching a ceiling with rising HTTP latency, flat generation
latency, and active-generations plateauing at 10 while in-flight keeps
climbing (8B).

### Reverted to healthy

PENDING - `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` must be unset and the
stack recreated after the Phase 8B sweep, the same revert procedure as
every prior phase's fault/limit knob.

## Rehearsal (non-Docker substitute, this development sandbox)

Before asking for the official reruns, the whole pipeline was rehearsed
end-to-end against a directly-run `uvicorn` process and a real Prometheus
v2.55.1 binary (same substitute pattern used in every prior Docker-dependent
phase, since Docker Hub is blocked in this sandbox). **This is not the
official result and is not merged into the tables above** - it's a
real (not synthetic), reduced-scale (10s per level, not 30s) confirmation
that the design produces the expected signature, at a single concurrency
level (25) for each sub-phase:

| | Throughput (req/s) | Gen p50/p95/p99 (ms) | HTTP p50/p95/p99 (ms) | Max in-flight | Max active |
|---|---|---|---|---|---|
| **8A-style** (unlimited, concurrency 25) | 131.4 | 178.8 / 287.8 / 297.7 | 179.8 / 289.2 / 299.0 | 25 | 25 |
| **8B-style** (limit=10, concurrency 25) | 55.0 | 175.0 / 289.0 / 298.5 | 433.2 / 569.6 / 596.7 | 25 | 10 |

This is exactly the predicted contrast: unlimited mode shows
`max_in_flight == max_active` (no queueing possible) and HTTP latency
tracking generation latency closely; limited mode shows `max_active`
pinned at the limit (10) while `max_in_flight` reaches the full requested
concurrency (25), generation latency staying flat and close to the
healthy baseline (~175-300ms) while HTTP latency roughly triples (queueing
delay of ~280ms at p95), and throughput dropping from 131.4 to 55.0 req/s -
close to the ~57 req/s theoretical ceiling (10 slots / ~0.175s average
generation time). Zero errors in either run.

## Validation

- `pytest`: `tests/test_concurrency.py` (new - default-unlimited behavior
  unchanged, the semaphore bounds `inference_generations_active` while
  `inference_requests_in_flight` rises higher, startup warning logged only
  when the limit is active, negative config rejected) and
  `tests/test_metrics.py` (new - both gauges appear in `/metrics` and
  settle back to 0 after a request) - full suite 36 passed, 2 skipped (no
  local GPU). Ruff clean.
- Real (non-synthetic) rehearsal against a directly-run app + real
  Prometheus in the development sandbox, described above - ran before
  asking for the official reruns and matches the predicted contrast exactly.
- `monitoring/prometheus.yml` re-validated with `promtool check config`
  (unchanged by this phase).
- `docker-compose.yml` re-validated with `docker compose config`, including
  both the new `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` default (`"0"`)
  and an override (`"10"`) resolving correctly.
- Official Docker Compose sweeps (both sub-phases, all 5 concurrency
  levels): **PENDING**.

## Compatibility with prior phase reports

`phase5_healthy_baseline.md`, `phase6_latency_fault.md`, and
`phase7_histogram_fix.md` are unchanged by this phase. Phase 8A's
concurrency-5 level is the closest direct comparison point to Phase 5's
baseline (same concurrency, same workload) and should reproduce it closely
once real numbers are in, the same way Phase 7's healthy rerun reproduced
Phase 5.
