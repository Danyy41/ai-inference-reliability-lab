# Phase 5: Healthy Baseline

The reference measurement every later failure-injection experiment gets
compared against. This run intentionally injects no failures - it exists to
establish "what does this system look like when nothing is wrong," using
the same tooling and methodology every future experiment will reuse.

## Status

**Complete.** This run was executed against the real Docker Compose stack
(app + Prometheus + Grafana, `docker compose up --build`) on the repo
owner's machine, using `scripts/run_experiment.sh` exactly as documented
below. The numbers in "Results" are real, measured values - nothing in
this report is invented or estimated.

**This is the canonical healthy mock baseline.** Every later
failure-injection experiment (Phase 6+) is expected to run the identical
workload below via `scripts/run_experiment.sh` and compare its Prometheus
numbers directly against this report's Results table. A regression is
"this run's numbers are worse than this baseline's," not a judgment call.

To reproduce it:

```bash
docker compose up --build -d
scripts/run_experiment.sh \
  --requests 1000 --concurrency 5 \
  --prompt "Tell me about reliability engineering." --max-tokens 64 \
  --url http://localhost:8000 --prometheus-url http://localhost:9090 \
  --backend mock \
  --out experiments/phase5_healthy_baseline_metrics.json
```

## Workload

| Parameter | Value |
|---|---|
| Backend | `mock` (`INFERENCE_LAB_BACKEND=mock`) |
| Total requests | 1000 |
| Concurrency | 5 |
| Prompt (fixed) | `"Tell me about reliability engineering."` |
| `max_tokens` (fixed) | 64 |
| Target | `inference-lab` container via Docker Compose (`http://localhost:8000`) |

1000 requests (not the originally-proposed 100) so the run lasts tens of
seconds rather than a handful - long enough for `rate()`/`increase()` and
the latency histogram to be based on many real observations rather than
one or two.

Note on tokens: the mock backend computes `completion_tokens =
min(max_tokens, word_count_of_prompt)`. The prompt has 5 words, so every
request deterministically generates exactly 5 completion tokens (5000
total across the run) - only latency is randomized (uniform 50-300ms per
request), not token count.

## Metrics captured

All nine come from Prometheus, not from the load generator's own
client-side view (which cannot see server-side CPU/memory/backend-info at
all, and only approximates latency/throughput from outside):

1. **Request throughput** - requests/sec over the exact workload window
2. **p50 latency**, **p95 latency**, **p99 latency** - from the
   `inference_generation_latency_seconds` histogram
3. **Error rate** - failed / total requests
4. **Token throughput** - completion tokens/sec over the exact workload
   window
5. **Process CPU** - average CPU cores consumed during the run
6. **Process memory** - resident memory observed during the run
7. **Active backend** - which backend/device/model was serving (from
   `inference_backend_info`)

## Methodology: how results are captured from Prometheus

`scripts/run_experiment.sh` orchestrates the whole run:

1. **Pre-workload settle (6s)** - sleeps before starting, so Prometheus's
   scrape interval (2s, see below) has already produced a fresh, at-rest
   sample before the burst begins.
2. **Load test** - runs `scripts/load_test.py` with the exact workload
   parameters above, recording the real start/end wall-clock timestamps
   (sub-second precision) around it.
3. **Post-workload settle (6s)** - sleeps after the load test finishes, so
   Prometheus has time to scrape the fully-settled final state rather than
   a value from mid-burst.
4. **Capture** - `scripts/capture_prometheus_metrics.py` queries
   Prometheus for the nine metrics above, using the exact `[start, end]`
   window from step 2.

Two different query techniques are used deliberately, not one uniform
approach:

- **Counters that feed a rate over the exact workload duration** (request
  throughput, error rate, token throughput, process CPU) are captured as
  an **instant-query snapshot diff**: one Prometheus query at precisely
  `start_ts`, one at `end_ts` (after the post-workload settle buffer),
  subtracted and divided by the *exact* `end_ts - start_ts` - not a padded
  range. Padding the range here would attribute idle-period CPU/requests
  from before or after the burst to the workload, inflating the numbers.
- **The latency histogram and the memory gauge** instead use a **padded**
  range query (`[start_ts - 6s, eval_ts]`), evaluated once after the
  settle buffer. Padding doesn't bias these: idle time contributes zero
  histogram observations either way, and padding the memory window just
  protects against missing a real scrape sample near the edges.
  `max_over_time()`/`avg_over_time()` are used for memory so it reflects
  more than a single point-in-time gauge read.

This pre/post-buffer design is why the requirement "make sure Prometheus
has samples immediately before and after the workload, not just the exact
request timestamps" is met: the settle buffers *guarantee* real scrapes
land outside the burst window (given the 2s scrape interval), and the
instant queries are anchored to timestamps chosen to land on those settled
samples rather than assuming any particular scrape landed exactly on the
first or last request.

### Prometheus scrape interval: 2s, not the default 15s

`monitoring/prometheus.yml` was changed from the default 15s scrape
interval to 2s for this and all future experiments. With a workload
lasting tens of seconds, a 15s interval would produce only 2-3 real
samples total - workable for counters (which accumulate correctly
regardless of scrape timing) but poor for the memory *gauge*, which is
only ever as fresh as the last scrape. At 2s, a 36-second run produces on
the order of 18 real samples, giving `max_over_time()` a meaningful chance
of catching an actual peak rather than one arbitrary point.

## Results

### Prometheus (authoritative)

