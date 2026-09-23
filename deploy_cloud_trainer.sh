#!/usr/bin/env bash
set -e

PROJECT_ID="kronagent"
REGION="us-central1"
REPO_NAME="hyperion-quant"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/model-trainer:latest"
JOB_NAME="hyperion-model-retrainer"
BUCKET_NAME="hyperion-quant-kronagent"

echo "================================================================================"
echo "  HYPERION QUANT: DEPLOYING CLOUD RUN TRAINING JOB (SERVERLESS ML TRAINING)"
echo "================================================================================"

echo "[1/4] Ensuring Artifact Registry repository exists..."
gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Hyperion Quant Container Registry" \
    --quiet || true

echo "[2/4] Building and pushing training container via Google Cloud Build..."
gcloud builds submit \
    --tag "${IMAGE_NAME}" \
    -f Dockerfile.trainer .

echo "[3/4] Creating / Updating Cloud Run Job: ${JOB_NAME}..."
gcloud run jobs deploy "${JOB_NAME}" \
    --image "${IMAGE_NAME}" \
    --region "${REGION}" \
    --tasks 1 \
    --memory 4Gi \
    --cpu 2 \
    --set-env-vars "GCS_BUCKET=${BUCKET_NAME}" \
    --quiet

echo "[4/4] Cloud Run Job successfully configured!"
echo "  To trigger training on-demand:"
echo "    gcloud run jobs execute ${JOB_NAME} --region ${REGION}"
echo "================================================================================"
