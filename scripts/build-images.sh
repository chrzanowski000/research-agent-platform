#!/usr/bin/env bash
# scripts/build-images.sh
# Builds all 3 custom Docker images and tags them locally.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Building chat-ui..."
docker build \
  -f "${REPO_ROOT}/infrastructure/docker/chat-ui.Dockerfile" \
  -t agents/chat-ui:latest \
  "${REPO_ROOT}/services/chat-ui/"

echo "==> Building langgraph-api..."
docker build \
  -f "${REPO_ROOT}/infrastructure/docker/langgraph-api.Dockerfile" \
  -t agents/langgraph-api:latest \
  "${REPO_ROOT}/services/langgraph-api/"

echo "==> Building persistence-api..."
docker build \
  -f "${REPO_ROOT}/infrastructure/docker/persistence-api.Dockerfile" \
  -t agents/persistence-api:latest \
  "${REPO_ROOT}/services/persistence-api/"

echo ""
echo "All images built successfully:"
docker images | grep "^agents/"
