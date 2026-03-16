# Manual: Deployment — Docker

[← Home](Home.md) | [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) | [Configuration](Manual-Configuration-and-Secrets.md)

> **Status note:** The Docker Compose setup is described in the README as **legacy/deprecated**. The preferred deployment is Kubernetes (see [Kubernetes Deployment](Manual-Deployment-Kubernetes.md)). The `docker-compose.yml` still works but uses older service names and paths that do not fully match the current `services/` directory layout. Inconsistencies are called out below.

---

## Prerequisites

- Docker Desktop (or Docker Engine + Compose plugin)
- 1Password CLI (`op`) installed and signed in — or manually set env vars
- `OPENROUTER_API_KEY`, `TAVILY_API_KEY`, `POSTGRES_PASSWORD` available

---

## Configuration — `.env_tpl`

`.env_tpl` is the canonical config file. It uses 1Password `op://` references for secrets.

**Option A — Use 1Password CLI (recommended):**

`run.sh` wraps Docker Compose with `op run --env-file=.env_tpl` which resolves `op://` refs at runtime.

**Option B — Manual env file:**

Copy `.env_tpl` to `.env` and replace `op://` references with literal values:

```bash
cp .env_tpl .env
# Edit .env: replace op://... values with actual secrets
```

Then use `docker compose --env-file .env up` directly.

---

## Starting the Stack

**With 1Password:**
```bash
./run.sh
```

`run.sh` runs:
```bash
op run --env-file=.env_tpl -- docker compose up --build
```
It also monitors container health and auto-cleans on exit.

**Without 1Password (manual .env):**
```bash
docker compose --env-file .env up --build
```

**Expected startup order:**
1. `postgres` starts, health check passes (`pg_isready`)
2. `duckling` starts
3. `agent` (langgraph-api) starts, waits for postgres + duckling
4. `research-persistence-api` starts, waits for postgres
5. `ui` (chat-ui) starts, waits for agent health check

---

## Accessing the Apps

| Service | URL |
|---------|-----|
| Chat UI | http://localhost:3000 |
| LangGraph API | http://localhost:2024 |
| Persistence API | http://localhost:8001 |
| Duckling | http://localhost:8000 |

---

## Stopping the Stack

```bash
# Ctrl+C in the run.sh terminal (auto-cleanup)
# Or:
docker compose down
# To remove volumes (wipes Postgres data):
docker compose down -v
```

---

## Known Inconsistencies

| Issue | Detail |
|-------|--------|
| **Old build context** | `docker-compose.yml` builds the `agent` service from `context: .` with `dockerfile: Dockerfile`. There is no top-level `Dockerfile` in the repo — the Dockerfiles are in `infrastructure/docker/`. This means `docker compose up --build` will likely fail unless a root-level `Dockerfile` exists. **Needs confirmation.** |
| **Old UI build context** | `ui` service uses `context: ./agent-chat-ui`. The frontend was moved to `services/chat-ui/`. This will fail. |
| **Old persistence API path** | `research-persistence-api` uses `dockerfile: research_persistence_api/Dockerfile`. The service is at `services/persistence-api/`. This will fail. |
| **Service name mismatch** | Internal hostname is `research-persistence-api` in Docker Compose but `persistence-api` in Kubernetes. |

**Practical recommendation:** The Docker Compose setup will require path fixes before it works with the current repo layout. Use Kubernetes for a working deployment. If Docker Compose is required, update `docker-compose.yml` to point to `infrastructure/docker/` Dockerfiles and `services/` build contexts.

---

## Environment Variables Reference

See [Manual: Configuration and Secrets](Manual-Configuration-and-Secrets.md) for the full variable list. Key vars for Docker Compose:

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `OPENROUTER_API_KEY` | Yes | — | LLM gateway key |
| `TAVILY_API_KEY` | Yes | — | Web search key |
| `POSTGRES_PASSWORD` | Yes | — | DB password |
| `MODEL_NAME` | No | `nvidia/nemotron-3-nano-30b-a3b:free` | Global LLM fallback |
| `PERSIST_RUNS` | No | `false` | Enable DB persistence |
| `LANGSMITH_TRACING` | No | `false` | Enable tracing |
| `DUCKLING_URL` | No | `http://duckling:8000` | Auto-set in Docker Compose |

---

## Troubleshooting

### `ui` container fails to start
**Cause:** Waits for `agent` health check (`curl http://localhost:2024/ok`). If `agent` takes > 30s to load models, health check times out.
**Fix:** Increase `start_period` in the `agent` healthcheck, or start services individually.

### `agent` exits on startup
**Cause:** Missing `OPENROUTER_API_KEY` or config error.
**Fix:** Check logs: `docker compose logs agent`. Verify `.env` / 1Password resolution.

### Postgres connection refused
**Cause:** `agent` or `research-persistence-api` starts before postgres is ready.
**Fix:** Compose `depends_on` with `condition: service_healthy` should handle this. If it doesn't, check that postgres healthcheck is passing: `docker compose ps postgres`.

### Build fails — Dockerfile not found
**Cause:** `docker-compose.yml` references old paths (see inconsistencies above).
**Fix:** Update `docker-compose.yml` with correct `context` and `dockerfile` paths for current repo layout.

---

## See Also

- [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) — Recommended deployment
- [Configuration and Secrets](Manual-Configuration-and-Secrets.md) — Full env var reference
- [Operations and Troubleshooting](Manual-Operations-and-Troubleshooting.md) — Container management
