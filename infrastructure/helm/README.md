# research-agent-platform Helm Chart

Helm chart for deploying the research-agent-platform to Kubernetes.

> **Note:** This chart is for production deployments. Local development uses Kustomize:
> ```bash
> kubectl apply -k infrastructure/k8s/dev
> ```

## Prerequisites

- Helm 3.x
- Kubernetes 1.28+
- nginx ingress controller installed in the cluster
- The `app-secrets` Kubernetes Secret must exist in the target namespace **before** installing the chart:

```bash
kubectl get secret app-secrets -n agents
```

If it doesn't exist, create it:
```bash
kubectl create secret generic app-secrets \
  --namespace agents \
  --from-literal=OPENROUTER_API_KEY=<your-key> \
  --from-literal=LANGSMITH_API_KEY=<your-key> \
  --from-literal=TAVILY_API_KEY=<your-key> \
  --from-literal=POSTGRES_PASSWORD=<your-password>
```

## Install

```bash
helm install agents infrastructure/helm/research-agent-platform \
  --namespace agents \
  --create-namespace
```

## Upgrade

```bash
helm upgrade agents infrastructure/helm/research-agent-platform \
  --namespace agents
```

## Uninstall

```bash
helm uninstall agents --namespace agents
```

## Key Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `chatUi.replicas` | `1` | chat-ui replica count |
| `langgraphApi.replicas` | `1` | langgraph-api replica count |
| `langgraphApi.persistence.enabled` | `false` | Use PVC for langgraph data (set true for prod) |
| `langgraphApi.persistence.storageClass` | `""` | Storage class for langgraph PVC (e.g. `gp2` on EKS) |
| `postgres.storageSize` | `5Gi` | PostgreSQL PVC storage size |
| `postgres.storageClass` | `""` | Storage class for postgres PVC |
| `ingress.enabled` | `true` | Enable nginx ingress |
| `ingress.host` | `agent.local` | Ingress hostname |
| `secrets.existingSecret` | `app-secrets` | Name of existing Kubernetes secret |

Override any value with `--set` or a custom values file:

```bash
helm upgrade agents infrastructure/helm/research-agent-platform \
  --namespace agents \
  --set chatUi.replicas=2 \
  --set langgraphApi.replicas=2 \
  --set langgraphApi.persistence.enabled=true \
  --set langgraphApi.persistence.storageClass=gp2 \
  --set ingress.host=agents.example.com
```

## Validate Without Installing

```bash
# Lint the chart
helm lint infrastructure/helm/research-agent-platform

# Render all templates
helm template agents infrastructure/helm/research-agent-platform --namespace agents

# Dry-run against cluster
helm template agents infrastructure/helm/research-agent-platform --namespace agents \
  | kubectl apply --dry-run=client -f -
```
