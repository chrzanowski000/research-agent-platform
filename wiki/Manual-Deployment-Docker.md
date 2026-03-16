# Manual: Deployment — Docker

[← Home](Home.md) | [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) | [Configuration](Manual-Configuration-and-Secrets.md)

> **Status note:** The Docker Compose setup is described in the README as **legacy/deprecated**. The preferred deployment is Kubernetes (see [Kubernetes Deployment](Manual-Deployment-Kubernetes.md)). `docker-compose.yml` has been updated to use the current `services/` and `infrastructure/docker/` layout.

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

## Service Name Note

The internal Docker Compose hostname for the persistence API is `research-persistence-api`, while in Kubernetes it is `persistence-api`. This difference only matters if switching between the two environments — it has no impact on a pure Docker Compose deployment.

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
**Cause:** Stale build context or Dockerfile path.
**Fix:** Verify `docker-compose.yml` references `services/<name>` as context and `../../infrastructure/docker/<name>.Dockerfile`. These paths reflect the current repo layout.

---

## See Also

- [Kubernetes Deployment](Manual-Deployment-Kubernetes.md) — Recommended deployment
- [Configuration and Secrets](Manual-Configuration-and-Secrets.md) — Full env var reference
- [Operations and Troubleshooting](Manual-Operations-and-Troubleshooting.md) — Container management
