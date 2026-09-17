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

**Complete.** The concurrency limit, both new gauges, fixed-duration load
generation, and the expanded Prometheus capture are implemented and
covered by `tests/test_concurrency.py` / `tests/test_metrics.py` (36
passed, 2 skipped - no local GPU; Ruff clean). The decisive official
comparison - unlimited vs. `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10`,
both at concurrency 100 for 30 seconds - was run against the real Docker
Compose stack on the repo owner's machine, from *inside* the
`inference-lab` container (see "Methodology note" below for why). The
numbers in "Official results" are real, measured values - nothing in this
report is invented or estimated. The limit was reverted to healthy
(`INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` unset, container recreated)
immediately after - see "Reverted to healthy" below.

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

## Methodology note: the WSL → Windows → Docker Desktop transport confound

The repo owner's development machine runs Docker Desktop on Windows with
the client (WSL) issuing requests to `localhost:8000`, which Docker Desktop
forwards from the WSL/Windows network boundary into the Linux VM running
the containers. At low-to-moderate concurrency this path is transparent,
but **at concurrency 100 it became a real client/transport bottleneck in
its own right** - the WSL→Windows→Docker-Desktop hop has its own
connection-handling limits, independent of anything the service or this
project's code does. Running the sweep through that path at high
concurrency would have measured the transport layer, not the server's
actual backpressure behavior - the same category of mistake this project
has caught before (Phase 7's histogram buckets, Phase 6's measurement
distortion), just one layer further out in the stack.

The fix was to eliminate that hop entirely for the decisive comparison:
both the load generator (`load_test.py`) and the server were run **inside
the `inference-lab` container**, talking to `127.0.0.1:8000` over loopback
- no WSL, no Windows networking, no Docker Desktop port-forwarding in the
path at all. This is what "Official results" below reports. It intentionally
trades the full 5-level sweep (5, 10, 25, 50, 100) for a single, clean,
maximally-informative concurrency=100 comparison between unlimited and
limited modes - concurrency=100 is exactly the level where the two modes'
behavior diverges most sharply, so it is sufficient on its own to answer
this phase's core question. The originally-planned tables for the other
concurrency levels (5, 10, 25, 50) are removed from this report rather than
left in with numbers from the confounded network path.

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
| Duration | 30 seconds |
| Concurrency (final decisive comparison) | 100 |
| Prompt (fixed) | `"Tell me about reliability engineering."` |
| `max_tokens` (fixed) | 64 |
| Phase 8A | `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` unset (0, unlimited) |
| Phase 8B | `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10` |
| Load generator location | inside the `inference-lab` container, against `127.0.0.1:8000` (see "Methodology note" above) |

The tooling (`scripts/load_test.py`'s `--duration-seconds` mode,
`scripts/run_experiment.sh`) supports the full 5, 10, 25, 50, 100 sweep
documented below in "How to run it," and remains available for future use
- it's the transport path, not the tooling, that ruled out the full sweep
as this report's authoritative record.

`httpx.AsyncClient` in `load_test.py` now sets
`Limits(max_connections=concurrency + 20, max_keepalive_connections=concurrency)`
for both load-test modes, so the client's own default 100-connection pool
cap can never become the bottleneck at concurrency=100.

## How to run it

**General sweep recipe** (works at any concurrency level, from any client
location that doesn't introduce a transport bottleneck of its own - see
"Methodology note" above):

```bash
docker compose up -d --build inference-lab   # Phase 8A: unlimited
# or: INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10 docker compose up -d --build inference-lab   # Phase 8B

for c in 5 10 25 50 100; do
  scripts/run_experiment.sh \
    --duration-seconds 30 --concurrency "$c" \
    --prompt "Tell me about reliability engineering." --max-tokens 64 \
    --url http://localhost:8000 --prometheus-url http://localhost:9090 \
    --backend mock \
    --out "experiments/phase8a_concurrency_${c}_metrics.json"   # or phase8b_...
done
```

**What was actually run for the official result**: exactly the concurrency
100 iteration of that loop, twice (once unlimited, once with
`INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS=10`), executed **from a shell
inside the `inference-lab` container** so `--url` pointed at
`http://127.0.0.1:8000` rather than a host-forwarded address:

```bash
docker compose exec inference-lab sh
# inside the container:
python scripts/load_test.py --duration-seconds 30 --concurrency 100 \
  --prompt "Tell me about reliability engineering." --max-tokens 64 \
  --url http://127.0.0.1:8000
```

**Revert to healthy afterward:**

```bash
unset INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS
docker compose up -d --build inference-lab
```

