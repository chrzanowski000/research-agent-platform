# Overview

[← Home](Home.md)

**agents-self-reflect** is a multi-agent AI research platform. It combines a conversational web UI with LangGraph-powered AI agents that perform multi-step research tasks, then stores results for later review.

---

## What It Does

Users submit natural-language research queries through a chat interface. The platform routes the query to an AI agent (selectable per session), which:

1. Parses any date/time references (e.g., "papers from last 3 months")
2. Breaks the query into targeted sub-queries
3. Searches multiple sources: Semantic Scholar, Tavily (web), GitHub
4. Ranks results by semantic similarity to the original intent
5. Synthesizes a structured summary
6. Persists the run to a database for future retrieval

Multiple agent types are available, from research-focused pipelines to self-reflective reasoning loops.

---

## Key Capabilities

- **Natural language date parsing** — Duckling converts phrases like "last quarter" into ISO date ranges
- **Multi-source research** — Semantic Scholar, Tavily web search, GitHub (arXiv disabled by default)
- **Semantic ranking** — Results are ranked by cosine similarity using a local embedding model
- **Agent selection** — Users can choose between `research_agent`, `self_reflection_agent`, `self_reflection_agent_v2`
- **Persistent history** — Research queries and runs are stored in PostgreSQL and browsable via the UI
- **Configurable models** — Any node in the pipeline can use a different LLM, controlled via environment variables
- **LangSmith tracing** — Optional observability via LangSmith when `LANGSMITH_TRACING=true`

---

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 15, React 19, TypeScript, Tailwind CSS 4, Radix UI |
| Agent backend | Python 3.12, LangGraph, LangChain, OpenRouter (LLM gateway) |
| Persistence API | Python, FastAPI, SQLAlchemy |
| Date parsing | Rasa Duckling (Docker container) |
| Database | PostgreSQL 16 |
| Container runtime | Docker / Docker Compose (legacy), Kubernetes |
| Orchestration | Kustomize (dev/prod overlays), Helm chart |
| Secret management | 1Password CLI (`op run`) |
| Observability | LangSmith (optional) |
| CI/CD | GitHub Actions (EKS deploy) |

---

## High-Level Platform Summary

```
Browser
  └── chat-ui (Next.js :3000)
        ├── /api/* proxy → langgraph-api (:2024)
        └── /api/research/* proxy → persistence-api (:8001)

langgraph-api
  ├── research_agent     — 10-node research pipeline
  ├── self_reflection_agent v1 — generate/reflect/search
  └── self_reflection_agent v2 — generate/reflect (tool-use)
        ├── → Duckling (:8000)   date parsing
        ├── → Semantic Scholar   academic search
        ├── → Tavily             web search
        └── → PostgreSQL (:5432) run persistence

persistence-api
  └── → PostgreSQL (:5432)       query/run/source history
```

For detailed request flow and Mermaid diagrams, see [Manual: Architecture](Manual-Architecture.md).

---

## See Also

- [Applications](Applications.md) — Per-service details
- [Manual: Architecture](Manual-Architecture.md) — How data flows end-to-end
- [Manual: Configuration and Secrets](Manual-Configuration-and-Secrets.md) — Model selection and env vars
