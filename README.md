# agents-self-reflect

A multi-agent chat platform powered by LangGraph, featuring self-reflection agents and research capabilities.

## Architecture

```
Browser → chat-ui (3000)
             ↓
     NGINX Ingress (/api → langgraph-api, /research → persistence-api)
             ↓                                    ↓
  langgraph-api (2024)              persistence-api (8001)
       ↓         ↓                         ↓
  duckling    postgres (5432) ←────────────┘
   (8000)
```

### Services

| Service | Port | Description |
|---|---|---|
| chat-ui | 3000 | Next.js 15 frontend |
| langgraph-api | 2024 | LangGraph agent backend (self-reflection, research) |
| persistence-api | 8001 | FastAPI REST API for browsing research runs |
| duckling | 8000 | Rasa Duckling date parser |
| postgres | 5432 | PostgreSQL database |

---

## Local Kubernetes Deployment (Docker Desktop)

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) with Kubernetes enabled
- `kubectl` CLI
- `docker` CLI
- `pnpm` (for local frontend development only)

### 1. Enable Kubernetes in Docker Desktop

Docker Desktop → Settings → Kubernetes → Enable Kubernetes → Apply

Verify:
```bash
kubectl config use-context docker-desktop
kubectl get nodes
```

### 2. Configure Secrets

Edit `infrastructure/k8s/base/secrets.yaml`. Replace all `Y2hhbmdlbWU=` placeholders with your real base64-encoded values:

```bash
# Generate base64 value:
echo -n 'your-real-api-key' | base64
```

Secrets to configure:
- `OPENROUTER_API_KEY` — OpenRouter or OpenAI API key
- `LANGSMITH_API_KEY` — LangSmith tracing key (optional but recommended)
- `TAVILY_API_KEY` — Tavily web search key (optional)
- `POSTGRES_PASSWORD` — any strong password for the local database

### 3. Deploy

```bash
./scripts/deploy-local.sh
```

This builds all Docker images locally and applies the dev Kubernetes overlay.

### 4. Access the App

**Option A — Port-forward (simplest):**
```bash
kubectl port-forward svc/chat-ui 3000:3000 &
```
Open: http://localhost:3000

> **Note:** With port-forward, the browser cannot reach `langgraph-api` via `/api`. For full functionality without Ingress, set `NEXT_PUBLIC_API_URL=http://localhost:2024` in the ConfigMap and also forward the backend:
> ```bash
> kubectl port-forward svc/langgraph-api 2024:2024 &
> ```

**Option B — NGINX Ingress:**

Install the NGINX Ingress controller:
```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.10.0/deploy/static/provider/cloud/deploy.yaml
```

Add to `/etc/hosts`:
```
127.0.0.1 agent.local
```

Open: http://agent.local

### Common kubectl Commands

```bash
# View all pods
kubectl get pods

# View logs
kubectl logs deployment/langgraph-api -f

# Restart a deployment
kubectl rollout restart deployment/chat-ui

# Check pod status
kubectl describe pod <pod-name>

# Delete everything
kubectl delete -k infrastructure/k8s/dev
```

---

## EKS Deployment

### Prerequisites

1. AWS CLI configured and `kubectl` connected to your EKS cluster.
2. Push to `main` at least once — the CI workflow (`.github/workflows/build-and-push-images.yml`) builds all images and creates ECR repositories automatically. ECR repos are **not** created by `deploy-eks.sh`.
3. Required GitHub secrets: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AWS_ACCOUNT_ID`.
4. Note the `IMAGE_TAG` (commit SHA) printed in the workflow job summary.

### Deploy

```bash
aws eks update-kubeconfig --region us-east-1 --name <your-cluster-name>

export AWS_ACCOUNT_ID=123456789012
export AWS_REGION=us-east-1
export IMAGE_TAG=<commit-sha-from-ci-summary>

./scripts/deploy-eks.sh
```

### CI/CD

Push to `main` triggers the build workflow. The workflow job summary shows the exact `IMAGE_TAG` (commit SHA) to use with `deploy-eks.sh`.

---

## Development

### Run tests

```bash
conda run -n agents python -m pytest tests/ -v
```

### Build images manually

```bash
./scripts/build-images.sh
```

### Local frontend development (without Kubernetes)

```bash
cd services/chat-ui
pnpm install
pnpm dev
```

---

## Legacy: Docker Compose

The original Docker Compose setup has been replaced by Kubernetes. If you need to reference the original configuration, see git history prior to the Kubernetes migration commit.
