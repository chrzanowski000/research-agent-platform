# Manual: Operations and Troubleshooting

[← Home](Home) | [Kubernetes Deployment](Manual-Deployment-Kubernetes) | [Configuration](Manual-Configuration-and-Secrets)

---

> **Namespace note:** Commands in this file omit `-n` and target the **`default` namespace** (local Docker Desktop / Kustomize deployments). For **GKE Helm** deployments (namespace `agents`), append `-n agents` to all `kubectl` commands, and use `kubectl rollout restart ... -n agents`, etc.

---

## Checking Status

### Pod status
```bash
kubectl get pods
kubectl get pods -w          # watch live
kubectl describe pod <name>  # events + resource usage
```

### Deployment rollout status
```bash
kubectl rollout status deployment/langgraph-api
kubectl rollout status deployment/chat-ui
kubectl rollout status deployment/persistence-api
kubectl rollout status deployment/duckling
kubectl rollout status statefulset/postgres
```

### Service endpoints
```bash
kubectl get svc
kubectl get ingress
```

---

## Viewing Logs

```bash
# Tail logs (live)
kubectl logs -f deployment/langgraph-api
kubectl logs -f deployment/chat-ui
kubectl logs -f deployment/persistence-api
kubectl logs -f deployment/duckling
kubectl logs -f statefulset/postgres

# Last 100 lines
kubectl logs deployment/langgraph-api --tail=100

# Previous container (after a crash)
kubectl logs deployment/langgraph-api --previous
```

**Useful log patterns:**

| What to look for | Indicates |
|-----------------|-----------|
| `ConfigError: Missing required API key` | `OPENROUTER_API_KEY` not in `app-secrets` |
| `Duckling request failed` | Duckling unreachable — check duckling pod |
| `Semantic Scholar search failed` | S2 API error or rate limit — try `USE_MOCK_S2=true` |
| `persist_run failed (non-fatal)` | DB persistence error — check `DATABASE_URL` and postgres |
| `Draft APPROVED after N iteration(s)` | Normal completion of self-reflection agents |
| `Max iterations reached` | Agent hit loop limit — draft returned as-is |
| `PII detected` | PII middleware blocked a request |
| `Active models ──` | Startup model printout (when `LOG_MODELS=true`) |

---

## Restarting Services

```bash
# Restart a single deployment
kubectl rollout restart deployment/langgraph-api
kubectl rollout restart deployment/chat-ui
kubectl rollout restart deployment/persistence-api
kubectl rollout restart deployment/duckling

# Restart all at once
kubectl rollout restart deployment/langgraph-api deployment/chat-ui deployment/persistence-api deployment/duckling
```

Postgres (StatefulSet) — only restart if needed:
```bash
kubectl rollout restart statefulset/postgres
```

---

## Rebuilding Images

After code changes:

```bash
# Rebuild all three images
scripts/build-images.sh

# Rebuild without cache
NO_CACHE=1 scripts/build-images.sh

# Then restart to pick up new images
kubectl rollout restart deployment/langgraph-api deployment/chat-ui deployment/persistence-api
```

---

## Re-injecting Secrets

After cluster recreation or 1Password session expiry:

```bash
# Re-authenticate 1Password (if session expired)
op signin

# Re-inject secrets
scripts/inject-secrets.sh

# Restart affected pods
kubectl rollout restart deployment/langgraph-api deployment/persistence-api
```

---

## Verifying Ingress and Networking

```bash
# Check ingress controller is running
kubectl get pods -n ingress-nginx

# Check ingress rules
kubectl describe ingress

# Test DNS resolution (requires /etc/hosts entry for agent.local)
curl -v http://agent.local/api/ok

# Test directly via port-forward (bypasses ingress)
kubectl port-forward svc/langgraph-api 2024:2024 &
curl http://localhost:2024/ok
```

---

## Cleanup

```bash
# Remove all platform resources — local Docker Desktop (Kustomize dev overlay):
kubectl delete -k infrastructure/k8s/dev

# Remove all platform resources — GKE Kustomize:
kubectl delete -k infrastructure/k8s/gke

# Remove all platform resources — Helm (GKE):
helm uninstall agents -n agents

# Remove the nginx ingress controller (Helm, installed separately):
helm uninstall ingress-nginx -n ingress-nginx
kubectl delete namespace ingress-nginx

# Remove secrets:
kubectl delete secret app-secrets             # local / Kustomize (default namespace)
kubectl delete secret app-secrets -n agents   # Helm (agents namespace)

# Remove PVC (WARNING: destroys all Postgres data):
kubectl delete pvc postgres-data                          # local Docker Desktop (Kustomize)
kubectl delete pvc postgres-data-postgres-0 -n agents     # GKE Helm
```

