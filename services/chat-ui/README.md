# Agent Chat UI

Agent Chat UI is a Next.js 15 application that provides a chat interface for interacting with LangGraph agents. It is pre-configured to work with the **Research Agent** — a 10-node plan-and-execute pipeline that retrieves and synthesizes academic papers from Semantic Scholar.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────┐
│         Browser (Next.js 15 Chat UI)             │
│              http://localhost:3000               │
└────────────────────┬─────────────────────────────┘
                     │ HTTP  (NEXT_PUBLIC_API_URL)
                     ▼
┌──────────────────────────────────────────────────┐
│           LangGraph API Backend                  │
│              http://localhost:2024               │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │          Research Agent (default)          │  │
│  │  parse_dates → extract_intent →           │  │
│  │  generate_queries → normalize →           │  │
│  │  apply_date_filter → execute_searches →   │  │
│  │  validate_date_range →                    │  │
│  │  rank_by_similarity → synthesize →        │  │
│  │  persist_run                              │  │
│  └────────────────────────────────────────────┘  │
│                                                  │
│  ┌────────────────────────────────────────────┐  │
│  │    Self-Reflection Agents (extras)         │  │
│  │  self_reflection_agent (v1)               │  │
│  │  self_reflection_agent_v2 (v2)            │  │
│  └────────────────────────────────────────────┘  │
└──────┬──────────┬──────────────┬─────────────────┘
       │          │              │
       ▼          ▼              ▼
┌──────────┐ ┌──────────┐ ┌────────────────────────┐
│ Duckling │ │ Postgres │ │ Research Persistence   │
│ :8000    │ │ :5432    │ │ API  :8001             │
│          │ │          │ │                        │
│ Date/time│ │ Research │ │ GET /research/queries  │
│ parsing  │ │ run      │ │ GET /research/runs/:id │
│          │ │ storage  │ │ DELETE /research/...   │
└──────────┘ └──────────┘ └────────────────────────┘
```

**External APIs used by the Research Agent:**
- **OpenRouter** — LLM calls for all pipeline nodes (intent extraction, query generation, synthesis)
- **Semantic Scholar Graph API** — Academic paper search (primary source)
- **Duckling** — Natural language date parsing (Docker service)
- **BAAI/bge-large-en-v1.5** — Local embedding model for cosine-similarity ranking (no API key needed)

---

## Research Agent Pipeline

The Research Agent is the primary agent in this system. It takes a natural-language research question and produces a structured synthesis of relevant academic papers.

### Nodes (in order)

| # | Node | What it does |
|---|------|-------------|
| 1 | `parse_dates` | Calls Duckling to extract `start_date` / `end_date` from the query (e.g. "papers from 2022 to 2024") |
| 2 | `extract_research_intent` | LLM extracts structured intent: `problem_domains`, `methods`, `related_concepts` (3–5 phrases each) |
| 3 | `generate_semantic_queries` | Generates semantic search queries via combinatorial expansion of intent components |
| 4 | `normalize_queries` | Deduplicates and strips queries down to bare keyword strings |
| 5 | `apply_date_filter` | Assembles the search plan, embedding the date constraint into each task |
| 6 | `execute_searches` | Runs queries against Semantic Scholar (1 s delay per request); deduplicates by URL |
| 7 | `validate_date_range` | Removes any retrieved result outside the requested date window |
| 8 | `rank_results_by_similarity` | Runs local `BAAI/bge-large-en-v1.5` embeddings; keeps results above cosine similarity 0.1; deduplicates by title |
| 9 | `synthesize` | LLM produces a structured brief: **Summary / Key Findings / Sources** |
| 10 | `persist_run` | *(optional)* Saves query, run metadata, sources, and disk artifacts to PostgreSQL when `PERSIST_RUNS=true` |

### Key constants

| Constant | Default | Purpose |
|----------|---------|---------|
| `SIMILARITY_THRESHOLD` | `0.1` | Minimum cosine similarity to keep a result |
| `QUERY_COUNT` | `5` | Number of search queries generated per run |
| `RESULTS_PER_QUERY` | `10` | Results fetched per individual search |
| `S2_DATE_FETCH_LIMIT` | `10` | Pre-filter fetch limit from Semantic Scholar |

---

## Docker Setup (Recommended)

### Quick Start

Run the entire stack from the **project root**:

```bash
# With 1Password (no secrets on disk)
op run --env-file=.env_tpl -- docker compose up --build --remove-orphans

