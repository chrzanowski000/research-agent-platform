#!/usr/bin/env bash
# scripts/deploy-gke.sh
# Deploys to GKE using Kustomize. Images must already exist in Artifact Registry.
# Usage: GCP_PROJECT=my-project GCP_REGION=europe-west1 GKE_CLUSTER=my-cluster IMAGE_TAG=abc123 ./scripts/deploy-gke.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${GCP_PROJECT:?ERROR: GCP_PROJECT must be set}"
: "${GCP_REGION:?ERROR: GCP_REGION must be set}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

export GCP_PROJECT GCP_REGION IMAGE_TAG

echo "==> Deploying to GKE (Kustomize)"
echo "    Project : ${GCP_PROJECT}"
echo "    Region  : ${GCP_REGION}"
echo "    Tag     : ${IMAGE_TAG}"
echo ""

# Optionally fetch cluster credentials if GKE_CLUSTER is provided
if [[ -n "${GKE_CLUSTER:-}" ]]; then
  GKE_ZONE="${GKE_ZONE:-${GCP_REGION}}"
  echo "==> Fetching GKE credentials for cluster '${GKE_CLUSTER}'..."
  gcloud container clusters get-credentials "${GKE_CLUSTER}" \
    --region "${GKE_ZONE}" \
    --project "${GCP_PROJECT}"
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
