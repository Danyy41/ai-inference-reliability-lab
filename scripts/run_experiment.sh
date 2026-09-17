#!/usr/bin/env bash
# Runs a reproducible load-test experiment against the running service and
# captures the resulting Prometheus metrics for the exact workload window.
#
# This is the reusable entry point for every experiment in this project -
# the Phase 5 healthy baseline today, and every later failure-injection
# experiment (same script, same measurement method; only the workload
# parameters or whatever failure is injected around it changes).
#
# It brackets the load test with pre/post "settle" buffers so Prometheus
# has real scrape samples immediately before and after the workload, not
# just the exact request timestamps - see
# experiments/phase5_healthy_baseline.md for why this matters.
#
# Usage:
#   scripts/run_experiment.sh \
#     --requests 1000 --concurrency 5 \
#     --prompt "Tell me about reliability engineering." --max-tokens 64 \
#     --url http://localhost:8000 --prometheus-url http://localhost:9090 \
#     --backend mock \
#     --out experiments/phase5_healthy_baseline_metrics.json
#
# --requests and --duration-seconds are mutually exclusive load-generation
# modes (see scripts/load_test.py): --duration-seconds runs a fixed-duration
# sweep (Phase 8+) instead of a fixed request count. Neither flag given
# preserves the historical default (1000 requests), so every Phase 5-7
# command above keeps working unchanged.
set -euo pipefail

REQUESTS=""
REQUESTS_EXPLICIT=0
DURATION_SECONDS=""
CONCURRENCY=5
PROMPT="Tell me about reliability engineering."
MAX_TOKENS=64
URL="http://localhost:8000"
PROMETHEUS_URL="http://localhost:9090"
BACKEND="mock"
PRE_BUFFER_SECONDS=6
POST_BUFFER_SECONDS=6
OUT=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --requests) REQUESTS="$2"; REQUESTS_EXPLICIT=1; shift 2 ;;
    --duration-seconds) DURATION_SECONDS="$2"; shift 2 ;;
    --concurrency) CONCURRENCY="$2"; shift 2 ;;
    --prompt) PROMPT="$2"; shift 2 ;;
    --max-tokens) MAX_TOKENS="$2"; shift 2 ;;
    --url) URL="$2"; shift 2 ;;
    --prometheus-url) PROMETHEUS_URL="$2"; shift 2 ;;
    --backend) BACKEND="$2"; shift 2 ;;
    --pre-buffer-seconds) PRE_BUFFER_SECONDS="$2"; shift 2 ;;
    --post-buffer-seconds) POST_BUFFER_SECONDS="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "$REQUESTS_EXPLICIT" -eq 1 && -n "$DURATION_SECONDS" ]]; then
  echo "Error: --requests and --duration-seconds are mutually exclusive - specify only one." >&2
  exit 1
fi
if [[ -z "$DURATION_SECONDS" && -z "$REQUESTS" ]]; then
  REQUESTS=1000  # historical default, preserved when neither flag is given
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Pre-workload settle (${PRE_BUFFER_SECONDS}s) so Prometheus has a fresh at-rest scrape ==="
sleep "$PRE_BUFFER_SECONDS"

# %s.%N for sub-second precision - whole-second timestamps here would
# introduce up to ~1s of error into the request-throughput/CPU/token
# calculations, which matters for a workload lasting only tens of seconds.
START_TS=$(date +%s.%N)
if [[ -n "$DURATION_SECONDS" ]]; then
  echo "=== Running load test: ${DURATION_SECONDS}s duration, concurrency ${CONCURRENCY}, backend=${BACKEND} ==="
  LOAD_ARGS=(--duration-seconds "$DURATION_SECONDS")
else
  echo "=== Running load test: ${REQUESTS} requests, concurrency ${CONCURRENCY}, backend=${BACKEND} ==="
  LOAD_ARGS=(--requests "$REQUESTS")
fi
python3 "$SCRIPT_DIR/load_test.py" \
  --url "$URL" \
  "${LOAD_ARGS[@]}" \
  --concurrency "$CONCURRENCY" \
  --prompt "$PROMPT" \
  --max-tokens "$MAX_TOKENS"

END_TS=$(date +%s.%N)
echo "=== Post-workload settle (${POST_BUFFER_SECONDS}s) so Prometheus scrapes the final state ==="
sleep "$POST_BUFFER_SECONDS"
EVAL_TS=$(date +%s.%N)

echo "=== Capturing metrics from Prometheus for window [${START_TS}, ${END_TS}] (eval at ${EVAL_TS}) ==="
CAPTURE_ARGS=(
  --prometheus-url "$PROMETHEUS_URL"
  --backend "$BACKEND"
  --start-ts "$START_TS"
  --end-ts "$END_TS"
  --eval-ts "$EVAL_TS"
  --range-pad-seconds "$PRE_BUFFER_SECONDS"
)
if [[ -n "$OUT" ]]; then
  CAPTURE_ARGS+=(--out "$OUT")
fi

python3 "$SCRIPT_DIR/capture_prometheus_metrics.py" "${CAPTURE_ARGS[@]}"