These are the canonical baseline numbers - captured server-side, from the
app's own `/metrics`, via `scripts/capture_prometheus_metrics.py`, for the
exact `[start, end]` window of the run described above. Raw JSON:
[`phase5_healthy_baseline_metrics.json`](phase5_healthy_baseline_metrics.json)
(reconstructed at the precision the script's own printed report uses -
2-4 decimal places per field, exactly as shown below; the machine that ran
the experiment holds the original file with full floating-point
precision, if that's ever needed).

| Metric | Value |
|---|---|
| Window | 42.15 s |
| Total requests | 1000 |
| Total errors | 0 |
| Request throughput | 23.726 req/s |
| Error rate | 0.00% |
| Latency p50 | 181.54 ms |
| Latency p95 | 289.24 ms |
| Latency p99 | 298.03 ms |
| Total completion tokens | 5000 |
| Token throughput | 118.628 tok/s |
| Process CPU (avg) | 0.0403 cores |
| Process memory (max) | 55.74 MB |
| Process memory (avg) | 55.54 MB |
| Active backend | `backend=mock device=n/a model=n/a` |

### Client-side cross-check (secondary, not authoritative)

`load_test.py`'s own view of the same run, printed to stdout during step 2
of `run_experiment.sh`. This is **not** the source of truth - it can't see
process CPU, memory, or token counts at all, and its latency is measured
end-to-end from the client (network + HTTP overhead included), not the
server's own generation time. It exists only as a sanity check that the
two independent measurements roughly agree.

| Metric | Value |
|---|---|
| Wall time | 40.61 s |
| Error rate | 0.00% |
| Throughput | 24.63 req/s |
| Latency mean | 198.65 ms |
| Latency p50 | 200.70 ms |
| Latency p95 | 308.70 ms |
| Latency p99 | 327.16 ms |
| Latency max | 368.19 ms |

### Reading the two side by side

The client-side numbers run consistently a bit higher than Prometheus's -
expected, since the client's latency includes the full HTTP round trip
(network, ASGI, `TimingMiddleware`) on top of the server-recorded
generation time alone, and its throughput is computed over `load_test.py`'s
own wall-clock window rather than the settle-buffered `[start, end]` window
Prometheus was queried over. The two datasets agreeing this closely (p50
within ~20ms, p95 within ~20ms, p99 within ~30ms, throughput within ~1
req/s) is itself a useful confirmation that both measurement paths are
sound - a real discrepancy between them (not just this small, expected
gap) would be worth investigating in its own right in a future experiment.

This real run's Prometheus numbers also land close to the non-Docker
substitute validation described below (p95 289.24ms here vs. ~287ms in the
substitute run, p99 298.03ms vs. ~298ms) - reassuring evidence that the
substitute validation was a reasonable proxy for the real containerized
stack.

## Tooling validation (before the real run above)

Before the real Docker Compose run, the scripts and config were validated
in the development sandbox that built this phase, using a non-Docker
substitute (the same pattern used for Phase 4A/4B, since Docker Hub is
blocked in that sandbox): the app run directly via `uvicorn`, and a real
Prometheus v2.55.1 binary (fetched from GitHub releases, not Docker Hub)
run against it with the same 2s scrape interval. This was **not** a
Docker-Compose run and its numbers were never reported as the baseline -
it existed only to prove the tooling itself was correct before asking for
a real run.

- `scripts/run_experiment.sh` ran the full 1000-request/concurrency-5
  workload end-to-end against this substitute setup without error.
- `scripts/capture_prometheus_metrics.py` successfully queried all nine
  metrics and printed a complete report.
- **A real bug was caught and fixed by this validation**: the original
  latency histogram buckets had a 250ms-500ms gap with zero real
  observations in it (mock latency tops out at 300ms), which made
  `histogram_quantile`'s linear interpolation overshoot p95/p99 by
  40-60% relative to the client-observed values (e.g. p99 reported as
  488ms against a true client-observed 301ms). Fixed by adding finer
  bucket boundaries between 50ms and 500ms in
  `src/inference_lab/observability/metrics.py`. After the fix, a re-run
  showed Prometheus-derived and client-observed percentiles agreeing
  closely (p95: 287ms vs. 292ms; p99: 298ms vs. 302ms).
- Also fixed: `run_experiment.sh` originally used `date +%s` (whole-second
  precision) for the workload window boundaries, which could introduce up
  to ~1s of error into a ~36s window. Changed to `date +%s.%N`.
- `monitoring/prometheus.yml` validated with `promtool check config`.
- `docker-compose.yml` validated with `docker compose config` (still valid
  after the scrape-interval change).
- Full pytest suite (21 passed, 2 skipped - no local GPU) and Ruff clean
  after the bucket-definition change.

## How future failure-injection runs will compare against this baseline

- Every future experiment reuses `scripts/run_experiment.sh` and
  `scripts/capture_prometheus_metrics.py` unchanged, with the same
  workload parameters unless a specific experiment deliberately varies
  one - so the only thing that differs between runs is whatever failure
  was injected, not the measurement method.
- Each experiment produces its own `experiments/phaseN_<name>.md` in this
  same report shape (same nine metrics, same table), making before/after
  comparisons a direct row-by-row diff instead of eyeballing differently
  formatted numbers.
- Because metrics are pulled with explicit `[start, end]` windows rather
  than "whatever Grafana shows right now," this baseline's numbers stay
  valid for comparison even after the live stack has moved on to a
  different, later experiment.
