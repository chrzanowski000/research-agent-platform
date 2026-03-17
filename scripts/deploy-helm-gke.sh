#!/usr/bin/env bash
# scripts/deploy-helm-gke.sh
# Deploys to GKE using Helm. Images must already exist in Artifact Registry.
# Usage: GCP_PROJECT=my-project GCP_REGION=europe-west1 GKE_CLUSTER=my-cluster IMAGE_TAG=abc123 ./scripts/deploy-helm-gke.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${GCP_PROJECT:?ERROR: GCP_PROJECT must be set}"
: "${GCP_REGION:?ERROR: GCP_REGION must be set}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
HELM_RELEASE="${HELM_RELEASE:-agents}"
HELM_NAMESPACE="${HELM_NAMESPACE:-agents}"

export GCP_PROJECT GCP_REGION IMAGE_TAG

CHART_DIR="${REPO_ROOT}/infrastructure/helm/research-agent-platform"

echo "==> Deploying to GKE (Helm)"
echo "    Project   : ${GCP_PROJECT}"
echo "    Region    : ${GCP_REGION}"
echo "    Tag       : ${IMAGE_TAG}"
echo "    Release   : ${HELM_RELEASE}"
echo "    Namespace : ${HELM_NAMESPACE}"
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

# Render values-gke.yaml with real image URIs via envsubst
TMP_VALUES=$(mktemp --suffix=.yaml)
trap 'rm -f "$TMP_VALUES"' EXIT

envsubst < "${CHART_DIR}/values-gke.yaml" > "${TMP_VALUES}"

echo "==> Running helm upgrade --install..."
helm upgrade --install "${HELM_RELEASE}" "${CHART_DIR}" \
  --namespace "${HELM_NAMESPACE}" \
  --create-namespace \
  -f "${CHART_DIR}/values.yaml" \
  -f "${TMP_VALUES}"

echo ""
echo "==> Waiting for rollout..."
kubectl rollout status deployment/chat-ui -n "${HELM_NAMESPACE}" --timeout=300s
kubectl rollout status deployment/langgraph-api -n "${HELM_NAMESPACE}" --timeout=300s
kubectl rollout status deployment/persistence-api -n "${HELM_NAMESPACE}" --timeout=300s
kubectl rollout status statefulset/postgres -n "${HELM_NAMESPACE}" --timeout=300s

echo ""
echo "==> GKE Helm deployment complete!"
echo "    Run: kubectl get pods -n ${HELM_NAMESPACE}"
