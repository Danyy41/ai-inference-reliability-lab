# Phase 7: Histogram Bucket Resolution Fix

Diagnoses and fixes the measurement distortion
[`phase6_latency_fault.md`](phase6_latency_fault.md) exposed: Prometheus's
`histogram_quantile`-derived p95/p99 overshot the true latency by
130-170ms during the +500ms fault experiment, even though the client-side
cross-check showed a clean, uniform ~490ms shift. This report does not
change or replace the historical Phase 5/6 records - it diagnoses the
measurement tool, fixes it, and reruns the identical workload to prove
the fix.

## Status

**Diagnosis, fix, and regression test: complete.** **Official reruns
(healthy + +500ms fault) against the Docker Compose stack: PENDING.**
This development sandbox's network policy blocks Docker Hub (the same
limitation as every prior Docker-dependent phase), so the reruns below
must happen on the repo owner's machine. No rerun numbers are invented in
their place. A non-Docker rehearsal of the fix (see "Rehearsal" below)
was run in this sandbox and is reported separately, clearly labeled as
not the official result.

## Diagnosis: why `histogram_quantile` distorted p95/p99

Both latency histograms
(`inference_generation_latency_seconds`, `http_request_duration_seconds`)
share `_LATENCY_BUCKETS_SECONDS` in `src/inference_lab/observability/metrics.py`.
Before this phase, the buckets between 500ms and 1s were only `0.75` and
`1.0` - a 250ms gap on each side.

Phase 6's fault produces `latency = uniform(50,300)ms + 500ms`, i.e. a
true distribution **uniform on [550ms, 800ms]**. Mapped onto the old
buckets:

| Bucket (`le`) | Cumulative % of observations |
|---|---|
| `≤0.5s` | 0% (true minimum is 550ms) |
| `≤0.75s` | 80% (everything in [550, 750]ms) |
| `≤1.0s` | 100% (the remaining [750, 800]ms) |

All 1000 observations land in just **two** buckets. `histogram_quantile`
finds the bucket containing the target rank and **linearly interpolates
across that bucket's full width**, implicitly assuming the observations
inside it are spread evenly. Redoing Prometheus's own interpolation
formula by hand, for the `(0.75, 1.0]` bucket (which spans 80%→100%):

- **p95** (rank 0.95): `0.75 + 0.25 × (0.95−0.80)/(1.00−0.80) = 0.9375s` = **937.5ms**. Real Phase 6 report: **933.15ms** ✓
- **p99** (rank 0.99): `0.75 + 0.25 × (0.99−0.80)/(1.00−0.80) = 0.9875s` = **987.5ms**. Real Phase 6 report: **986.63ms** ✓

These hand-computed values match the real Phase 6 numbers to within a
millisecond, confirming the mechanism precisely: the bucket's true
occupied sub-range is only [750, 800]ms - 50ms wide - but linear
interpolation spreads that 20% of the probability mass across the entire
250ms bucket, so it overestimates wherever the target rank falls inside
it. p50 was far more accurate only because the bucket it landed in
happened to have a smaller true/interpolated mismatch.

## The fix

`_LATENCY_BUCKETS_SECONDS` extended from 27 to 39 buckets: the existing
buckets below 500ms and above 1.5s are unchanged (they were never the
problem); the 500ms-1.5s range gets dense ~50ms steps up to 1.0s, tapering
to ~100ms steps up to 1.5s, replacing the old `0.75, 1` pair:

```python
_LATENCY_BUCKETS_SECONDS = (
    0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5,
    0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0,   # new
    1.1, 1.2, 1.3, 1.4, 1.5,                                 # new
    2, 3, 5, 7.5, 10, 15, 20, 30, 45, 60, 90, 120,
)
```

**Time-series cost**: +12 bucket boundaries × the small number of real
label combinations in use (`backend` ∈ {mock, huggingface};
`(method, path)` ∈ 3 real route combinations) adds roughly 60 additional
`_bucket` time series total across both histograms - a trivial addition
for a system whose whole purpose is measuring latency faults accurately,
and it only densifies the specific range shown to be a problem.

## Regression test

`tests/test_metrics.py` re-implements Prometheus's own `histogram_quantile`
linear-interpolation algorithm in Python and runs it against a
deterministic synthetic distribution - 1000 evenly-spaced values on
[550ms, 800ms], matching the Phase 6 fault's true shape exactly (no
randomness, so no flakiness) - through a real `prometheus_client`
`Histogram` with both the new and the old bucket sets:

| Percentile | True value | New-bucket estimate (error) | Old-bucket estimate (error) |
|---|---|---|---|
| p50 | 675.00 ms | 675.00 ms (**0.00ms**) | 656.25 ms (18.75ms) |
| p95 | 787.50 ms | 787.50 ms (**0.00ms**) | 937.50 ms (150.00ms) |
| p99 | 797.50 ms | 797.50 ms (**0.00ms**) | 987.50 ms (190.00ms) |

`test_new_buckets_keep_p95_p99_interpolation_error_small_for_the_phase6_fault_shape`
asserts the new-bucket error stays ≤30ms for p95/p99 (it's exactly 0ms
for this synthetic case - the values happen to land on the new, denser
boundaries). `test_old_buckets_would_have_overshot_p99_for_the_phase6_fault_shape`
asserts the old-bucket error exceeds 100ms, keeping the bug's reproduction
in the suite permanently rather than just noting it in prose.

## What would demonstrate the fix worked (official Docker Compose reruns)

Rerun the exact same workload as Phase 5/6 - 1000 requests, concurrency 5,
prompt `"Tell me about reliability engineering."`, `max_tokens=64` - via
`scripts/run_experiment.sh`, twice: once healthy, once with
`INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500`.

