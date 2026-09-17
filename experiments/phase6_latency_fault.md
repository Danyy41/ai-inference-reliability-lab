# Phase 6: Controlled Latency Fault

A single, deliberate, reversible fault - a fixed extra delay added to every
mock-backend request - measured with the exact same workload and tooling as
[`phase5_healthy_baseline.md`](phase5_healthy_baseline.md), so the two
reports are a direct, row-by-row comparison.

## Status

**Complete.** This run was executed against the real Docker Compose
stack on the repo owner's machine, using `scripts/run_experiment.sh`
exactly as documented below, with `INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500`
active. The numbers in "Results" are real, measured values - nothing in
this report is invented or estimated. See "Reverted to healthy" below
for the required last step of the experiment and how to confirm it.

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

### Reverted to healthy

**Required last step of this experiment.** After the results below were
captured, revert with the commands above (unset the variable, then
`docker compose up -d --build inference-lab` again) to return the stack
to the same `0`-default, unmodified-`docker-compose.yml` healthy state
Phase 5 was measured in. No checked-in file differs between "healthy" and
"fault" - the fault exists only as a shell environment variable for the
duration of an experiment, so reverting is exactly that one step, with
nothing else to undo. Confirm it the same way described above: no fresh
"fault injection active" warning in `docker compose logs inference-lab`
after the restart.

## Expected diagnostic signature (predicted before the run)

The fingerprint predicted before running the experiment: **latency shifts
up in parallel across p50/p95/p99 (a uniform shift, not a stretching
tail), throughput collapses roughly in proportion to the latency
increase, error rate stays at zero, and process CPU/memory stay flat.**
That specific combination is the signature of a *fixed per-request
delay* - as opposed to a queueing/backpressure fault (which would stretch
p99 much more than p50), a CPU-pressure fault (which would raise
`process_cpu_avg_cores`), or a crash/timeout fault (which would raise the
error rate). See "Explanation of the observed diagnostic signature"
below for how the real results compared against this prediction -
confirmed overall, with one genuine and useful nuance.

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

### Prometheus (authoritative)

Raw JSON: [`phase6_latency_fault_metrics.json`](phase6_latency_fault_metrics.json)
(reconstructed at the precision the script's own printed report uses,
same approach as the Phase 5 JSON).

| Metric | Value |
|---|---|
| Window | 145.19 s |
| Total requests | 1000 |
| Total errors | 0 |
| Request throughput | 6.888 req/s |
| Error rate | 0.00% |
| Latency p50 | 653.75 ms |
| Latency p95 | 933.15 ms |
| Latency p99 | 986.63 ms |
| Total completion tokens | 5000 |
| Token throughput | 34.438 tok/s |
| Process CPU (avg) | 0.0183 cores |
| Process memory (max) | 56.31 MB |
| Process memory (avg) | 56.04 MB |
| Active backend | `backend=mock device=n/a model=n/a` |

### Client-side cross-check (secondary, not authoritative)

| Metric | Value |
|---|---|
| Wall time | 139.45 s |
| Error rate | 0.00% |
| Throughput | 7.17 req/s |
| Latency mean | 692.17 ms |
| Latency p50 | 693.36 ms |
| Latency p95 | 801.20 ms |
| Latency p99 | 816.58 ms |
| Latency max | 1100.41 ms |

### Comparison against the Phase 5 baseline

| Metric | Phase 5 (healthy) | Phase 6 (fault, +500ms) | Change |
|---|---|---|---|
| Request throughput | 23.726 req/s | 6.888 req/s | **÷3.44** |
| Latency p50 | 181.54 ms | 653.75 ms | **+472.2 ms** |
| Latency p95 | 289.24 ms | 933.15 ms | **+643.9 ms** |
| Latency p99 | 298.03 ms | 986.63 ms | **+688.6 ms** |
| Token throughput | 118.628 tok/s | 34.438 tok/s | **÷3.44** |
| Error rate | 0.00% | 0.00% | unchanged |
| Process CPU (avg) | 0.0403 cores | 0.0183 cores | **÷2.2 (lower)** |
| Process memory (avg) | 55.54 MB | 56.04 MB | +0.5 MB (flat) |
| Window | 42.15 s | 145.19 s | **×3.44** |

