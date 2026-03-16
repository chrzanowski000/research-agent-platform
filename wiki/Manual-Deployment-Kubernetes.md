# Manual: Deployment — Kubernetes

[← Home](Home.md) | [Docker Deployment](Manual-Deployment-Docker.md) | [Configuration](Manual-Configuration-and-Secrets.md) | [Operations](Manual-Operations-and-Troubleshooting.md)

Two deployment paths are supported:

| Path | Target | Tool | Script |
|------|--------|------|--------|
| **Local / Dev** | Docker Desktop Kubernetes | Kustomize | `scripts/deploy-local.sh` |
| **Production** | AWS EKS | Kustomize | `scripts/deploy-eks.sh` |
| **Helm (alt)** | Any K8s | Helm | Manual |

---

## Local Deployment (Docker Desktop)

### Prerequisites

- Docker Desktop with Kubernetes enabled
- `kubectl` installed and context set to `docker-desktop`
- 1Password CLI (`op`) installed and signed in
- `kustomize` (or `kubectl apply -k` — bundled with `kubectl`)

### 1. Build Docker Images

```bash
scripts/build-images.sh
```

Builds three images locally (no push to a registry):
- `agents/langgraph-api:latest`
- `agents/chat-ui:latest`
- `agents/persistence-api:latest`

Force a clean build:
```bash
NO_CACHE=1 scripts/build-images.sh
```

### 2. Deploy

**Full automated deploy:**
```bash
scripts/deploy-local.sh
```

This script:
1. Verifies context is `docker-desktop` (prompts if not)
2. Runs `build-images.sh`
3. Applies the dev Kustomize overlay: `kubectl apply -k infrastructure/k8s/dev`
4. Runs `scripts/inject-secrets.sh` (creates K8s secret `app-secrets` from 1Password)
5. Restarts `langgraph-api` and `persistence-api` deployments
6. Waits for all rollouts to complete

**Manual step-by-step:**
```bash
kubectl apply -k infrastructure/k8s/dev
scripts/inject-secrets.sh
kubectl rollout restart deployment/langgraph-api
kubectl rollout restart deployment/persistence-api
```

### 3. Access the App

**Option A — Port-forward (no Ingress required):**
```bash
kubectl port-forward svc/chat-ui 3000:3000 &
kubectl port-forward svc/langgraph-api 2024:2024 &
```
Open http://localhost:3000

**Option B — Ingress (requires nginx ingress controller):**
```bash
# Install nginx ingress controller (if not already installed)
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.8.2/deploy/static/provider/cloud/deploy.yaml

# Add to /etc/hosts:
# 127.0.0.1 agent.local
```
Open http://agent.local

---

## Secret Injection

Secrets are stored in a Kubernetes secret named `app-secrets` in the default namespace.

**Automated (1Password):**
```bash
scripts/inject-secrets.sh
```

This reads `.env_tpl`, resolves all `op://` references via the 1Password CLI, and creates/updates the `app-secrets` secret in-cluster.

**Manual (without 1Password):**
```bash
kubectl create secret generic app-secrets \
  --from-literal=OPENROUTER_API_KEY=<value> \
  --from-literal=TAVILY_API_KEY=<value> \
  --from-literal=POSTGRES_PASSWORD=<value> \
  --from-literal=LANGSMITH_API_KEY=<value> \
  --dry-run=client -o yaml | kubectl apply -f -
```

**Important:** `app-secrets` is in-memory only in the dev overlay — it is not written to a YAML file in the repo.

---

## Kustomize Overlay Structure

```
infrastructure/k8s/
├── base/                      # Shared manifests
│   ├── configmaps.yaml
│   ├── secrets.yaml           # Secret key references (not values)
│   ├── services.yaml
│   ├── chat-ui-deployment.yaml
│   ├── langgraph-deployment.yaml
│   ├── persistence-deployment.yaml
│   ├── duckling-deployment.yaml
│   ├── postgres-statefulset.yaml
│   └── ingress.yaml
├── dev/                       # Docker Desktop overlay
│   └── kustomization.yaml     # imagePullPolicy: Never
└── prod/                      # EKS overlay
    └── kustomization.yaml     # PVC + prod resource limits
```

The dev overlay sets `imagePullPolicy: Never` — Kubernetes uses locally built images instead of pulling from a registry.

---

## Helm Deployment

A Helm chart is available at `infrastructure/helm/research-agent-platform/` (chart version 0.1.0).

