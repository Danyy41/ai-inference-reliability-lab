#!/usr/bin/env bash
# Deploys the mock-backend demo of the inference API to Google Cloud Run.
#
# The deployed service is deliberately cheap and bounded:
#   - mock backend only (INFERENCE_LAB_BACKEND=mock): no GPU, no model
#     download, no paid LLM API - see cloudbuild.yaml for the slim image
#   - scales to zero (min instances 0) and is capped at MAX_INSTANCES (2)
#   - request-based billing (CPU only allocated while serving a request)
#   - 1 vCPU / 512 MiB, 30s request timeout
#
# Prerequisites: gcloud installed and logged in (`gcloud auth login`), and a
# Google Cloud project with billing enabled. Nothing is created until you
# confirm the printed plan.
#
# Usage:
#   GCP_PROJECT_ID=my-project scripts/deploy_cloud_run.sh            # asks to confirm
#   GCP_PROJECT_ID=my-project scripts/deploy_cloud_run.sh --dry-run  # print commands only
#   GCP_PROJECT_ID=my-project scripts/deploy_cloud_run.sh --yes      # no prompt
#
# Optional overrides: GCP_REGION (us-central1), SERVICE_NAME
# (inference-lab-demo), AR_REPOSITORY (inference-lab), MAX_INSTANCES (2),
# IMAGE_TAG (current git short SHA).
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID to your Google Cloud project ID}"
REGION="${GCP_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-inference-lab-demo}"
AR_REPOSITORY="${AR_REPOSITORY:-inference-lab}"
MAX_INSTANCES="${MAX_INSTANCES:-2}"
IMAGE_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPOSITORY}/inference-lab:${IMAGE_TAG}"

DRY_RUN=false
ASSUME_YES=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --yes) ASSUME_YES=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

run() {
  echo "+ $*"
  if [ "$DRY_RUN" = "false" ]; then
    "$@"
  fi
}

cd "$(dirname "$0")/.."

if [ "$DRY_RUN" = "false" ]; then
  command -v gcloud >/dev/null || { echo "gcloud CLI not found" >&2; exit 1; }
  account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)"
  [ -n "$account" ] || { echo "No active gcloud account - run: gcloud auth login" >&2; exit 1; }
else
  account="(dry run)"
fi

cat <<EOF
=== Cloud Run deployment plan ===
  Account:        ${account}
  Project:        ${PROJECT_ID}
  Region:         ${REGION}
  Service:        ${SERVICE_NAME} (public, unauthenticated)
  Image:          ${IMAGE}
  Backend:        mock (no GPU, no model, no paid LLM API)
  Resources:      1 vCPU, 512Mi, concurrency 80, timeout 30s
  Scaling:        min 0, max ${MAX_INSTANCES} instances, request-based billing
EOF

if [ "$DRY_RUN" = "false" ] && [ "$ASSUME_YES" = "false" ]; then
  read -r -p "Type 'deploy' to continue: " answer
  [ "$answer" = "deploy" ] || { echo "Aborted."; exit 1; }
fi

echo "=== Enabling required APIs ==="
run gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  cloudbuild.googleapis.com --project "$PROJECT_ID"

echo "=== Ensuring Artifact Registry repository exists ==="
if [ "$DRY_RUN" = "true" ] || ! gcloud artifacts repositories describe "$AR_REPOSITORY" \
  --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
  run gcloud artifacts repositories create "$AR_REPOSITORY" \
    --repository-format docker --location "$REGION" --project "$PROJECT_ID" \
    --description "AI Inference Reliability Lab images"
fi

echo "=== Building image with Cloud Build ==="
run gcloud builds submit --project "$PROJECT_ID" --region "$REGION" \
  --config cloudbuild.yaml --substitutions "_IMAGE=${IMAGE}" .

echo "=== Deploying to Cloud Run ==="
run gcloud run deploy "$SERVICE_NAME" \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --image "$IMAGE" \
  --allow-unauthenticated \
  --port 8080 \
  --cpu 1 \
  --memory 512Mi \
  --concurrency 80 \
  --timeout 30 \
  --min-instances 0 \
  --max-instances "$MAX_INSTANCES" \
  --cpu-throttling \
  --set-env-vars INFERENCE_LAB_BACKEND=mock,INFERENCE_LAB_LOG_LEVEL=INFO \
  --labels app=inference-lab,env=demo

if [ "$DRY_RUN" = "true" ]; then
  echo "=== Dry run complete - nothing was created ==="
  exit 0
fi

SERVICE_URL="$(gcloud run services describe "$SERVICE_NAME" --project "$PROJECT_ID" \
  --region "$REGION" --format 'value(status.url)')"
echo "=== Deployed: ${SERVICE_URL} ==="

scripts/live_smoke_test.sh "$SERVICE_URL"
