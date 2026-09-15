#!/usr/bin/env python3
"""Simple concurrent load-testing script for the inference API.

Fires N requests at POST /generate with a configurable concurrency level and
reports latency percentiles and error rate. This is the benchmarking tool
used to measure the impact of intentionally-introduced failures and their
fixes in later versions of the lab.

Usage:
    python scripts/load_test.py --url http://localhost:8000 --requests 200 --concurrency 20
"""

import argparse
import asyncio
import statistics
import time
from dataclasses import dataclass, field

import httpx


@dataclass
class RequestOutcome:
    latency_ms: float
    status_code: int
    error: str | None = None


@dataclass
class LoadTestResult:
    outcomes: list[RequestOutcome] = field(default_factory=list)
    total_wall_time_s: float = 0.0

    @property
    def successes(self) -> list[RequestOutcome]:
        return [o for o in self.outcomes if o.error is None and o.status_code < 400]

    @property
    def failures(self) -> list[RequestOutcome]:
        return [o for o in self.outcomes if o.error is not None or o.status_code >= 400]

    def percentile(self, p: float) -> float:
        latencies = sorted(o.latency_ms for o in self.successes)
        if not latencies:
            return 0.0
        k = (len(latencies) - 1) * (p / 100)
        f, c = int(k), min(int(k) + 1, len(latencies) - 1)
        if f == c:
            return latencies[f]
        return latencies[f] + (latencies[c] - latencies[f]) * (k - f)


async def send_request(
    client: httpx.AsyncClient, url: str, prompt: str, max_tokens: int
) -> RequestOutcome:
    start = time.perf_counter()
    try:
        response = await client.post(
            f"{url}/generate", json={"prompt": prompt, "max_tokens": max_tokens}
        )
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestOutcome(latency_ms=latency_ms, status_code=response.status_code)
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestOutcome(latency_ms=latency_ms, status_code=0, error=str(exc))


async def run_load_test(
    url: str, num_requests: int, concurrency: int, prompt: str, max_tokens: int
) -> LoadTestResult:
    result = LoadTestResult()
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_send(client: httpx.AsyncClient) -> RequestOutcome:
        async with semaphore:
            return await send_request(client, url, prompt, max_tokens)

    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [bounded_send(client) for _ in range(num_requests)]
        result.outcomes = await asyncio.gather(*tasks)
    result.total_wall_time_s = time.perf_counter() - start

    return result


def print_report(result: LoadTestResult, num_requests: int) -> None:
    successes = result.successes
    failures = result.failures
    throughput = num_requests / result.total_wall_time_s if result.total_wall_time_s > 0 else 0.0

    print("=== Load Test Report ===")
    print(f"Total requests:   {num_requests}")
    print(f"Successes:        {len(successes)}")
    print(f"Failures:         {len(failures)}")
    print(f"Error rate:       {len(failures) / num_requests:.2%}")
    print(f"Wall time:        {result.total_wall_time_s:.2f}s")
    print(f"Throughput:       {throughput:.2f} req/s")

    if successes:
        latencies = [o.latency_ms for o in successes]
        print(f"Latency min:      {min(latencies):.2f} ms")
        print(f"Latency mean:     {statistics.mean(latencies):.2f} ms")
        print(f"Latency p50:      {result.percentile(50):.2f} ms")
        print(f"Latency p95:      {result.percentile(95):.2f} ms")
        print(f"Latency p99:      {result.percentile(99):.2f} ms")
        print(f"Latency max:      {max(latencies):.2f} ms")

    if failures:
        print("--- Sample failures ---")
        for outcome in failures[:5]:
            print(f"  status={outcome.status_code} error={outcome.error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of the API")
    parser.add_argument("--requests", type=int, default=100, help="Total number of requests")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent in-flight requests")
    parser.add_argument("--prompt", default="Tell me about reliability engineering.")
    parser.add_argument("--max-tokens", type=int, default=64)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(
        run_load_test(
            url=args.url,
            num_requests=args.requests,
            concurrency=args.concurrency,
            prompt=args.prompt,
            max_tokens=args.max_tokens,
        )
    )
    print_report(result, args.requests)


if __name__ == "__main__":
    main()
