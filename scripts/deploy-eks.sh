#!/usr/bin/env bash
# scripts/deploy-eks.sh
# Deploys to AWS EKS. Images must already exist in ECR.
# Usage: AWS_ACCOUNT_ID=123456789 AWS_REGION=us-east-1 IMAGE_TAG=abc123 ./scripts/deploy-eks.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

: "${AWS_ACCOUNT_ID:?ERROR: AWS_ACCOUNT_ID must be set}"
: "${AWS_REGION:?ERROR: AWS_REGION must be set}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

export AWS_ACCOUNT_ID AWS_REGION IMAGE_TAG

echo "==> Deploying to EKS"
echo "    Account : ${AWS_ACCOUNT_ID}"
echo "    Region  : ${AWS_REGION}"
echo "    Tag     : ${IMAGE_TAG}"
echo ""

# Strategy: render the full Kustomize output to a single YAML stream, then pipe
# to kubectl apply. This avoids the relative-path (../base) problem that occurs
# when copying the overlay to a temp dir — kustomize resolves ../base from where
# kustomization.yaml lives, which would be wrong in a temp dir.
#
# envsubst rewrites the prod kustomization.yaml with real ECR values, writes it
# to a temp file alongside the real base manifests (preserving relative paths),
# then kustomize renders and kubectl applies.

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

# Mirror the full k8s directory tree into TMP_DIR so relative paths resolve correctly
cp -r "${REPO_ROOT}/infrastructure/k8s/"* "${TMP_DIR}/"

# Overwrite prod/kustomization.yaml with the envsubst-substituted version
envsubst < "${REPO_ROOT}/infrastructure/k8s/prod/kustomization.yaml" \
         > "${TMP_DIR}/prod/kustomization.yaml"

echo "==> Applying manifests..."
kubectl apply -k "${TMP_DIR}/prod"

echo ""
echo "==> Waiting for rollout..."
kubectl rollout status deployment/chat-ui --timeout=300s
kubectl rollout status deployment/langgraph-api --timeout=300s
kubectl rollout status deployment/persistence-api --timeout=300s
kubectl rollout status statefulset/postgres --timeout=300s

echo ""
echo "==> EKS deployment complete!"
echo "    Run: kubectl get pods"
