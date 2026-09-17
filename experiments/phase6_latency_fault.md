# Phase 6: Controlled Latency Fault

A single, deliberate, reversible fault - a fixed extra delay added to every
mock-backend request - measured with the exact same workload and tooling as
[`phase5_healthy_baseline.md`](phase5_healthy_baseline.md), so the two
reports are a direct, row-by-row comparison.

## Status

**Results: PENDING.** The fault-injection code, config, tests, and
Compose wiring are implemented and validated (see "Validation" below).
The actual failure-run numbers have not been filled in - producing them
requires running the real workload against the Docker Compose stack, and
this development sandbox's network policy blocks Docker Hub (same
limitation as every Docker-dependent phase so far). No numbers are
invented in their place.

## The fault

- **What**: a fixed extra delay, added on top of the mock backend's
  existing random `uniform(50, 300)`ms latency, on every `/generate`
  call.
- **Where**: `src/inference_lab/backends/mock.py` - `latency_ms =
  random.uniform(min, max) + extra_latency_ms`, one line added to the
  existing latency calculation. Nothing else about the mock backend
  changes.
- **Control**: `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS` (default `0` -
  rejected if negative). At `0`, behavior is byte-for-byte identical to
  Phase 5.
- **Value for this experiment**: `500` (ms).
- **Visibility**: a `WARNING`-level log line
  (`Mock backend fault injection active: +500ms extra latency on every
  request`) is emitted once at startup whenever the value is nonzero, so
  an active fault is always operationally obvious, never silent.

## Workload (identical to Phase 5)

| Parameter | Value |
|---|---|
| Backend | `mock` (`INFERENCE_LAB_BACKEND=mock`) |
| Total requests | 1000 |
| Concurrency | 5 |
| Prompt (fixed) | `"Tell me about reliability engineering."` |
| `max_tokens` (fixed) | 64 |
| Target | `inference-lab` container via Docker Compose (`http://localhost:8000`) |
| **Fault** | `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500` |

Same tooling, unmodified: `scripts/run_experiment.sh` +
`scripts/capture_prometheus_metrics.py`, same 2s Prometheus scrape
interval, same pre/post settle buffers. Nothing about the measurement
method changes between a healthy run and a fault run - only the fault
itself.

## How to run it

**Linux/macOS:**

```bash
INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500 docker compose up -d --build inference-lab

scripts/run_experiment.sh \
  --requests 1000 --concurrency 5 \
  --prompt "Tell me about reliability engineering." --max-tokens 64 \
  --url http://localhost:8000 --prometheus-url http://localhost:9090 \
  --backend mock \
  --out experiments/phase6_latency_fault_metrics.json
```

**Windows PowerShell:**

```powershell
$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS="500"
docker compose up -d --build inference-lab
```

`scripts/run_experiment.sh` itself needs a Bash-capable shell (WSL, Git
Bash, or similar) even on Windows - it isn't rewritten in PowerShell.
Once the container is up with the fault active, run the same
`scripts/run_experiment.sh` invocation as above from that shell.

### Reverting to healthy

**Linux/macOS:**

```bash
unset INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS
docker compose up -d --build inference-lab
```

**Windows PowerShell:**

```powershell
$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=$null
docker compose up -d --build inference-lab
```

Setting a PowerShell environment variable to `$null` removes it from the
process environment, so Compose's `${INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS:-0}`
default substitution takes over and the container comes back up with the
fault disabled - no file was ever edited, so there's nothing to "undo."
Confirm the revert by checking `docker compose logs inference-lab` for the
absence of a fresh "fault injection active" warning after the restart, or
by re-running the Phase 5 workload and confirming its numbers land back
in the healthy range.

## Expected diagnostic signature

The fingerprint to look for once real numbers are in: **latency shifts up
in parallel across p50/p95/p99 (a uniform shift, not a stretching tail),
throughput collapses roughly in proportion to the latency increase, error
rate stays at zero, and process CPU/memory stay flat.** That specific
combination is the signature of a *fixed per-request delay* - as opposed
to a queueing/backpressure fault (which would stretch p99 much more than
p50), a CPU-pressure fault (which would raise `process_cpu_avg_cores`),
or a crash/timeout fault (which would raise the error rate). Confirming
this exact pattern is itself the validation that Phase 4B/5's existing
metrics are sufficient to diagnose this class of fault without adding any
new instrumentation.

