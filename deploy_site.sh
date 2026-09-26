#!/usr/bin/env bash
set -euo pipefail

# Publish the Hyperion Events site (hyperion-events site) to a public GCS bucket.
#
# The bucket holds the site and nothing else: it is readable by anyone on the
# internet, so never point SITE_BUCKET at a bucket with anything private in it.
# Run `hyperion-events ingest` first if you want fresh data; this only rebuilds
# the pages from the database and uploads them.

PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${SITE_REGION:-asia-south1}"
BUCKET_NAME="${SITE_BUCKET:-hyperion-events-site-${PROJECT_ID}}"
OUT_DIR="${SITE_OUT:-site}"
DB_PATH="${HYPERION_DB:-data/hyperion.db}"

if [ -z "$PROJECT_ID" ]; then
    echo "[-] Error: GCP Project ID could not be determined. Set GCP_PROJECT_ID or run 'gcloud config set project <PROJECT_ID>'."
    exit 1
fi
if [ ! -f "$DB_PATH" ]; then
    echo "[-] Error: no database at ${DB_PATH}. Run 'hyperion-events ingest' first."
    exit 1
fi

echo "================================================================================"
echo "  HYPERION EVENTS: PUBLISHING THE PUBLIC SITE"
echo "================================================================================"
echo "  Project: ${PROJECT_ID}"
echo "  Bucket:  gs://${BUCKET_NAME} (public, ${REGION})"
echo "================================================================================"

echo "[1/4] Building the site from ${DB_PATH}..."
PYTHONPATH="$(dirname "$0")/python${PYTHONPATH:+:$PYTHONPATH}" \
    python3 -m event_study.cli --db "$DB_PATH" site --out "$OUT_DIR"

echo "[2/4] Ensuring the bucket exists..."
if ! gcloud storage buckets describe "gs://${BUCKET_NAME}" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${BUCKET_NAME}" \
        --project "$PROJECT_ID" \
        --location "$REGION" \
        --uniform-bucket-level-access \
        --no-public-access-prevention
fi
gcloud storage buckets update "gs://${BUCKET_NAME}" --web-main-page-suffix=index.html --quiet

echo "[3/4] Allowing public read of the site bucket..."
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET_NAME}" \
    --member=allUsers --role=roles/storage.objectViewer --quiet >/dev/null

echo "[4/4] Uploading (files gone from the site are removed from the bucket)..."
gcloud storage rsync "$OUT_DIR" "gs://${BUCKET_NAME}" \
    --recursive --delete-unmatched-destination-objects \
    --cache-control="public, max-age=300"

echo "================================================================================"
echo "  Live at: https://storage.googleapis.com/${BUCKET_NAME}/index.html"
echo "================================================================================"