# Or with manually exported variables
export OPENROUTER_API_KEY="your_key"
export TAVILY_API_KEY="your_key"
export LANGSMITH_API_KEY="your_key"   # optional
export POSTGRES_PASSWORD="your_password"
docker compose up --build --remove-orphans
```

### Services

| Service | URL | Purpose |
|---------|-----|---------|
| Chat UI | http://localhost:3000 | Next.js frontend |
| LangGraph API | http://localhost:2024 | Agent execution backend |
| Research Persistence API | http://localhost:8001 | Browse/delete persisted research runs |
| Duckling | http://localhost:8000 | Date parser (used by Research Agent) |
| PostgreSQL | localhost:5432 | Research run persistence |
| LangSmith Studio | https://smith.langchain.com/studio/?baseUrl=http://localhost:2024 | Trace & debug agents |

### Rebuild After Code Changes

```bash
# Rebuild only the UI service
docker compose up --build ui

# Rebuild everything
docker compose up --build

# Full clean rebuild (e.g. after lockfile changes)
docker compose down --volumes --remove-orphans
docker compose build --no-cache
docker compose up
```

### View Logs

```bash
docker compose logs -f           # all services
docker compose logs -f ui        # chat frontend
docker compose logs -f agent     # LangGraph backend
docker compose logs -f research-persistence-api
docker compose logs -f duckling
```

---

## Local Setup (Without Docker)

### 1. Install Node dependencies

```bash
cd agent-chat-ui
pnpm install
```

### 2. Configure environment

Copy the example env file and fill in the values:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
# URL of the running LangGraph API backend
NEXT_PUBLIC_API_URL=http://localhost:2024

# Which agent to use (research_agent is the default/recommended)
NEXT_PUBLIC_ASSISTANT_ID=research_agent

# Internal service-to-service URL (Docker only — not used in local dev)
LANGGRAPH_API_URL=http://agent:2024

# URL of the Research Persistence API (used by /runs page)
RESEARCH_PERSISTENCE_API_URL=http://localhost:8001
```

### 3. Start the backend first

The UI requires the LangGraph backend to be running. Start it with Docker or locally:

```bash
# Easiest: run just the agent + duckling via Docker
docker compose up agent duckling
```

### 4. Run the UI

```bash
pnpm dev
```

Open http://localhost:3000.

---

## Environment Variables

### Frontend Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `NEXT_PUBLIC_API_URL` | `http://localhost:2024` | Yes | URL of the LangGraph backend, resolved by the browser |
| `NEXT_PUBLIC_ASSISTANT_ID` | `self_reflection_agent` | Yes | Graph/assistant ID to use (set to `research_agent` for the main agent) |
| `LANGGRAPH_API_URL` | `http://agent:2024` | Docker only | Internal service-to-service URL (uses Docker hostname `agent`) |
| `RESEARCH_PERSISTENCE_API_URL` | `http://research-persistence-api:8001` | No | URL for the research persistence API (enables `/runs` page) |

> **Note:** `NEXT_PUBLIC_*` variables are embedded into the browser bundle at build time. Never put secrets in `NEXT_PUBLIC_*` variables.

### Backend Variables (used by LangGraph agent service)

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `OPENROUTER_API_KEY` | — | **Yes** | OpenRouter API key for all LLM calls |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | No | OpenRouter endpoint |
| `TAVILY_API_KEY` | — | No | Tavily API key (used by self-reflection agents for web search) |
| `LANGSMITH_API_KEY` | — | No | LangSmith key for tracing |
| `LANGSMITH_TRACING` | `true` | No | Enable/disable LangSmith tracing |
| `LANGSMITH_PROJECT` | `self-reflection-agent` | No | LangSmith project name |
| `MODEL_NAME` | `nvidia/nemotron-3-nano-30b-a3b:free` | No | Global default model (OpenRouter model string) |
| `PERSIST_RUNS` | `false` | No | Set to `true` to save research runs to PostgreSQL |
| `DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/research` | If `PERSIST_RUNS=true` | PostgreSQL connection string |
| `POSTGRES_PASSWORD` | — | Docker only | PostgreSQL superuser password |
| `DUCKLING_URL` | `http://localhost:8000` | No | Duckling date parser endpoint |
| `USE_MOCK_S2` | `false` | No | Use mock Semantic Scholar results (for testing) |
| `LOG_MODELS` | `false` | No | Print active model names on agent startup |

### Per-Node Model Overrides (Research Agent)

The Research Agent supports fine-grained model selection. Priority order (highest → lowest):

1. **Node-level** (e.g. `RESEARCH_PLANNER_MODEL`)
2. **Agent-level** (e.g. `RESEARCH_MODEL`)
3. **Global fallback** (`MODEL_NAME`)

