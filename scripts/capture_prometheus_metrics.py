#!/usr/bin/env python3
"""Capture a reproducible metrics snapshot for one load-test run, from Prometheus.

Given the exact [start_ts, end_ts] wall-clock window of a load-test run (plus
an eval_ts taken after a post-workload settle period), this queries a running
Prometheus for: request throughput, latency p50/p95/p99, error rate, token
throughput, process CPU, process memory, and the active backend.

This is normally invoked by scripts/run_experiment.sh, which handles the
pre/post settle buffers and timestamp bookkeeping - see that script and
experiments/phase5_healthy_baseline.md for the full methodology and why
each metric is computed the way it is below.

Usage:
    python scripts/capture_prometheus_metrics.py \
        --prometheus-url http://localhost:9090 \
        --backend mock \
        --start-ts 1700000000 --end-ts 1700000042 --eval-ts 1700000048 \
        --range-pad-seconds 6 \
        --out experiments/phase5_healthy_baseline_metrics.json
"""

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass

import httpx


def instant_query(prometheus_url: str, query: str, time_ts: float) -> list[dict]:
    """Run a Prometheus instant query at a specific timestamp.

    An instant query at time T returns, for each matching series, the most
    recent sample at or before T (within Prometheus's default 5m staleness
    lookback) - it does not require a scrape to have landed at exactly T.
    """
    response = httpx.get(
        f"{prometheus_url}/api/v1/query",
        params={"query": query, "time": time_ts},
        timeout=10.0,
    )
    response.raise_for_status()
    data = response.json()
    if data["status"] != "success":
        raise RuntimeError(f"Prometheus query failed: {query!r}: {data}")
    return data["data"]["result"]


def single_value(results: list[dict], query: str) -> float:
    if not results:
        raise RuntimeError(f"No data returned for query: {query!r}")
    if len(results) > 1:
        raise RuntimeError(f"Expected a single series for query: {query!r}, got {len(results)}")
    return float(results[0]["value"][1])


@dataclass
class BaselineMetrics:
    window_seconds: float
    total_requests: int
    total_errors: int
    request_throughput_rps: float
    error_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    total_completion_tokens: int
    token_throughput_tps: float
    process_cpu_avg_cores: float
    process_memory_max_bytes: float
    process_memory_avg_bytes: float
    active_backend: str
    active_device: str
    active_model: str


def _counters_at(prometheus_url: str, backend: str, ts: float) -> dict[str, float]:
    """Snapshot the raw counters this run cares about at one instant."""
    requests_by_status = {
        r["metric"].get("status", "unknown"): float(r["value"][1])
        for r in instant_query(
            prometheus_url, f'inference_requests_total{{backend="{backend}"}}', ts
        )
    }
    tokens = instant_query(
        prometheus_url, f'inference_completion_tokens_total{{backend="{backend}"}}', ts
    )
    cpu = instant_query(prometheus_url, "process_cpu_seconds_total", ts)
    return {
        "success": requests_by_status.get("success", 0.0),
        "error": requests_by_status.get("error", 0.0),
        "tokens": float(tokens[0]["value"][1]) if tokens else 0.0,
        "cpu_seconds": float(cpu[0]["value"][1]) if cpu else 0.0,
    }


