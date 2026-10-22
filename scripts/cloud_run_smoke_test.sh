#!/usr/bin/env bash
# Proves a deployed Cloud Run service (or any running instance) actually
# serves traffic correctly, and that it is running the mock backend with no
# GPU - the cloud equivalent of scripts/docker_smoke_test.sh.
#
# Usage:
#   scripts/cloud_run_smoke_test.sh https://inference-lab-demo-xxxxx-uc.a.run.app
set -euo pipefail

BASE_URL="${1:?Usage: $0 <service-url>}"
BASE_URL="${BASE_URL%/}"

echo "=== GET /health ==="
health_response="$(curl -sS -f --max-time 30 "${BASE_URL}/health")"
echo "$health_response"
echo "$health_response" | grep -q '"status":"ok"' || {
  echo "Unexpected /health response"
  exit 1
}

echo "=== POST /generate ==="
generate_response="$(curl -sS -f --max-time 30 -X POST "${BASE_URL}/generate" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Cloud Run smoke test prompt", "max_tokens": 16}')"
echo "$generate_response"
echo "$generate_response" | grep -q '"text":"\[mock completion' || {
  echo "Unexpected /generate response (expected a mock completion)"
  exit 1
}
echo "$generate_response" | grep -q '"gpu_memory_allocated_mb":null' || {
  echo "Unexpected /generate response (expected no GPU memory stats)"
  exit 1
}

echo "=== GET /metrics (backend info) ==="
backend_info="$(curl -sS -f --max-time 30 "${BASE_URL}/metrics" | grep '^inference_backend_info')"
echo "$backend_info"
echo "$backend_info" | grep -q 'backend="mock",device="n/a",model="n/a"' || {
  echo "Unexpected backend info (expected mock backend, no device, no model)"
  exit 1
}

echo "=== Smoke test passed: ${BASE_URL} (mock backend, no GPU, no model) ==="
