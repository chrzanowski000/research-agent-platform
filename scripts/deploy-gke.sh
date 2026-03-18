#!/usr/bin/env bash
# scripts/deploy-gke.sh
# Deploys to GKE using Kustomize. Images must already exist in Artifact Registry.
# Usage: GCP_PROJECT=my-project GCP_REGION=europe-west1 GKE_CLUSTER=my-cluster IMAGE_TAG=abc123 [AUTOPILOT=1] ./scripts/deploy-gke.sh
# Set AUTOPILOT=1 to create an Autopilot cluster if it doesn't exist (recommended — scales to zero, no idle node costs).
# Without AUTOPILOT=1, a Standard cluster is created (1 node, e2-standard-2, 50 GB disk).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${GCP_PROJECT:?ERROR: GCP_PROJECT must be set}"
: "${GCP_REGION:?ERROR: GCP_REGION must be set}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
AUTOPILOT="${AUTOPILOT:-0}"

export GCP_PROJECT GCP_REGION IMAGE_TAG

echo "==> Deploying to GKE (Kustomize)"
echo "    Project  : ${GCP_PROJECT}"
echo "    Region   : ${GCP_REGION}"
echo "    Tag      : ${IMAGE_TAG}"
echo "    Autopilot: ${AUTOPILOT}"
echo ""

# Optionally create + fetch cluster credentials if GKE_CLUSTER is provided
if [[ -n "${GKE_CLUSTER:-}" ]]; then
  if ! gcloud container clusters describe "${GKE_CLUSTER}" \
      --region "${GCP_REGION}" --project "${GCP_PROJECT}" &>/dev/null; then
    if [[ "${AUTOPILOT}" == "1" ]]; then
      echo "==> Creating Autopilot cluster '${GKE_CLUSTER}'..."
      gcloud container clusters create-auto "${GKE_CLUSTER}" \
        --region "${GCP_REGION}" --project "${GCP_PROJECT}"
    else
      echo "==> Creating Standard cluster '${GKE_CLUSTER}' (1 node, e2-standard-2, 50 GB)..."
      gcloud container clusters create "${GKE_CLUSTER}" \
        --region "${GCP_REGION}" --project "${GCP_PROJECT}" \
        --num-nodes 1 --machine-type e2-standard-2 --disk-size 50
    fi
    echo ""
  fi
  echo "==> Fetching GKE credentials for cluster '${GKE_CLUSTER}'..."
  gcloud container clusters get-credentials "${GKE_CLUSTER}" \
    --region "${GCP_REGION}" --project "${GCP_PROJECT}"
  echo ""
fi

# Use envsubst to render the gke kustomization.yaml with real values,
# then apply. Mirrors the deploy-eks.sh approach.
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

cp -r "${REPO_ROOT}/infrastructure/k8s/"* "${TMP_DIR}/"

envsubst < "${REPO_ROOT}/infrastructure/k8s/gke/kustomization.yaml" \
         > "${TMP_DIR}/gke/kustomization.yaml"

echo "==> Applying manifests..."
kubectl apply -k "${TMP_DIR}/gke"

echo ""
echo "==> Waiting for rollout..."
kubectl rollout status deployment/chat-ui --timeout=300s
kubectl rollout status deployment/langgraph-api --timeout=300s
kubectl rollout status deployment/persistence-api --timeout=300s
kubectl rollout status statefulset/postgres --timeout=300s

echo ""
echo "==> GKE deployment complete!"
echo "    Run: kubectl get pods"