def capture(
    prometheus_url: str,
    backend: str,
    start_ts: float,
    end_ts: float,
    eval_ts: float,
    range_pad_seconds: float,
) -> BaselineMetrics:
    """Capture the baseline metric set for the workload window [start_ts, end_ts].

    Two different techniques are used deliberately, not one uniform approach:

    - Counters that feed a *rate over the exact workload duration* (request
      throughput, error rate, token throughput, process CPU utilization) are
      captured as an instant-query snapshot diff at precisely start_ts and
      eval_ts, divided by the exact (end_ts - start_ts) - NOT by a padded
      range. Padding the range here would attribute idle-period CPU/requests
      before or after the burst to the workload, inflating the numbers.
    - The latency histogram and the memory gauge instead use a *padded*
      range query ([start_ts - range_pad_seconds, eval_ts]), evaluated at
      eval_ts. Padding doesn't bias these: idle time contributes zero
      histogram observations either way, and padding the memory window only
      helps make sure a real scrape sample near the edges isn't missed.
    """
    window_seconds = end_ts - start_ts
    if window_seconds <= 0:
        raise ValueError("end_ts must be after start_ts")

    before = _counters_at(prometheus_url, backend, start_ts)
    after = _counters_at(prometheus_url, backend, eval_ts)

    total_success = after["success"] - before["success"]
    total_error = after["error"] - before["error"]
    total_requests = total_success + total_error
    total_tokens = after["tokens"] - before["tokens"]
    cpu_delta = after["cpu_seconds"] - before["cpu_seconds"]

    range_seconds = max(1, math.ceil((eval_ts - start_ts) + range_pad_seconds))

    def quantile_ms(p: float) -> float:
        query = (
            f"histogram_quantile({p}, sum(rate("
            f'inference_generation_latency_seconds_bucket{{backend="{backend}"}}'
            f"[{range_seconds}s])) by (le))"
        )
        results = instant_query(prometheus_url, query, eval_ts)
        if not results or results[0]["value"][1] == "NaN":
            return float("nan")
        return float(results[0]["value"][1]) * 1000

    mem_max_query = f"max_over_time(process_resident_memory_bytes[{range_seconds}s])"
    mem_avg_query = f"avg_over_time(process_resident_memory_bytes[{range_seconds}s])"

    backend_info_query = f'inference_backend_info{{backend="{backend}"}}'
    info_results = instant_query(prometheus_url, backend_info_query, eval_ts)
    if not info_results:
        raise RuntimeError(
            "No inference_backend_info sample found - is the app running and "
            "being scraped by Prometheus?"
        )
    labels = info_results[0]["metric"]

    return BaselineMetrics(
        window_seconds=window_seconds,
        total_requests=round(total_requests),
        total_errors=round(total_error),
        request_throughput_rps=total_requests / window_seconds,
        error_rate=(total_error / total_requests) if total_requests > 0 else 0.0,
        p50_latency_ms=quantile_ms(0.50),
        p95_latency_ms=quantile_ms(0.95),
        p99_latency_ms=quantile_ms(0.99),
        total_completion_tokens=round(total_tokens),
        token_throughput_tps=total_tokens / window_seconds,
        process_cpu_avg_cores=cpu_delta / window_seconds,
        process_memory_max_bytes=single_value(
            instant_query(prometheus_url, mem_max_query, eval_ts), mem_max_query
        ),
        process_memory_avg_bytes=single_value(
            instant_query(prometheus_url, mem_avg_query, eval_ts), mem_avg_query
        ),
        active_backend=labels.get("backend", "unknown"),
        active_device=labels.get("device", "unknown"),
        active_model=labels.get("model", "unknown"),
    )


def print_report(metrics: BaselineMetrics) -> None:
    print("=== Prometheus-captured metrics ===")
    print(f"Window:                 {metrics.window_seconds:.2f} s")
    print(f"Total requests:         {metrics.total_requests}")
    print(f"Total errors:           {metrics.total_errors}")
    print(f"Request throughput:     {metrics.request_throughput_rps:.3f} req/s")
    print(f"Error rate:             {metrics.error_rate:.2%}")
    print(f"Latency p50:            {metrics.p50_latency_ms:.2f} ms")
    print(f"Latency p95:            {metrics.p95_latency_ms:.2f} ms")
    print(f"Latency p99:            {metrics.p99_latency_ms:.2f} ms")
    print(f"Total completion tokens:{metrics.total_completion_tokens}")
    print(f"Token throughput:       {metrics.token_throughput_tps:.3f} tok/s")
    print(f"Process CPU (avg):      {metrics.process_cpu_avg_cores:.4f} cores")
    print(f"Process memory (max):   {metrics.process_memory_max_bytes / (1024 * 1024):.2f} MB")
    print(f"Process memory (avg):   {metrics.process_memory_avg_bytes / (1024 * 1024):.2f} MB")
    print(
        f"Active backend:         backend={metrics.active_backend} "
        f"device={metrics.active_device} model={metrics.active_model}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--backend", default="mock")
    parser.add_argument("--start-ts", type=float, required=True, help="Workload start (unix ts)")
    parser.add_argument("--end-ts", type=float, required=True, help="Workload end (unix ts)")
    parser.add_argument(
        "--eval-ts",
        type=float,
        required=True,
        help="When to evaluate the 'after' snapshot (unix ts, should be after "
        "end-ts plus a post-workload settle buffer)",
    )
    parser.add_argument(
        "--range-pad-seconds",
        type=float,
        default=6.0,
        help="Extra margin added before start-ts for range queries (latency "
        "percentiles, memory) so edge samples aren't missed",
    )
    parser.add_argument("--out", help="Optional path to write the metrics as JSON")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        metrics = capture(
            prometheus_url=args.prometheus_url,
            backend=args.backend,
            start_ts=args.start_ts,
            end_ts=args.end_ts,
            eval_ts=args.eval_ts,
            range_pad_seconds=args.range_pad_seconds,
        )
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        print(f"Failed to capture metrics: {exc}", file=sys.stderr)
        sys.exit(1)

    print_report(metrics)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(asdict(metrics), f, indent=2)
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