**Success signal (per the approved plan - a desirable target, not a rigid
pass/fail gate)**: the gap between Prometheus's p95/p99 and the
client-side cross-check's p95/p99 should shrink from the ~130-170ms seen
in Phase 6 down to roughly **~20-30ms**, similar to the tightness p50
already had. **Should stay materially unchanged versus Phase 5/6** (this
is a measurement-resolution fix, not a behavior change): request
throughput, error rate, process CPU, process memory, and active backend
identity. The **healthy** rerun in particular should reproduce Phase 5's
numbers closely, confirming no regression in the range that was already
accurate.

### Official rerun commands

```bash
# Healthy rerun (new buckets, no fault)
docker compose up -d --build inference-lab
scripts/run_experiment.sh \
  --requests 1000 --concurrency 5 \
  --prompt "Tell me about reliability engineering." --max-tokens 64 \
  --url http://localhost:8000 --prometheus-url http://localhost:9090 \
  --backend mock \
  --out experiments/phase7_healthy_rerun_metrics.json

# +500ms fault rerun (new buckets)
INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500 docker compose up -d --build inference-lab
scripts/run_experiment.sh \
  --requests 1000 --concurrency 5 \
  --prompt "Tell me about reliability engineering." --max-tokens 64 \
  --url http://localhost:8000 --prometheus-url http://localhost:9090 \
  --backend mock \
  --out experiments/phase7_fault_rerun_metrics.json

# Revert to healthy afterward
unset INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS
docker compose up -d --build inference-lab
```

(Windows PowerShell: `$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS="500"` /
`$env:INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=$null`, same as Phase 6.)

## Official rerun results

*(Pending - see "Status" above.)*

### Healthy rerun (new buckets) vs. Phase 5

| Metric | Phase 5 (original) | Phase 7 healthy rerun | Consistent? |
|---|---|---|---|
| Request throughput | 23.726 req/s | — | — |
| Latency p50 | 181.54 ms | — | — |
| Latency p95 | 289.24 ms | — | — |
| Latency p99 | 298.03 ms | — | — |
| Error rate | 0.00% | — | — |
| Process CPU (avg) | 0.0403 cores | — | — |
| Process memory (avg) | 55.54 MB | — | — |

### +500ms fault rerun (new buckets) vs. Phase 6

| Metric | Phase 6 (old buckets) | Phase 7 fault rerun (new buckets) | Client-side (for comparison) |
|---|---|---|---|
| Request throughput | 6.888 req/s | — | 7.17 req/s |
| Latency p50 | 653.75 ms | — | 693.36 ms |
| Latency p95 | 933.15 ms | — | 801.20 ms |
| Latency p99 | 986.63 ms | — | 816.58 ms |
| Error rate | 0.00% | — | 0.00% |
| Process CPU (avg) | 0.0183 cores | — | n/a |
| Process memory (avg) | 56.04 MB | — | n/a |

### Did the fix work?

*(To be filled in once the official reruns land - the specific check is
whether the new Prometheus p95/p99 gap against the client-side numbers in
that same rerun has shrunk to roughly the 20-30ms range, down from
Phase 6's 132ms/170ms gap.)*

## Rehearsal (non-Docker substitute, this development sandbox)

Before asking for the official reruns, the fix was rehearsed end-to-end
in this sandbox against a directly-run `uvicorn` process and a real
Prometheus v2.55.1 binary (fetched from GitHub releases, since Docker Hub
is blocked here) - the same substitute pattern used in every prior
Docker-dependent phase. **This is not the official result and is reported
separately, not merged into the tables above.**

Ran `scripts/run_experiment.sh` for real (not synthetic) with
`INFERENCE_LAB_MOCK_EXTRA_LATENCY_MS=500` active, at a reduced scale (300
requests instead of 1000, to keep the rehearsal quick - same prompt,
`max_tokens`, and concurrency):

| Metric | Client-side | Prometheus (new buckets) | Gap |
|---|---|---|---|
| Latency p50 | 691.10 ms | 685.71 ms | 5.39 ms |
| Latency p95 | 778.51 ms | 785.58 ms | **7.07 ms** |
| Latency p99 | 798.78 ms | 797.12 ms | **1.66 ms** |

Compare to Phase 6's real gaps of **132ms (p95)** and **170ms (p99)**.
This is a real, measured (not synthetic) confirmation that the fix works
as diagnosed - the official 1000-request Docker Compose reruns are still
needed to make this the recorded result, per the approved plan.

## Validation

- `tests/test_metrics.py`: two new regression tests (see "Regression
  test" above), plus the full existing suite - 29 passed, 2 skipped (no
  local GPU) - unaffected by the bucket change. Ruff clean.
- `monitoring/prometheus.yml` re-validated with `promtool check config`
  (unchanged by this phase).
- `docker-compose.yml` re-validated with `docker compose config`
  (unchanged by this phase).
- Real (non-synthetic) rehearsal against a directly-run app + real
  Prometheus, described above.

## Compatibility with Phase 5/6 reports

`phase5_healthy_baseline.md` and `phase6_latency_fault.md` are unchanged
by this phase - their recorded numbers remain the historical record of
real runs measured under the old buckets. Because bucket boundaries
changed, the raw historical `_bucket` time series from those runs aren't
directly comparable to post-fix `_bucket` series at the metric level -
but the client-side latency numbers and derived percentiles in both
reports remain valid ground truth regardless of bucket configuration,
which is exactly what makes verifying this fix against them meaningful.
Going forward, Phase 8+ experiments should be compared against this
phase's rerun numbers (measured under the new buckets), not directly
against Phase 5/6's original numbers.