## Explanation of the observed diagnostic signature

The overall pattern matches the predicted signature well, with one
genuinely interesting nuance worth calling out rather than glossing over.

**What matched exactly as predicted:**

- **Throughput, token throughput, and window duration all moved by the
  same ratio** - 3.44-3.45x across all three (÷3.4445 for throughput,
  ÷3.4447 for tokens, ×3.4446 for window). That's not a coincidence: with
  concurrency fixed at 5 and a fixed 5 tokens/request, these three are
  mechanically linked (throughput ≈ concurrency ÷ mean latency, tokens/s
  = throughput × 5, window ≈ requests ÷ throughput). Seeing them move in
  lockstep is exactly what a *pure latency* fault should produce - a
  fault that also consumed extra CPU or memory per request would break
  this clean proportionality.
- **Error rate stayed at 0.00%.** No timeouts, no crashes - confirms this
  is purely a latency fault, not a reliability fault, at 500ms.
- **Process CPU dropped** (0.0403 → 0.0183 cores), it didn't rise. This
  is the key fingerprint that distinguishes this fault from a CPU-pressure
  fault: the extra time is spent in a non-blocking `asyncio.sleep()`, so
  the same amount of actual CPU work is now spread over a ~3.44x longer
  wall-clock window, dropping average utilization. A CPU-bound fault would
  have shown the opposite.
- **Process memory stayed flat** (55.54 → 56.04 MB, +0.5MB) - no
  allocation growth from a sleep, as expected.
- **Active backend unchanged** (`backend=mock`) - confirms only latency
  behavior changed.

**The nuance: p50 shifted less than p95/p99 in the Prometheus numbers,
but not in the client-side numbers.**

A constant additive delay should shift every percentile by the *same*
amount - adding a fixed 500ms to a random variable shifts its entire
distribution, including every percentile, by exactly 500ms. The
client-side numbers confirm this almost perfectly:

| Percentile | Client shift (Phase 5 → 6) |
|---|---|
| p50 | 200.70 → 693.36 ms = **+492.7 ms** |
| p95 | 308.70 → 801.20 ms = **+492.5 ms** |
| p99 | 327.16 → 816.58 ms = **+489.4 ms** |

Three shifts within 3ms of each other - textbook uniform shift.

The **Prometheus-derived** shifts, by contrast, grow with the percentile:

| Percentile | Prometheus shift (Phase 5 → 6) |
|---|---|
| p50 | 181.54 → 653.75 ms = **+472.2 ms** |
| p95 | 289.24 → 933.15 ms = **+643.9 ms** |
| p99 | 298.03 → 986.63 ms = **+688.6 ms** |

This divergence is not a real behavioral difference - it's a **histogram
bucket resolution artifact**, the same class of issue fixed in Phase 5's
validation, just showing up in a different range this time. The Phase 5
fix densified `_LATENCY_BUCKETS_SECONDS` between 50ms and 500ms (where
the *healthy* distribution lives), but the fault run's distribution now
lives in the 550ms-1000ms decade, where the buckets are still coarse:
`..., 0.5, 0.75, 1, ...` - 250ms gaps. `histogram_quantile`'s linear
interpolation within a bucket that wide overstates p95/p99 exactly the
way it did before the Phase 5 fix, just at a different, currently-unfixed
part of the range. The client-side numbers, unaffected by histogram
bucket boundaries, show what's actually true: a clean, uniform ~490ms
shift.

**Takeaway for future experiments**: this is a real, useful finding, not
a discrepancy to hide. It confirms the value of keeping the client-side
cross-check (exactly this kind of artifact is what it's for), and it
flags a follow-up worth doing before running experiments whose fault
value pushes latencies past ~500ms: extend
`_LATENCY_BUCKETS_SECONDS` with denser boundaries in whatever range the
faulted distribution will land in, the same way Phase 5 densified
50-500ms for the healthy range. Not done in this phase, since it's a
measurement-tooling refinement rather than part of this fault's own
scope - noted here for Phase 7+ to pick up if needed.

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
