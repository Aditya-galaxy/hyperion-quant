#!/usr/bin/env bash
set -e

# Dynamically resolve GCP Project ID from environment or active gcloud config
PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-us-central1}"
REPO_NAME="hyperion-quant"
IMAGE_NAME="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/model-trainer:latest"
JOB_NAME="hyperion-model-retrainer"
BUCKET_NAME="${GCS_BUCKET:-hyperion-quant-${PROJECT_ID}}"

if [ -z "$PROJECT_ID" ]; then
    echo "[-] Error: GCP Project ID could not be determined. Set GCP_PROJECT_ID or run 'gcloud config set project <PROJECT_ID>'."
    exit 1
fi

echo "================================================================================"
echo "  HYPERION QUANT: DEPLOYING CLOUD RUN TRAINING JOB (SERVERLESS ML TRAINING)"
echo "================================================================================"
echo "  Project: ${PROJECT_ID}"
echo "  Region:  ${REGION}"
echo "  Bucket:  ${BUCKET_NAME}"
echo "================================================================================"

echo "[1/4] Ensuring Artifact Registry repository exists..."
gcloud artifacts repositories create "${REPO_NAME}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Hyperion Quant Container Registry" \
    --quiet || true

echo "[2/4] Building and pushing training container via Google Cloud Build..."
gcloud builds submit \
    --config=cloudbuild.yaml \
    --substitutions=_IMAGE_TAG="${IMAGE_NAME}" .

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
