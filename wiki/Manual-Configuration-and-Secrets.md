# Manual: Configuration and Secrets

[← Home](Home) | [Architecture](Manual-Architecture) | [Kubernetes Deployment](Manual-Deployment-Kubernetes)

This page centralizes all configuration knowledge for the platform. Variables are grouped by service and annotated as secrets vs. non-secret config.

**Source of truth:** `.env_tpl` at the repo root.

---

## Secret Handling

### 1Password (primary method)

Secrets in `.env_tpl` use 1Password references:
```
OPENROUTER_API_KEY=op://APIS/OPENROUTER_API_KEY_SELF_REFLECT/credential
```

**Kubernetes:** `scripts/inject-secrets.sh` resolves these references and creates a K8s secret named `app-secrets`:
```bash
scripts/inject-secrets.sh
```

`app-secrets` is in-memory — it is not persisted to a YAML file and must be re-injected after cluster recreation.

**Docker Compose:** `run.sh` uses `op run --env-file=.env_tpl` to inject secrets at process start.

### Manual / CI override

Replace `op://` values with literals. For Kubernetes:
```bash
kubectl create secret generic app-secrets \
  --from-literal=OPENROUTER_API_KEY=sk-... \
  --from-literal=TAVILY_API_KEY=tvly-... \
  --from-literal=POSTGRES_PASSWORD=mypassword \
  --from-literal=LANGSMITH_API_KEY=ls-... \
  --dry-run=client -o yaml | kubectl apply -f -
```

---

## Variables by Service

### LLM / API Keys (secrets)

| Variable | Secret | Description |
|----------|--------|-------------|
| `OPENROUTER_API_KEY` | ✅ | LLM gateway key (OpenRouter). Also accepts `OPENAI_API_KEY`. |
| `OPENROUTER_BASE_URL` | No | Base URL for LLM API. Default: `https://openrouter.ai/api/v1` |
| `TAVILY_API_KEY` | ✅ | Tavily web search API key |
| `LANGSMITH_API_KEY` | ✅ | LangSmith tracing key. Also accepts `LANGCHAIN_API_KEY`. |
| `POSTGRES_PASSWORD` | ✅ | PostgreSQL superuser password |

---

### LangSmith Tracing

| Variable | Default | Description |
|----------|---------|-------------|
| `LANGSMITH_TRACING` | `false` | Set to `true` to enable LangSmith trace export |
| `LANGSMITH_PROJECT` | `self-reflection-agent` | Project name in LangSmith UI |

Tracing is off by default. Enable for debugging or observability:
```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your-key>
```

---

### Model Configuration (langgraph-api)

The agent backend uses a 3-tier model resolution hierarchy:

```
1. Per-node override    e.g. RESEARCH_PLANNER_MODEL
       ↓ (if not set)
2. Agent-wide override  e.g. RESEARCH_MODEL
       ↓ (if not set)
3. Global fallback      MODEL_NAME
```

**Global fallback:**
| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_NAME` | `nvidia/nemotron-3-nano-30b-a3b:free` | Fallback for all nodes not individually configured |

**research_agent:**
| Variable | Node | Description |
|----------|------|-------------|
| `RESEARCH_MODEL` | All research nodes | Agent-wide override |
| `RESEARCH_PLANNER_MODEL` | `extract_research_intent` | Intent extraction |
| `RESEARCH_TOPIC_EXTRACTOR_MODEL` | `extract_research_intent` | Falls back to `RESEARCH_PLANNER_MODEL` |
| `RESEARCH_KEYWORD_EXPANDER_MODEL` | `generate_semantic_queries` | Falls back to `RESEARCH_PLANNER_MODEL` |
| `RESEARCH_QUERY_GENERATOR_MODEL` | query generation | Falls back to `RESEARCH_PLANNER_MODEL` |
| `RESEARCH_FILTER_MODEL` | `rank_results_by_similarity` | Similarity ranking |
| `RESEARCH_SYNTHESIZER_MODEL` | `synthesize` | Final synthesis |
| `RESEARCH_EMBEDDING_MODEL` | `rank_results_by_similarity` | Local sentence-transformers model. Default: `BAAI/bge-large-en-v1.5`. `.env_tpl` sets `allenai/specter2` |

**self_reflection_agent (v1):**
| Variable | Node | Description |
|----------|------|-------------|
| `REFLECTION_V1_MODEL` | All v1 nodes | Agent-wide override |
| `REFLECTION_V1_SEARCH_DECISION_MODEL` | `search_decision` | Web search decision |
| `REFLECTION_V1_GENERATE_MODEL` | `generate` | Answer generation |
| `REFLECTION_V1_REFLECT_MODEL` | `reflect` | Draft review |

**self_reflection_agent_v2:**
| Variable | Node | Description |
|----------|------|-------------|
| `REFLECTION_V2_MODEL` | All v2 nodes | Agent-wide override |
| `REFLECTION_V2_GENERATE_MODEL` | `generate` | Answer generation |
| `REFLECTION_V2_REFLECT_MODEL` | `reflect` | Draft review |

---

### research_agent Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `PERSIST_RUNS` | `false` | Enable research run persistence to PostgreSQL + disk. Set to `true` in production. |
| `USE_MOCK_S2` | `false` | Use mock Semantic Scholar responses. Set to `true` during dev to avoid API rate limits. |
| `LOG_MODELS` | `false` | Print active model names at startup. Useful for verifying override config. |

---

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/research` | Full SQLAlchemy connection string. In Kubernetes: uses `postgres` service hostname. |
| `POSTGRES_PASSWORD` | — (required) | Must match password in `DATABASE_URL` |

