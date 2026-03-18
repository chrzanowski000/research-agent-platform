#!/usr/bin/env bash
# scripts/build-and-push-gke.sh
# Builds the 3 service images and pushes them to Google Artifact Registry.
# Usage: GCP_PROJECT=my-project GCP_REGION=europe-west1 IMAGE_TAG=abc123 ./scripts/build-and-push-gke.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${GCP_PROJECT:?ERROR: GCP_PROJECT must be set}"
: "${GCP_REGION:?ERROR: GCP_REGION must be set}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

REGISTRY="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/agents"

echo "==> Building and pushing images to Artifact Registry"
echo "    Project  : ${GCP_PROJECT}"
echo "    Region   : ${GCP_REGION}"
echo "    Registry : ${REGISTRY}"
echo "    Tag      : ${IMAGE_TAG}"
echo ""

echo "==> Configuring Docker auth for Artifact Registry..."
gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet

echo "==> Ensuring Artifact Registry repository 'agents' exists..."
if ! gcloud artifacts repositories describe agents \
    --project="${GCP_PROJECT}" \
    --location="${GCP_REGION}" \
    --quiet 2>/dev/null; then
  echo "    Repository not found — creating..."
  gcloud artifacts repositories create agents \
    --repository-format=docker \
    --location="${GCP_REGION}" \
    --project="${GCP_PROJECT}" \
    --description="agents-self-reflect service images"
else
  echo "    Repository already exists."
fi
echo ""

echo "==> Building chat-ui..."
# NEXT_PUBLIC_API_URL is baked into the image at build time (Next.js requirement).
# Pass it explicitly if you know the external IP/domain, e.g.:
#   NEXT_PUBLIC_API_URL=http://34.77.x.x/api ./scripts/build-and-push-gke.sh
# If not set, defaults to /api (relative URL — works when UI is served from the same domain).
CHAT_UI_API_URL="${NEXT_PUBLIC_API_URL:-/api}"
docker build \
  --platform linux/amd64 \
  --build-arg NEXT_PUBLIC_API_URL="${CHAT_UI_API_URL}" \
  -f "${REPO_ROOT}/infrastructure/docker/chat-ui.Dockerfile" \
  -t "${REGISTRY}/chat-ui:${IMAGE_TAG}" \
  "${REPO_ROOT}/services/chat-ui/"

echo "==> Building langgraph-api..."
docker build \
  --platform linux/amd64 \
  -f "${REPO_ROOT}/infrastructure/docker/langgraph-api.Dockerfile" \
  -t "${REGISTRY}/langgraph-api:${IMAGE_TAG}" \
  "${REPO_ROOT}/services/langgraph-api/"

echo "==> Building persistence-api..."
docker build \
  --platform linux/amd64 \
  -f "${REPO_ROOT}/infrastructure/docker/persistence-api.Dockerfile" \
  -t "${REGISTRY}/persistence-api:${IMAGE_TAG}" \
  "${REPO_ROOT}/services/persistence-api/"

echo ""
echo "==> Pushing images..."
docker push "${REGISTRY}/chat-ui:${IMAGE_TAG}"
docker push "${REGISTRY}/langgraph-api:${IMAGE_TAG}"
docker push "${REGISTRY}/persistence-api:${IMAGE_TAG}"

echo ""
echo "==> Done. Images pushed:"
echo "    ${REGISTRY}/chat-ui:${IMAGE_TAG}"
echo "    ${REGISTRY}/langgraph-api:${IMAGE_TAG}"
echo "    ${REGISTRY}/persistence-api:${IMAGE_TAG}"
