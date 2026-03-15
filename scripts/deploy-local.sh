#!/usr/bin/env bash
# scripts/deploy-local.sh
# Builds images and deploys to local Docker Desktop Kubernetes.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Verify kubectl is pointing at Docker Desktop
CONTEXT=$(kubectl config current-context)
if [[ "$CONTEXT" != "docker-desktop" ]]; then
  echo "WARNING: kubectl context is '$CONTEXT', not 'docker-desktop'."
  read -rp "Continue? [y/N] " confirm
  [[ "$confirm" == "y" ]] || exit 1
fi

echo "==> Building Docker images..."
NO_CACHE="${NO_CACHE:-}" "${REPO_ROOT}/scripts/build-images.sh"

echo ""
echo "==> Injecting secrets from 1Password..."
"${REPO_ROOT}/scripts/inject-secrets.sh"

echo ""
echo "==> Applying Kubernetes manifests (dev overlay)..."
kubectl apply -k "${REPO_ROOT}/infrastructure/k8s/dev"

echo ""
echo "==> Waiting for deployments to be ready..."
kubectl rollout status statefulset/postgres --timeout=120s
kubectl rollout status deployment/duckling --timeout=60s
kubectl rollout status deployment/persistence-api --timeout=120s
kubectl rollout status deployment/langgraph-api --timeout=180s
kubectl rollout status deployment/chat-ui --timeout=120s

echo ""
echo "==> Deployment complete!"
echo ""
echo "To access the app, run ONE of the following:"
echo ""
echo "  Option A - Port-forward (simplest):"
echo "    kubectl port-forward svc/chat-ui 3000:3000 &"
echo "    kubectl port-forward svc/langgraph-api 2024:2024 &"
echo "    Then open: http://localhost:3000"
echo "    (Set NEXT_PUBLIC_API_URL=http://localhost:2024 if not using Ingress)"
echo ""
echo "  Option B - Ingress (requires nginx ingress controller):"
echo "    Add to /etc/hosts: 127.0.0.1 agent.local"
echo "    Then open: http://agent.local"
