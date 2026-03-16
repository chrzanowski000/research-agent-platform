# Applications

[← Home](Home.md) | [Overview](Overview.md) | [Architecture](Manual-Architecture.md)

This page documents every service in the platform: its purpose, location, stack, ports, and dependencies.

---

## Service Map

| Service | Port | Path | Role |
|---------|------|------|------|
| chat-ui | 3000 | `services/chat-ui/` | Frontend |
| langgraph-api | 2024 | `services/langgraph-api/` | Agent backend |
| persistence-api | 8001 | `services/persistence-api/` | Research history API |
| duckling | 8000 | (external image) | Date/time parser |
| postgres | 5432 | (external image) | Database |

---

## chat-ui

**Purpose:** Web-based chat interface for interacting with agents.

**Path:** `services/chat-ui/`

**Stack:**
- Next.js 15 (App Router)
- React 19
- TypeScript
- Tailwind CSS 4 + CVA (component variants)
- Radix UI primitives
- `nuqs` for URL state management
- LangGraph JS SDK (`@langchain/langgraph`) for streaming agent responses
- React Markdown + KaTeX for rendered output

**Default port:** 3000

**Key directories:**
```
services/chat-ui/src/
├── app/             # Next.js routes (api/ proxy, runs/ history page)
├── components/
│   ├── thread/      # Main chat component + AgentSelector
│   └── ui/          # Reusable UI components (shadcn-style)
├── providers/       # Context API (Stream, Thread, Artifact)
└── hooks/           # Custom React hooks
```

**Environment variables:**
| Variable | Purpose | Default |
|----------|---------|---------|
| `NEXT_PUBLIC_API_URL` | Public-facing API base URL | `/api` |
| `NEXT_PUBLIC_ASSISTANT_ID` | Default agent shown in UI | `self_reflection_agent` |
| `LANGGRAPH_API_URL` | Internal URL to langgraph-api | `http://agent:2024` |
| `RESEARCH_PERSISTENCE_API_URL` | Internal URL to persistence-api | `http://research-persistence-api:8001` |

**Dependencies:**
- Upstream: user browser
- Downstream: langgraph-api (streaming), persistence-api (history reads)

**Deployment role:** Stateless; single replica. Next.js proxies `/api/*` to langgraph-api and `/api/research/*` to persistence-api to avoid CORS.

---

## langgraph-api

**Purpose:** Runs all LangGraph agent graphs. Exposes the LangGraph CLI server which the frontend streams from.

**Path:** `services/langgraph-api/`

**Stack:**
- Python 3.12
- LangGraph + LangChain
- LangGraph CLI (`langgraph dev`) — open-source, in-memory checkpoint mode
- OpenRouter as LLM gateway (OpenAI-compatible)
- `sentence-transformers` for local embeddings
- `sqlalchemy` + `psycopg2` for database access
- Tavily Python SDK for web search

**Default port:** 2024

**Key files:**
```
services/langgraph-api/
├── agents/
│   ├── research_agent.py          # 10-node research pipeline
│   ├── self_reflection_agent.py   # v1: search_decision→web_search→generate→reflect loop
│   └── self_reflection_agent_v2.py # v2: generate→reflect loop (no web search)
├── config.py                       # Centralized env-var config + model resolution
├── database.py                     # SQLAlchemy engine + session setup
├── models.py                       # ORM models (Query, Run, Source)
└── langgraph.json                  # Graph registration for LangGraph CLI
```

**Agents:**

### research_agent
10-node plan-and-execute pipeline. Entry: `parse_dates`. Exit: `synthesize` (or `persist_run` when `PERSIST_RUNS=true`).
See [Manual: Agent Graphs](Manual-Agent-Graphs.md) for the full node breakdown.

### self_reflection_agent (v1)
4-node loop with optional Tavily web search. Entry: `search_decision`. Loops until draft is approved or max iterations reached.
Features: PII middleware masking, configurable max iterations, search budget.

### self_reflection_agent_v2
2-node loop without web search. Entry: `generate`. Simpler and faster than v1.

**Dependencies:**
- Upstream: chat-ui (streaming requests)
- Downstream: Duckling (date parsing), Semantic Scholar API, Tavily API, GitHub API, PostgreSQL (when `PERSIST_RUNS=true`)

**Deployment role:** Stateless pod; single replica. Uses in-memory LangGraph checkpointing (no Redis).

---

## persistence-api

**Purpose:** Stores and retrieves research run history. Provides a REST API over the PostgreSQL database populated by `research_agent`.

**Path:** `services/persistence-api/`

**Stack:**
- Python 3.12
- FastAPI
- SQLAlchemy 2.x (ORM)
- psycopg2-binary (PostgreSQL driver)
- Uvicorn (ASGI server)

**Default port:** 8001

**Key files:**
```
services/persistence-api/
├── main.py        # FastAPI app + all route handlers
├── models.py      # SQLAlchemy ORM (Query, Run, Source)
├── schemas.py     # Pydantic response schemas
└── database.py    # DB engine + session factory
```

**Endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/research/queries` | List all queries (paginated) |
| GET | `/research/queries/{query_id}` | Query + all its runs |
| GET | `/research/runs/{run_id}` | Full run with sources |
| DELETE | `/research/runs/{run_id}` | Delete run |
| DELETE | `/research/queries/{query_id}` | Delete query + associated folder |

**Dependencies:**
- Upstream: chat-ui (REST reads), research_agent (writes via `persist_run` node)
- Downstream: PostgreSQL

**Deployment role:** Stateless pod; single replica.

---

## duckling

**Purpose:** Natural language date/time extraction. Converts phrases like "last 3 months" or "papers from 2023 to 2025" into ISO date ranges.

**Image:** `rasa/duckling:latest`

**Default port:** 8000

**API used by research_agent:**
```
POST /parse
Body: locale=en_US&text=<input>&dims=["time"]
Response: JSON array of time entities
```

**Dependencies:**
- Upstream: research_agent (`parse_dates` node)
- Downstream: none

**Deployment role:** Stateless pod; single replica. No persistent storage.

---

## postgres

**Purpose:** Stores all research queries, runs, and sources.

**Image:** `postgres:16-alpine`

**Default port:** 5432

**Database:** `research`

**Schema:**
- `Query` — unique research topics
- `Run` — individual executions of a query (synthesis text, date_filter, timestamps)
- `Source` — individual search results linked to a run

**Dependencies:**
- Upstream: persistence-api (reads), research_agent (writes via `persist_run`)
- Downstream: none

**Deployment role:** Kubernetes StatefulSet with a PersistentVolumeClaim (5 Gi default). Not stateless — data survives pod restarts.

---

## See Also

- [Manual: Agent Graphs](Manual-Agent-Graphs.md) — Detailed node-by-node agent documentation
- [Manual: Architecture](Manual-Architecture.md) — How these services connect
- [Manual: Configuration and Secrets](Manual-Configuration-and-Secrets.md) — All environment variables