---

## Common Failures

### Symptom: `CrashLoopBackOff` on `langgraph-api`

**Cause 1:** Missing or invalid API key
```bash
kubectl logs deployment/langgraph-api --previous
# Look for: ConfigError: Missing required API key
```
**Fix:** Re-inject secrets: `scripts/inject-secrets.sh`, then restart.

**Cause 2:** Postgres not ready when langgraph-api starts
**Fix:** Check postgres pod status: `kubectl get pods`. Wait for it to be Running before restarting langgraph-api.

---

### Symptom: `ImagePullBackOff`

**Symptom:** Pod stays in `ImagePullBackOff` or `ErrImagePull` state.

**Cause:** Kubernetes trying to pull image from a registry that doesn't have it.

**Fix:** Ensure you applied the **dev** overlay (not base or prod):
```bash
kubectl apply -k infrastructure/k8s/dev
```
The dev overlay sets `imagePullPolicy: Never`.

---

### Symptom: Chat UI loads but agent requests hang / timeout

**Cause 1:** `LANGGRAPH_API_URL` is wrong in the chat-ui deployment.
**Fix:** Check configmap: `kubectl get configmap -o yaml`. Verify `LANGGRAPH_API_URL=http://langgraph-api:2024`.

**Cause 2:** `langgraph-api` is unhealthy.
**Fix:**
```bash
kubectl port-forward svc/langgraph-api 2024:2024 &
curl http://localhost:2024/ok
kubectl logs deployment/langgraph-api
```

---

### Symptom: Research history not showing in UI

**Cause 1:** `PERSIST_RUNS` is not set to `true`.
**Fix:** Update ConfigMap or env var to `PERSIST_RUNS=true` and restart langgraph-api.

**Cause 2:** `RESEARCH_PERSISTENCE_API_URL` is wrong in chat-ui.
**Fix:** Check configmap. Value should be `http://persistence-api:8001`.

**Cause 3:** Database schema not initialized.
**Fix:** Check persistence-api logs:
```bash
kubectl logs deployment/persistence-api
```
Look for SQLAlchemy table creation errors.

---

### Symptom: Duckling parse returns empty / date filter not working

**Symptom:** Date filters are ignored, or date ranges come back empty. Logs show `Duckling request failed`.

**Cause:** Duckling pod not running or unreachable.

**Fix:**
```bash
kubectl get pods | grep duckling
kubectl logs deployment/duckling
kubectl port-forward svc/duckling 8000:8000 &
curl -d 'locale=en_US&text=last 3 months&dims=["time"]' http://localhost:8000/parse
```
Expected: JSON array with a `time` entity.

---

### Symptom: Semantic Scholar returns no results

**Cause 1:** API rate limit (429).
**Fix:** Enable mock mode for dev: `USE_MOCK_S2=true`. For production, add wait time between requests (already built-in at 1s per query).

**Cause 2:** Query too specific — similarity threshold drops all results.
**Fix:** Check logs for `rank_results_by_similarity: kept 0 / N results`. Try a broader query. The threshold is `SIMILARITY_THRESHOLD = 0.1` in `research_agent.py`.

---

### Symptom: Postgres StatefulSet stuck in `Pending`

**Symptom:** `postgres-0` pod stays in `Pending` state and never starts.

**Cause:** No StorageClass available to provision the PVC.

**Fix:**
```bash
kubectl get storageclass
kubectl describe pvc postgres-data
```
Docker Desktop should provide a `hostpath` storageclass by default.

---

### Symptom: `app-secrets` not found

**Symptom:** Pods crash with `CreateContainerConfigError` or `secret "app-secrets" not found`.

**Cause:** Secrets were never injected, or were deleted.

**Fix:**
```bash
kubectl get secret app-secrets  # should exist
scripts/inject-secrets.sh        # re-create
```

---

## Useful One-liners

```bash
# All pod statuses at a glance
kubectl get pods

# Get all env vars for a deployment (includes secrets)
kubectl exec deployment/langgraph-api -- env | sort

# Watch logs from all pods matching a label
kubectl logs -l app=langgraph-api -f

# Exec into a running container
kubectl exec -it deployment/langgraph-api -- bash

# Check resource usage
kubectl top pods

# Describe all events (useful after a failed deploy)
kubectl get events --sort-by=.lastTimestamp
```

---

## See Also

- [Kubernetes Deployment](Manual-Deployment-Kubernetes) — Deploy commands
- [Configuration and Secrets](Manual-Configuration-and-Secrets) — Env vars reference
- [Docker Deployment](Manual-Deployment-Docker) — Docker Compose troubleshooting