| Variable | Node it applies to |
|----------|-------------------|
| `RESEARCH_MODEL` | All Research Agent nodes (agent-level override) |
| `RESEARCH_PLANNER_MODEL` | `extract_research_intent` |
| `RESEARCH_SYNTHESIZER_MODEL` | `synthesize` |
| `RESEARCH_FILTER_MODEL` | `normalize_queries` |
| `RESEARCH_TOPIC_EXTRACTOR_MODEL` | `extract_research_intent` |
| `RESEARCH_KEYWORD_EXPANDER_MODEL` | `generate_semantic_queries` |
| `RESEARCH_QUERY_GENERATOR_MODEL` | `generate_semantic_queries` |
| `RESEARCH_EMBEDDING_MODEL` | `rank_results_by_similarity` (default: `BAAI/bge-large-en-v1.5`) |

---

## Hiding Messages in the Chat

### Prevent Live Streaming

To stop a message from appearing while it streams, add the `langsmith:nostream` tag to the model:

```python
from langchain_anthropic import ChatAnthropic

model = ChatAnthropic().with_config(config={"tags": ["langsmith:nostream"]})
```

The message will still appear once the LLM call finishes, if it was saved to graph state.

### Hide Messages Permanently

To completely hide a message from the UI, prefix its `id` with `do-not-render-` before saving to state:

```python
result = model.invoke([messages])
result.id = f"do-not-render-{result.id}"
return {"messages": [result]}
```

The UI filters out any message whose `id` starts with `do-not-render-`.

---

## Rendering Artifacts

The chat UI supports rendering content in a side panel via the artifact system. Obtain the artifact context from `thread.meta.artifact`:

```tsx
export function useArtifact<TContext = Record<string, unknown>>() {
  const thread = useStreamContext<
    { messages: Message[]; ui: UIMessage[] },
    { MetaType: { artifact: [Component, Bag] } }
  >();
  return thread.meta?.artifact;
}
```

Then render content using the `Artifact` component:

```tsx
export function Writer(props: { title?: string; content?: string }) {
  const [Artifact, { open, setOpen }] = useArtifact();

  return (
    <>
      <div onClick={() => setOpen(!open)} className="cursor-pointer rounded-lg border p-4">
        <p className="font-medium">{props.title}</p>
      </div>
      <Artifact title={props.title}>
        <p className="p-4 whitespace-pre-wrap">{props.content}</p>
      </Artifact>
    </>
  );
}
```

---

## Going to Production

By default, the UI connects directly to the LangGraph backend from the browser. For production, you need to proxy requests server-side to avoid exposing API keys.

### Quickstart — API Passthrough

Set these variables in your `.env`:

```bash
NEXT_PUBLIC_ASSISTANT_ID="research_agent"
# Your deployed LangGraph server URL
LANGGRAPH_API_URL="https://my-agent.default.us.langgraph.app"
# Your website URL + /api (the proxy endpoint)
NEXT_PUBLIC_API_URL="https://my-website.com/api"
# Injected server-side — never expose to the browser
LANGSMITH_API_KEY="lsv2_..."
```

See the [LangGraph Next.js API Passthrough](https://www.npmjs.com/package/langgraph-nextjs-api-passthrough) docs for full details.

### Custom Authentication

For more control, implement custom auth in your LangGraph deployment and pass the token via headers:

```tsx
const streamValue = useTypedStream({
  apiUrl: process.env.NEXT_PUBLIC_API_URL,
  assistantId: process.env.NEXT_PUBLIC_ASSISTANT_ID,
  defaultHeaders: {
    Authentication: `Bearer ${yourTokenHere}`,
  },
});
```

---

## Extras — Additional Agents

The backend also exposes two self-reflection agents. These are simpler than the Research Agent and intended for writing/iteration tasks rather than academic retrieval.

### Self-Reflection Agent v1 (`self_reflection_agent`)

A 4-node iterative loop that optionally searches the web via Tavily before generating and refining an answer through reflection.

**Nodes:** `search_decision` → `web_search` *(optional)* → `generate` → `reflect` → *(loop or end)*

- **Best for:** Writing tasks, report drafts, iterative content improvement
- **Requires:** `TAVILY_API_KEY` (for web search)
- **Max iterations:** 1–10 (default 3)

### Self-Reflection Agent v2 (`self_reflection_agent_v2`)

A simplified 2-node loop (no web search) that generates and reflects until the answer is approved.

**Nodes:** `generate` → `reflect` → *(loop or end)*

- **Best for:** Tasks that don't need external context
- **Max iterations:** 1–10 (default 3)

To switch to either agent in the UI, change `NEXT_PUBLIC_ASSISTANT_ID` to `self_reflection_agent` or `self_reflection_agent_v2`.