| Metric | Phase 5 (healthy) | Expected direction under the fault | Why |
|---|---|---|---|
| Latency p50 | 181.54 ms | **Up ~500ms** (~681ms) | Constant additive delay |
| Latency p95 | 289.24 ms | **Up ~500ms** (~789ms) | Same - shift, not stretch |
| Latency p99 | 298.03 ms | **Up ~500ms** (~798ms) | Same - shift, not stretch |
| Request throughput | 23.726 req/s | **Down sharply** (~7-8 req/s) | throughput ≈ concurrency ÷ mean latency, and mean latency roughly quadruples |
| Token throughput | 118.628 tok/s | **Down proportionally** with throughput | Same 5000 total tokens, spread over a much longer window |
| Error rate | 0.00% | **~0%, unchanged** | Client timeout (30s) is far above the ~680ms expected mean - no timeouts triggered |
| Process CPU (avg) | 0.0403 cores | **Flat or lower** | The delay is `asyncio.sleep()` - non-blocking, consumes no CPU |
| Process memory | 55.74 / 55.54 MB | **Flat** | No new allocations from a sleep |
| Active backend | `backend=mock` | **Unchanged** | Confirms only latency behavior changed, not which backend is running |
| Run window | 42.15 s | **Much longer** (roughly 135-140s) | Same 1000 requests, ~500ms slower each, fixed concurrency |

## Results

*(Pending a real run against the Docker Compose stack - see "Status" above.)*

### Prometheus (authoritative)

| Metric | Value |
|---|---|
| Window | — |
| Total requests | — |
| Total errors | — |
| Request throughput | — |
| Error rate | — |
| Latency p50 | — |
| Latency p95 | — |
| Latency p99 | — |
| Total completion tokens | — |
| Token throughput | — |
| Process CPU (avg) | — |
| Process memory (max) | — |
| Process memory (avg) | — |
| Active backend | — |

### Client-side cross-check (secondary, not authoritative)

| Metric | Value |
|---|---|
| Wall time | — |
| Error rate | — |
| Throughput | — |
| Latency mean | — |
| Latency p50 | — |
| Latency p95 | — |
| Latency p99 | — |
| Latency max | — |

### Comparison against the Phase 5 baseline

*(To be filled in once real numbers land - a row-by-row diff against
[`phase5_healthy_baseline.md`](phase5_healthy_baseline.md)'s Results
table, checked against the "expected diagnostic signature" table above.)*

## Validation (before the real run)

- `tests/test_mock_backend.py` (new): default behavior unchanged when
  `extra_latency_ms=0` (no warning logged, latency stays in the normal
  50-300ms range); the extra delay actually adds real wall-clock time
  (measured, not just asserted); the warning log fires with the correct
  value when the fault is active; `Settings` rejects a negative
  `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS` with a validation error; the
  default is confirmed `0`.
- Full pytest suite: 27 passed, 2 skipped (no local GPU). Ruff clean.
- `docker-compose.yml`'s new `${INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS:-0}`
  substitution checked with `docker compose config`: resolves to `"0"`
  when unset, and to `"500"` when the shell env var is set - confirmed
  both ways.
- `monitoring/prometheus.yml` re-validated with `promtool check config`
  (unchanged by this phase, re-checked for hygiene).
- Functional smoke test against a directly-run `uvicorn` process (not
  Docker, since Docker Hub is blocked in this sandbox): with
  `INFERENCE_LAB_MOCK_MIN_LATENCY_MS=0`, `INFERENCE_LAB_MOCK_MAX_LATENCY_MS=0`,
  and `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500`, the startup warning logged
  correctly and a real `/generate` call measured `latency_ms: 499.97` -
  confirming the fault applies precisely as designed. A second run with
  the variable unset logged no warning and returned a latency inside the
  normal 50-300ms range, confirming the default path is unaffected.

## How this compares against later experiments

This report follows the same shape as
[`phase5_healthy_baseline.md`](phase5_healthy_baseline.md) and will be
followed by later failure-injection experiments (Phase 7+) in the same
format, so the effect of any eventual fix can be judged directly: "does
the fixed run's numbers move back toward this baseline," not a judgment
call.