**Windows PowerShell** (same pattern as Phase 6/7, for the host-side
`docker compose up` step - the `docker compose exec` step above is
identical on any host):

```powershell
$env:INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS="10"
docker compose up -d --build inference-lab
# ... run scripts/run_experiment.sh via a Bash-capable shell (WSL, Git Bash) as before ...
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
every result below in "Official results" is a real Docker Compose run.

## Official results

Real Docker Compose run, concurrency 100, 30 seconds, load generator and
server both inside the `inference-lab` container (loopback - see
"Methodology note" above). Raw artifacts:
[`phase8a_concurrency_100_metrics.json`](phase8a_concurrency_100_metrics.json),
[`phase8b_concurrency_100_metrics.json`](phase8b_concurrency_100_metrics.json).
Figures below are client-side `load_test.py` measurements (end-to-end,
including the near-zero loopback network overhead) unless noted otherwise.

| | Unlimited | Limit=10 |
|---|---|---|
| Total requests (30s window) | 3518 | 1772 |
| Errors | 0 (0.00%) | 0 (0.00%) |
| Wall time | 31.11 s | 31.97 s |
| Throughput | **113.09 req/s** | **55.42 req/s** |
| Latency mean | 862.84 ms | 1745.41 ms |
| Latency p50 | **297.66 ms** | 1783.10 ms |
| Latency p95 | **3776.81 ms** | 1943.07 ms |
| Latency p99 | **5273.51 ms** | 1998.10 ms |
| Latency max | 5537.81 ms | 2061.78 ms |
| Max `inference_requests_in_flight` (Prometheus) | - | **100** |
| Max `inference_generations_active` (Prometheus) | - | **10** |

**The key gauge proof** (Phase 8B, limit=10): `inference_requests_in_flight`
peaked at exactly **100** - every one of the 100 concurrently-issued
requests was accepted into the `/generate` handler - while
`inference_generations_active` peaked at exactly **10** - the semaphore
limit. The other ~90 requests, at any given instant, were inside the
handler but blocked waiting to acquire a generation slot: **100 requests
entered the inference path, only 10 could execute `backend.generate()`
simultaneously, and roughly 90 waited behind the semaphore** - this is a
direct, unambiguous measurement of the admission-control mechanism
working exactly as designed, not an inference from latency shape alone.

### The unexpected finding: unlimited mode's tail is worse than the limited mode's

The naive prediction from this project's architecture analysis (a
non-blocking `asyncio.sleep`-based backend has no compute to saturate, so
concurrency alone shouldn't create queueing) held for p50 - **297.66ms is
close to the Phase 5-7 healthy baseline's ~175-300ms range** - but broke
down badly for the tail. At concurrency 100, unlimited mode's p95
(3776.81ms) and p99 (5273.51ms) are **worse than the limited mode's p99**
(1998.10ms), despite the limited mode doing barely half the throughput.
The sandbox rehearsal below (at concurrency 25, non-Dockerized) did not
show this - it only appears here, in the real container, at the higher
concurrency of 100.

The likely mechanism: even though `backend.generate()` itself burns no
CPU, every request still has real (if individually small) per-request
overhead on the single asyncio event loop - request parsing, Pydantic
validation, the `TimingMiddleware` log line, JSON response encoding. At
100 fully unthrottled concurrent requests, that overhead compounds into
real scheduling contention: most requests still get serviced quickly (the
tight p50), but a subset gets starved behind that contention and pays a
severely elevated latency (the blown-out p95/p99) - a classic unbounded,
chaotic contention signature, distinct from the disciplined FIFO-style
queueing the semaphore produces. The mean (862.84ms) landing close to
`100 concurrency / 862.84ms ≈ 116 req/s` (vs. the observed 113.09 req/s)
confirms the tail, not the typical case, is what's actually setting the
achieved throughput.

The limited mode's own numbers are internally consistent with clean,
predictable admission control: p50 through p99 span a narrow 1783-1998ms
band (no chaotic tail), and `10 slots / ~0.175s average generation time ≈
57 req/s` predicts the observed 55.42 req/s almost exactly - the
throughput ceiling here is a designed, bounded consequence of the limit,
not an emergent side effect of contention.

**This is the central reliability-engineering lesson of Phase 8**:
admission control (the semaphore) traded away about half of unlimited
mode's throughput (55 vs. 113 req/s) in exchange for a dramatically more
predictable and bounded tail latency (p99 1998ms vs. 5273ms) - unbounded
concurrency is not "free" even against a backend with zero compute cost,
because per-request overhead still compounds into real contention once
concurrency is high enough, and a deliberate capacity limit converts that
chaotic contention into disciplined, bounded queueing.

### Did the contrast hold?

**Yes, and it revealed more than the original prediction anticipated.**
The core Phase 8B signature from the approved plan is confirmed exactly:
concurrency 100 → `max_in_flight=100` → only `max_active=10` executing →
~90 queued → throughput approaches a ceiling → error rate stays at 0%.
Phase 8A's "flat latency, scaling throughput" prediction held for p50 but
not for the tail - which is itself the more interesting, more valuable
finding: it shows unbounded concurrency has a real cost even against a
non-blocking backend, and that Phase 8B's admission control isn't just a
queueing demonstration - it's a genuine tail-latency improvement over the
unlimited baseline at this concurrency.

### Reverted to healthy

**Confirmed.** After the Phase 8B run's results were captured,
`INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` was unset and
`docker compose up -d --build inference-lab` was run again, returning the
stack to its `0`-default healthy state - the same revert procedure
documented in Phase 6/7. No checked-in file was ever modified for either
run; the limit existed only as a shell environment variable for the
duration of the Phase 8B run.

## Rehearsal (non-Docker substitute, this development sandbox)

Before asking for the official reruns, the whole pipeline was rehearsed
end-to-end against a directly-run `uvicorn` process and a real Prometheus
v2.55.1 binary (same substitute pattern used in every prior Docker-dependent
phase, since Docker Hub is blocked in this sandbox). **This is not the
official result and is not merged into the table above** - it's a
real (not synthetic), reduced-scale (10s, not 30s; concurrency 25, not 100)
confirmation that the design produces the expected signature:

| | Throughput (req/s) | Gen p50/p95/p99 (ms) | HTTP p50/p95/p99 (ms) | Max in-flight | Max active |
|---|---|---|---|---|---|
| **8A-style** (unlimited, concurrency 25) | 131.4 | 178.8 / 287.8 / 297.7 | 179.8 / 289.2 / 299.0 | 25 | 25 |
| **8B-style** (limit=10, concurrency 25) | 55.0 | 175.0 / 289.0 / 298.5 | 433.2 / 569.6 / 596.7 | 25 | 10 |

This confirmed the core mechanism (in-flight/active gauges, semaphore
bound, throughput drop) exactly. **Notably, it did not show the unlimited
mode's tail-latency blowup** seen in the official concurrency-100 result
above - at concurrency 25, unlimited mode's own HTTP p95/p99 (289.2ms /
299.0ms) stayed tight and close to generation latency, with no sign of
the chaotic contention that appeared at concurrency 100 in the real
container. This is itself a useful finding about rehearsals: a
lower-concurrency, non-Dockerized sandbox check is good enough to validate
that the *instrumentation* works, but not a substitute for the official
run at the actually-planned concurrency level, since the interesting
failure mode here only emerged at the higher concurrency and the extra
overhead of a real containerized process.

## Validation

- **Official Docker Compose run, both modes, concurrency 100** - see
  "Official results" above. This is the authoritative validation of the
  concurrency limit and the backpressure signature it produces.
- `pytest`: `tests/test_concurrency.py` (default-unlimited behavior
  unchanged, the semaphore bounds `inference_generations_active` while
  `inference_requests_in_flight` rises higher, startup warning logged only
  when the limit is active, negative config rejected) and
  `tests/test_metrics.py` (both gauges appear in `/metrics` and settle back
  to 0 after a request) - full suite 36 passed, 2 skipped (no local GPU).
  Ruff clean.
- Real (non-synthetic) rehearsal against a directly-run app + real
  Prometheus in the development sandbox, described above - ran before
  asking for the official reruns and confirmed the core mechanism (though
  not the tail-latency finding, which only appeared at the higher official
  concurrency level).
- `monitoring/prometheus.yml` re-validated with `promtool check config`
  (unchanged by this phase).
- `docker-compose.yml` re-validated with `docker compose config`, including
  both the new `INFERENCE_LAB_MAX_CONCURRENT_GENERATIONS` default (`"0"`)
  and an override (`"10"`) resolving correctly.

## Compatibility with prior phase reports

`phase5_healthy_baseline.md`, `phase6_latency_fault.md`, and
`phase7_histogram_fix.md` are unchanged by this phase. Unlimited mode's
p50 (297.66ms) is consistent with Phase 5-7's ~175-300ms healthy range,
confirming that the typical-case behavior this project has measured since
Phase 5 still holds even at concurrency 100 - it's specifically the tail
that changes at high concurrency, a distinction the earlier phases (all
run at concurrency 5) had no way to surface.