### Prerequisites

- `helm` CLI installed
- A running Kubernetes cluster with `kubectl` configured
- `app-secrets` secret already created (Helm does not manage secrets — inject separately)

### Install

```bash
helm install research-agent-platform \
  infrastructure/helm/research-agent-platform/ \
  --namespace default
```

### Install with custom values

```bash
helm install research-agent-platform \
  infrastructure/helm/research-agent-platform/ \
  --set langgraphApi.image.tag=v1.2.3 \
  --set ingress.host=myapp.example.com \
  --set postgres.storage.size=20Gi
```

### Upgrade

```bash
helm upgrade research-agent-platform \
  infrastructure/helm/research-agent-platform/
```

### Uninstall

```bash
helm uninstall research-agent-platform
```

### Key Helm values (`values.yaml`)

| Path | Default | Description |
|------|---------|-------------|
| `ingress.enabled` | `true` | Enable NGINX Ingress |
| `ingress.host` | `agent.local` | Hostname |
| `postgres.storage.size` | `5Gi` | PVC size |
| `langgraphApi.replicas` | `1` | Replica count |
| `persistence.enabled` | `true` | Enable PERSIST_RUNS |
| `chatUi.env.NEXT_PUBLIC_ASSISTANT_ID` | `self_reflection_agent` | Default agent |

---

## EKS (Production) Deployment

### Prerequisites

- AWS credentials configured (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` or IAM role)
- `kubectl` configured for EKS cluster
- ECR repositories created for all three images
- Environment variables set: `AWS_ACCOUNT_ID`, `AWS_REGION`, `IMAGE_TAG`

### Deploy

```bash
AWS_ACCOUNT_ID=123456789012 \
AWS_REGION=us-east-1 \
IMAGE_TAG=abc1234 \
scripts/deploy-eks.sh
```

The script:
1. Tags and pushes images to ECR
2. Applies the prod Kustomize overlay: `kubectl apply -k infrastructure/k8s/prod`
3. Injects secrets from 1Password
4. Restarts deployments

### CI/CD

A GitHub Actions workflow (`.github/`) handles EKS deploys automatically on push to the main branch. It uses commit SHA as the image tag.

---

## Verify Deployment

```bash
# All pods running
kubectl get pods

# Specific pod status
kubectl describe pod <pod-name>

# Application health
kubectl port-forward svc/chat-ui 3000:3000 &
curl http://localhost:3000

# LangGraph API health
kubectl port-forward svc/langgraph-api 2024:2024 &
curl http://localhost:2024/ok

# Persistence API health
kubectl port-forward svc/persistence-api 8001:8001 &
curl http://localhost:8001/health
```

---

## Troubleshooting

### `ImagePullBackOff` on local Kubernetes
**Cause:** `imagePullPolicy: Always` trying to pull from a registry, or image not built locally.
**Fix:** Verify you're using the dev overlay (`kubectl apply -k infrastructure/k8s/dev`). Check image name matches what `build-images.sh` builds.

### `CrashLoopBackOff` on langgraph-api
**Cause:** Usually a missing secret or config error.
**Fix:**
```bash
kubectl logs deployment/langgraph-api
```
Check for `ConfigError: Missing required API key`. Verify `app-secrets` exists and has correct keys.

### `app-secrets` not found
**Cause:** `inject-secrets.sh` not run, or 1Password session expired.
**Fix:**
```bash
op signin  # re-authenticate to 1Password
scripts/inject-secrets.sh
kubectl rollout restart deployment/langgraph-api deployment/persistence-api
```

### Ingress returns 404
**Cause:** NGINX ingress controller not installed, or `/etc/hosts` not updated.
**Fix:**
```bash
kubectl get ingressclass  # should show nginx
# Add to /etc/hosts: 127.0.0.1 agent.local
```

### Postgres StatefulSet stuck in Pending
**Cause:** PVC cannot be provisioned (no default StorageClass on Docker Desktop).
**Fix:**
```bash
kubectl get storageclass  # verify 'hostpath' or 'standard' exists
kubectl describe pvc postgres-data
```

---

## See Also

- [Docker Deployment](Manual-Deployment-Docker.md) — Legacy Docker Compose
- [Configuration and Secrets](Manual-Configuration-and-Secrets.md) — All env vars and secret handling
- [Operations and Troubleshooting](Manual-Operations-and-Troubleshooting.md) — Day-2 ops and log viewing