Kubernetes services use:
```
postgresql://postgres:${POSTGRES_PASSWORD}@postgres:5432/research
```

Local dev without Docker:
```
postgresql://postgres:<password>@localhost:5432/research
```

---

### Duckling

| Variable | Default | Description |
|----------|---------|-------------|
| `DUCKLING_URL` | `http://localhost:8000` | URL to the Duckling date parser. In Kubernetes/Docker Compose: `http://duckling:8000` |

---

### chat-ui

| Variable | Build-time? | Description |
|----------|-------------|-------------|
| `NEXT_PUBLIC_API_URL` | Yes (public) | Base URL the browser uses for API calls. In K8s with Ingress: `/api`. With port-forward: `http://localhost:2024`. |
| `NEXT_PUBLIC_ASSISTANT_ID` | Yes (public) | Default agent ID shown in UI. Options: `research_agent`, `self_reflection_agent`, `self_reflection_agent_v2` |
| `LANGGRAPH_API_URL` | No (server-only) | Internal URL used by Next.js API routes to proxy to langgraph-api. Never exposed to the browser. |
| `RESEARCH_PERSISTENCE_API_URL` | No (server-only) | Internal URL for persistence-api. Never exposed to the browser. |

`NEXT_PUBLIC_*` variables are embedded into the Next.js bundle at **build time**. Changing them requires a rebuild.

---

## Kubernetes ConfigMap

In Kubernetes, non-secret configuration is provided via a ConfigMap (from `infrastructure/k8s/base/configmaps.yaml` or the Helm chart's generated ConfigMap). Secret values are provided from the `app-secrets` Secret.

**ConfigMap** (non-sensitive):
- `OPENROUTER_BASE_URL`
- `MODEL_NAME`
- `DUCKLING_URL`
- `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`
- `PERSIST_RUNS`
- `USE_MOCK_S2`
- `LOG_MODELS`
- `DATABASE_URL` (connection string without embedded password) — **Needs confirmation:** database URL structure in the ConfigMap may embed the password. Verify before committing to a public repo.

**Secret** (`app-secrets`):
- `OPENROUTER_API_KEY`
- `TAVILY_API_KEY`
- `POSTGRES_PASSWORD`
- `LANGSMITH_API_KEY`

---

## Enabling Specific Features

### Enable research run persistence
```
PERSIST_RUNS=true
DATABASE_URL=postgresql://postgres:<password>@postgres:5432/research
POSTGRES_PASSWORD=<password>
```

### Enable LangSmith observability
```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<key>
LANGSMITH_PROJECT=my-project
```

### Use a different LLM for synthesis only
```
MODEL_NAME=qwen/qwen3-235b-a22b:free      # global fallback
RESEARCH_SYNTHESIZER_MODEL=openai/gpt-4o   # synthesis override
```

### Print active models at startup
```
LOG_MODELS=true
```

Output appears in the langgraph-api container logs on startup.

---

## See Also

- [Kubernetes Deployment](Manual-Deployment-Kubernetes) — How secrets are injected
- [Docker Deployment](Manual-Deployment-Docker) — Docker Compose secret handling
- [Agent Graphs](Manual-Agent-Graphs) — Per-node model assignments
