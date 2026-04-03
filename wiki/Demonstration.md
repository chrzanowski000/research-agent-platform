# Demonstration

[← Home](Home) | [Overview](Overview) | [Agent Graphs](Manual-Agent-Graphs)

This page walks through a complete end-to-end demonstration of the platform.

---

## Prerequisites

The platform must be running. Choose one:

- **Docker Compose (quickest):** see [Docker Deployment](Manual-Deployment-Docker)
- **Kubernetes (recommended):** see [Kubernetes Deployment](Manual-Deployment-Kubernetes)

Once running, open **http://localhost:3000** (or **http://agent.local** with Ingress).

---

## Live Demo Checklist

Run through these in order:

- [ ] Platform is up — all services healthy
- [ ] Chat UI loads at http://localhost:3000
- [ ] Agent selector shows all three agents
- [ ] Submit a research query with the `research_agent`
- [ ] Observe streaming response in the UI
- [ ] Show structured output: Summary, Key Findings, Sources
- [ ] Demonstrate date filtering with a temporal prompt
- [ ] Switch to `self_reflection_agent` and show the iteration loop
- [ ] Open the research history panel (if `PERSIST_RUNS=true`)
- [ ] Show a past run with sources
- [ ] Open LangSmith Studio (optional) to show traces

---

## Demo Scenario A — Academic Research with Date Filter

**Agent:** `research_agent`

**Prompt:**
```
Find papers on transformer architectures for time series forecasting from 2023 to 2024
```

**What happens step by step:**

1. `parse_dates` — Duckling extracts `start_date: 2023-01-01`, `end_date: 2024-12-31`
2. `extract_research_intent` — LLM identifies problem domains, methods, related concepts
3. `generate_semantic_queries` — 5 keyword queries generated (e.g., "transformer time series forecasting")
4. `normalize_queries` — Queries deduplicated and cleaned
5. `apply_date_filter` — Search plan assembled (5 × semantic_scholar tasks)
6. `execute_searches` — Semantic Scholar API called for each query
7. `rank_results_by_similarity` — Results re-ranked by cosine similarity to original query; low-similarity results dropped
8. `synthesize` — LLM writes a structured brief: Summary + Key Findings + Sources
9. `persist_run` — (if enabled) Run saved to PostgreSQL + disk

**Expected output format:**
```markdown
## Summary
3-sentence overview of transformer approaches for time series...

## Key Findings
- Finding 1 with date noted (e.g., "Published 2023-06")
- Finding 2 ...

## Sources
1. **Title** — Authors — 2023-04 — https://arxiv.org/...
2. ...
```

`TODO: insert screenshot of chat UI with research results`

---

## Demo Scenario B — Self-Reflection Loop

**Agent:** `self_reflection_agent` (v1)

**Prompt:**
```
Explain the difference between RAG and fine-tuning for LLMs, with pros and cons of each
```

**What happens step by step:**

1. `search_decision` — LLM decides whether web search is needed
2. `web_search` (optional) — Tavily fetches relevant web snippets
3. `generate` — First draft written using web context
4. `reflect` — Reviewer evaluates correctness, completeness, clarity
   - If `APPROVED` → done
   - If feedback provided → loop back to `search_decision`
5. Repeats up to `max_iterations` times (default: 3)

**What to highlight:**
- The iteration counter visible in LangSmith traces
- Each loop produces a progressively better draft
- PII masking: try entering a fake email address in the prompt to see it masked

`TODO: insert screenshot of self-reflection agent output with iteration count`

---

## Demo Scenario C — Research History

**Requires:** `PERSIST_RUNS=true` and a prior completed run

1. Open the chat history panel in the UI
2. Select a past research query
3. Expand to see individual runs and their sources

`TODO: insert screenshot of research history panel`

---

## Demo Scenario D — Observability via LangSmith

**Requires:** `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` set

1. Run any research query
2. Open https://smith.langchain.com
3. Navigate to project `self-reflection-agent`
4. Find the trace — inspect node-by-node inputs and outputs
5. Show token counts, latencies, and tool calls per node

`TODO: insert screenshot of LangSmith trace for research_agent`

---

## Example Prompts by Agent

### research_agent
```
Recent advances in retrieval-augmented generation 2024
```
```
Papers on diffusion models for protein structure prediction in the last 6 months
```
```
State of the art in LLM reasoning benchmarks from 2023 to 2025
```
```
GitHub repositories for open-source vector databases with most stars
```

### self_reflection_agent (v1)
```
What are the trade-offs between PostgreSQL and MongoDB for high-write workloads?
```
```
Write a technical summary of how transformer attention mechanisms work
```

### self_reflection_agent_v2
```
Explain gradient descent in 3 paragraphs for a junior engineer
```
```
List the most important considerations when designing a REST API
```

---

## What Can Be Demonstrated

| Area | What to show |
|------|-------------|
| UI | Chat interface, agent selector, preset prompts, streaming output |
| Agent execution | Real-time streaming, node-by-node progress visible in logs |
| Date filtering | Natural language dates converted to ISO ranges by Duckling |
| Semantic ranking | Cosine similarity filtering removes off-topic results |
| Persistence | Research history, run detail, source list (requires `PERSIST_RUNS=true`) |
| Observability | LangSmith traces with per-node latency and tokens (requires tracing enabled) |
| Safety | PII blocking in self-reflection agents (email/credit card masking) |

`TODO: insert screenshot of Kubernetes deployment or logs`

---

## See Also

- [Manual: Agent Graphs](Manual-Agent-Graphs) — How each agent works internally
- [Manual: Architecture](Manual-Architecture) — Full request flow
- [Manual: Configuration and Secrets](Manual-Configuration-and-Secrets) — Enable persistence and tracing
