#!/usr/bin/env bash
# Builds the Docker image, runs it, and proves the container actually
# serves traffic correctly before anyone trusts it in CI or production.
#
# Usage:
#   scripts/docker_smoke_test.sh            # mock backend (default, fast, no network needed)
#   scripts/docker_smoke_test.sh huggingface # Hugging Face CPU backend (downloads a model - needs network)
set -euo pipefail

BACKEND="${1:-mock}"
IMAGE_TAG="inference-lab:smoke-test"
CONTAINER_NAME="inference-lab-smoke-test"
HOST_PORT="8000"

cleanup() {
  docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== Building image ==="
docker build -t "$IMAGE_TAG" .

echo "=== Starting container (backend=$BACKEND) ==="
cleanup
docker run -d \
  --name "$CONTAINER_NAME" \
  -p "${HOST_PORT}:8000" \
  -e "INFERENCE_LAB_BACKEND=${BACKEND}" \
  "$IMAGE_TAG"

echo "=== Waiting for container health check to pass ==="
for _ in $(seq 1 30); do
  status="$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null || echo "starting")"
  if [ "$status" = "healthy" ]; then
    echo "Container is healthy."
    break
  fi
  if [ "$status" = "unhealthy" ]; then
    echo "Container reported unhealthy. Logs:"
    docker logs "$CONTAINER_NAME"
    exit 1
  fi
  sleep 2
done

if [ "$status" != "healthy" ]; then
  echo "Timed out waiting for healthy status (last status: $status). Logs:"
  docker logs "$CONTAINER_NAME"
  exit 1
fi

echo "=== GET /health ==="
health_response="$(curl -sS -f "http://localhost:${HOST_PORT}/health")"
echo "$health_response"
echo "$health_response" | grep -q '"status":"ok"' || {
  echo "Unexpected /health response"
  exit 1
}

echo "=== POST /generate ==="
generate_response="$(curl -sS -f -X POST "http://localhost:${HOST_PORT}/generate" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Docker smoke test prompt", "max_tokens": 16}')"
echo "$generate_response"
echo "$generate_response" | grep -q '"text"' || {
  echo "Unexpected /generate response"
  exit 1
}

echo "=== Smoke test passed (backend=$BACKEND) ==="
