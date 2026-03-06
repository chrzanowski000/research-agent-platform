# LangGraph Self-Reflection Agent

This project contains a LangGraph agent that iteratively generates and improves answers through a four-node graph:

1. **Search Decision** — an LLM decides whether a web search is needed for the current task and iteration, respecting a configurable `max_web_searches` budget per turn. On each new conversation turn all per-turn counters reset automatically.
2. **Web Search** *(optional)* — if search is required, [Tavily](https://tavily.com) fetches up-to-date external context (with exponential-backoff retry on transient errors) and appends it to the agent's `web_context`.
3. **Generate** — a generation agent writes or improves the draft answer, informed by accumulated `web_context` and any prior reviewer feedback. Inputs and outputs are filtered through PII middleware that masks emails, credit card numbers, IP addresses, and MAC addresses.
4. **Reflect** — a reflection agent reviews the draft for correctness, completeness, and clarity. If approved, the answer is finalised; otherwise it provides actionable feedback and the loop repeats — up to `max_iterations` times (default 3, configurable 1–10).

```
        ┌─────────────────┐
        │  search_decision │◄──────────────────┐
        └────────┬────────┘                    │
                 │                             │
        search_needed?                         │
        ┌────yes─┴──no────┐                    │
        ▼                 ▼                    │
  ┌───────────┐     ┌──────────┐               │
  │ web_search│────►│ generate │               │
  └───────────┘     └────┬─────┘               │
                         ▼                     │
                   ┌──────────┐                │
                   │ reflect  │                │
                   └────┬─────┘                │
                        │                      │
               approved / max_iterations?      │
               ┌───yes──┴──no─────────────────►┘
               ▼
             [END]
```

## Docker (recommended)

### Option A — 1Password (recommended, nothing written to disk)

Secrets are injected directly from 1Password as environment variables — no file is ever created:

```bash
op run --env-file=.env.tpl -- docker compose up --build --remove-orphans
```

> Requires `op` CLI signed in. `.env.tpl` contains only 1Password references, safe to commit.

### Option B — manual env vars

```bash
export OPENROUTER_API_KEY=...
export TAVILY_API_KEY=...
export LANGSMITH_API_KEY=...
docker compose up --build
```

Then open **http://localhost:3000** in your browser.

Services started:
| Service | URL |
|---------|-----|
| Chat UI (Next.js) | http://localhost:3000 |
| LangGraph API | http://localhost:2024 |
| LangSmith | https://smith.langchain.com/studio/?baseUrl=http://localhost:2024 |


To stop:
```bash
docker compose down
```

## Files
- `agent.py`: LangGraph workflow with generate/reflect loop.
- `requirements.txt`: Python dependencies.

## Setup
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment
Set your OpenRouter API key:
```bash
export OPENROUTER_API_KEY="your_openrouter_api_key"
```

Optional model override (default: `nvidia/nemotron-3-nano-30b-a3b:free`):
```bash
export MODEL_NAME="nvidia/nemotron-3-nano-30b-a3b:free"
```

Optional base URL override (default shown):
```bash
export OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"
```

LangSmith tracing:
```bash
export LANGSMITH_API_KEY="your_langsmith_api_key"
export LANGSMITH_TRACING="true"
export LANGSMITH_PROJECT="self-reflection-agent"
```

Notes:
- If `LANGSMITH_API_KEY` is set, tracing is auto-enabled when no tracing flag is explicitly set.
- Legacy `LANGCHAIN_API_KEY` / `LANGCHAIN_PROJECT` are also supported.

## Run
```bash
python agent.py
```

You can also import and call `run_agent(task, max_iterations=3)`.

## Connect to LangSmith Studio
This repo now includes `langgraph.json` pointing to `agent.py:app`.

1. Install dependencies:
```bash
pip install -r requirements.txt
```
2. Fill keys in `.env`:
```bash
OPENROUTER_API_KEY=...
TAVILY_API_KEY=...
LANGSMITH_API_KEY=...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=self-reflection-agent
```
3. Start local dev server for Studio:
```bash
langgraph dev
```
4. Open Studio UI from the URL shown in terminal and select graph `self_reflection_agent`.

## Chat UI (agent-chat-ui)

This repo includes `agent-chat-ui/` — a Next.js app that provides a chat interface for the LangGraph agent.

### Setup
```bash
cd agent-chat-ui
pnpm install
```

### Configure
Copy the example env file and set the values:
```bash
cp agent-chat-ui/.env.example agent-chat-ui/.env
```

Required variables:
```
NEXT_PUBLIC_API_URL=http://localhost:2024
NEXT_PUBLIC_ASSISTANT_ID=self_reflection_agent
```

> `NEXT_PUBLIC_API_URL` should point to your running `langgraph dev` server (default: `http://localhost:2024`).

### Run
```bash
cd agent-chat-ui
pnpm dev
```

The UI will be available at `http://localhost:3000`.