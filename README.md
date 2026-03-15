# LangGraph Multi-Agent Research & Reflection System

This project contains three LangGraph agents plus a small FastAPI service for browsing persisted research runs. Each agent has a distinct purpose and graph topology.

---

## Project Structure

```
├── agents/
│   ├── self_reflection_agent.py      # Self-Reflection Agent (v1)
│   ├── self_reflection_agent_v2.py   # Self-Reflection Agent (v2) with tool use
│   └── research_agent.py             # Research Agent (plan-and-execute)
├── agent-chat-ui/                    # Next.js 15 chat interface
├── research_persistence_api/         # FastAPI service for persisted research queries/runs/sources
├── tests/                            # Test suite
├── docker-compose.yml                # Docker Compose setup
├── langgraph.json                    # LangGraph graph registry
├── config.py                         # Centralized configuration
└── requirements.txt                  # Python dependencies
```

---

# Agent 1: Research Agent (Plan-and-Execute)

**File:** `agents/research_agent.py`
**Purpose:** Perform deep research with structured query planning, date filtering, multi-source retrieval, semantic relevance filtering, and optional persistence.
**Best for:** Research queries that need search planning, source ranking, date constraints, and a synthesized brief.

## Graph Architecture

```
                         ┌─────────────────┐
                         │  parse_dates    │
                         └────────┬────────┘
                                  │
                         ┌────────▼──────────┐
                         │extract_research  │
                         │intent            │
                         └────────┬──────────┘
                                  │
                         ┌────────▼──────────────┐
                         │generate_semantic_    │
                         │queries               │
                         └────────┬──────────────┘
                                  │
                         ┌────────▼──────────────┐
                         │normalize_queries     │
                         └────────┬──────────────┘
                                  │
                         ┌────────▼──────────────┐
                         │apply_date_filter     │
                         └────────┬──────────────┘
                                  │
                         ┌────────▼──────────────┐
                         │execute_searches     │
                         └────────┬──────────────┘
                                  │
                         ┌────────▼──────────────┐
                         │validate_date_range   │
                         └────────┬──────────────┘
                                  │
                         ┌────────▼──────────────┐
                         │rank_results_by_      │
                         │similarity            │
                         └────────┬──────────────┘
                                  │
                         ┌────────▼──────────────┐
                         │synthesize            │
                         └────────┬──────────────┘
                                  │
                                  ▼
                                [END]
```

## Pipeline Nodes

| Node | Purpose |
|------|---------|
| `parse_dates` | Extracts date constraints from the user query via [Duckling](https://github.com/facebook/duckling); produces `start_date`, `end_date` |
| `extract_research_intent` | Analyzes query to extract structured intent: `problem_domains`, `methods`, `related_concepts` (3–5 phrases each) |
| `generate_semantic_queries` | Generates semantic search queries from the structured intent |
| `normalize_queries` | Normalizes and deduplicates generated queries before search-plan assembly |
| `apply_date_filter` | Applies extracted date constraints to the planned searches |
| `execute_searches` | Runs the search plan against enabled sources and deduplicates results |
| `validate_date_range` | Post-retrieval check that removes out-of-range results when date constraints are present |
| `rank_results_by_similarity` | Embedding cosine-similarity filter (threshold 0.1) using `BAAI/bge-large-en-v1.5`; deduplicates by title |
| `synthesize` | Synthesizes kept results into a structured research brief (Summary / Key Findings / Sources) |
| `persist_run` | Optionally stores the final query, run, sources, and disk artifacts when `PERSIST_RUNS=true` |

## Running the Research Agent

### Prerequisites

1. **Duckling service** (for date parsing):
   ```bash
   docker compose up duckling
   ```
   Default URL: `http://localhost:8000` (override with `DUCKLING_URL` environment variable).

2. **Environment variables:**
   ```bash
   export OPENROUTER_API_KEY="your_openrouter_api_key"
   export TAVILY_API_KEY="your_tavily_api_key"  # optional, currently unused
   export LANGSMITH_API_KEY="your_langsmith_api_key"  # optional, for tracing
   ```

### Command Line Usage

```bash
# Basic query
python agents/research_agent.py "quantum error correction using machine learning"

# With date constraint (natural language)
python agents/research_agent.py "nuclear fusion energy from 2023 to 2024"

# With query count limit
python agents/research_agent.py "reinforcement learning" --query-count 5

# Enable debug logging
LOG_MODELS=true python agents/research_agent.py "topic"
```

### Python API

```python
from agents.research_agent import run_agent

result = run_agent(
    topic="quantum parameter estimation using machine learning",
    query_count=5
)

print(result["synthesis"])        # Final research brief
print(result["search_results"])   # List of papers kept after filtering
print(result["date_filter"])      # Extracted date constraints (if any)
```

---

# Agent 2: Self-Reflection Agent (v1)

**File:** `agents/self_reflection_agent.py`
**Purpose:** Iteratively generate and refine answers through reflection feedback loops.
**Best for:** Writing tasks, report generation, iterative improvement of content.

## Graph Architecture

```
                    ┌─────────────────────┐
                    │  search_decision    │◄───────────────────┐
                    └──────────┬──────────┘                    │
                               │                               │
                      search_needed?                           │
                      ┌────yes─┴──no────┐                      │
                      ▼                 ▼                      │
                ┌──────────────┐  ┌──────────────┐             │
                │  web_search  │─►│  generate    │             │
                └──────────────┘  └──────┬───────┘             │
                                         ▼                     │
                                 ┌──────────────┐              │
                                 │  reflect     │              │
                                 └──────┬───────┘              │
                                        │                      │
                              approved / max_iterations?       │
                              ┌───yes──┴──no─────────────────►┘
                              ▼
                            [END]
```

## Pipeline Nodes

| Node | Purpose |
|------|---------|
| `search_decision` | LLM decides whether web search is needed for current task; respects `max_web_searches` budget per turn |
| `web_search` | *(optional)* If needed, [Tavily](https://tavily.com) fetches external context with exponential-backoff retry |
| `generate` | Generate or improve draft answer, informed by search context and reflection feedback; PII masking on inputs/outputs |
| `reflect` | Review draft for correctness, completeness, clarity; approve or provide actionable feedback for loop repeat (max `max_iterations`, default 3) |

## Running Self-Reflection Agent (v1)

### Prerequisites

1. **Environment variables:**
   ```bash
   export OPENROUTER_API_KEY="your_openrouter_api_key"
   export TAVILY_API_KEY="your_tavily_api_key"
   export LANGSMITH_API_KEY="your_langsmith_api_key"  # optional
   ```

### Command Line Usage

```bash
# Run agent (enters interactive chat)
python agents/self_reflection_agent.py

# Set model override
MODEL_NAME="gpt-4" python agents/self_reflection_agent.py
```

### Python API

```python
from agents.self_reflection_agent import run_agent

result = run_agent(
    task="Write a comprehensive summary of quantum computing",
    max_iterations=3
)

print(result["output"])       # Final generated answer
print(result["web_context"])  # Search results (if any)
print(result["iterations"])   # Number of iterations used
```

### Enable Debug Output

```bash
LOG_MODELS=true python agents/self_reflection_agent.py
```

---

# Agent 3: Self-Reflection Agent (v2)

**File:** `agents/self_reflection_agent_v2.py`
**Purpose:** Enhanced reflection agent with tool use capabilities.
**Best for:** Complex tasks requiring external tool integration, structured workflows.

## Graph Architecture

```
                    ┌──────────────────────┐
                    │  router / dispatcher │◄─────────────────┐
                    └──────────┬───────────┘                  │
                               │                              │
                    ┌──────────┴──────────┐                   │
                    ▼                     ▼                   │
            ┌──────────────┐      ┌──────────────┐            │
            │  tool_agent  │      │  generate    │            │
            └──────┬───────┘      └──────┬───────┘            │
                   │                     │                    │
                   └────────────┬────────┘                    │
                                ▼                            │
                        ┌──────────────┐                     │
                        │  reflect     │                     │
                        └──────┬───────┘                     │
                               │                            │
                     approved / max_iterations?             │
                     ┌───yes──┴──no──────────────────────►┘
                     ▼
                   [END]
```

## Running Self-Reflection Agent (v2)

### Prerequisites

Same as v1 (see above).

### Command Line Usage

```bash
python agents/self_reflection_agent_v2.py
```

### Python API

```python
from agents.self_reflection_agent_v2 import run_agent

result = run_agent(
    task="Research and summarize recent ML papers",
    max_iterations=3
)

print(result["output"])       # Final answer with tool results
print(result["tool_calls"])   # Tools used during execution
```

---

# Research Persistence API

**Package:** `research_persistence_api`
**Purpose:** Browse and delete persisted research queries, runs, and sources written by the research agent.
**Best for:** Inspecting prior research runs from the UI or directly over HTTP.

## Endpoints

| Method | Path | Purpose |
|------|---------|---------|
| `GET` | `/research/queries` | List saved queries with run counts and last-run timestamps |
| `GET` | `/research/queries/{query_id}` | Get one query plus its runs |
| `GET` | `/research/runs/{run_id}` | Get one run plus its sources |
| `DELETE` | `/research/queries/{query_id}` | Delete a query and its persisted artifacts |

---

# Testing

## Running All Tests

```bash
# Using conda environment
conda run -n agents python -m pytest tests/ -v

# Or activate environment first
conda activate agents
pytest tests/ -v
```

## Running Specific Test Suites

```bash
# Research agent tests only
conda run -n agents python -m pytest tests/test_research_date_parser.py -v

# Specific test
conda run -n agents python -m pytest tests/test_research_date_parser.py::test_extract_research_intent_returns_valid_schema -v
```

## Test Structure

```
tests/
├── test_agent.py                 # Self-reflection agent tests
├── test_persistence.py           # Persistence model and storage tests
└── test_research_date_parser.py  # Research date parsing and pipeline tests
```

## Coverage Summary

- **parse_dates:** interval, single year, year range, no result, HTTP error, empty messages
- **apply_date_filter:** date embedding, no date filter, respects query count limits, blocks on empty queries
- **validate_date_range:** keeps in-range, removes out-of-range, no filter, all removed, non-arXiv, unparseable IDs
- **Semantic pipeline:** intent extraction, semantic queries with domains, query deduplication, embedding-based filtering, state propagation

---

# Docker Deployment

## Quick Start (Recommended)

### Option A: 1Password Integration (No Secrets on Disk)

```bash
# Prerequisites: op CLI installed and signed in
op run --env-file=.env_tpl -- docker compose up --build --remove-orphans
```

Services available:
- **Chat UI:** http://localhost:3000
- **LangGraph API:** http://localhost:2024
- **Research Persistence API:** http://localhost:8001
- **LangSmith Studio:** https://smith.langchain.com/studio/?baseUrl=http://localhost:2024

### Option B: Manual Environment Variables

```bash
# Set required variables
export OPENROUTER_API_KEY="your_key"
export TAVILY_API_KEY="your_key"
export LANGSMITH_API_KEY="your_key"

# Start services
docker compose up --build --remove-orphans
```

Then open **http://localhost:3000** in your browser.

## Services

| Service | URL | Purpose |
|---------|-----|---------|
| Chat UI | http://localhost:3000 | Next.js 15 frontend for chat |
| LangGraph API | http://localhost:2024 | Agent execution backend |
| Research Persistence API | http://localhost:8001 | FastAPI service for persisted research data |
| PostgreSQL | localhost:5432 | Research persistence database |
| Duckling | http://localhost:8000 | Date parser service |
| LangSmith Studio | https://smith.langchain.com/studio/?baseUrl=http://localhost:2024 | Agent tracing & debugging |

## Stopping Docker

```bash
docker compose down
```

## Viewing Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f ui      # Chat UI
docker compose logs -f agent   # LangGraph API
docker compose logs -f research-persistence-api
docker compose logs -f duckling
```

---

# Setup & Installation

## Local Development

### 1. Create Python Environment

```bash
# Using conda
conda create -n agents python=3.11
conda activate agents

# Or using venv
python -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment

Create `.env` file (or set environment variables):

```bash
# Required
OPENROUTER_API_KEY=your_openrouter_key
TAVILY_API_KEY=your_tavily_key

# Optional but recommended
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=agents-self-reflect

# Optional model overrides
MODEL_NAME=nvidia/nemotron-3-nano-30b-a3b:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Optional Duckling override
DUCKLING_URL=http://localhost:8000

# Database (PostgreSQL). For local dev, start postgres separately or use docker compose up postgres.
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/research
```

### 4. Start Duckling (for Research Agent)

```bash
docker compose up duckling
```

## Running LangSmith Studio Locally

```bash
# In project root with dependencies installed
langgraph dev

# Studio UI will be available at http://localhost:8000
# Select graph "self_reflection_agent" or "research_agent" to debug
```

---

# Configuration

All configuration is centralized in `config.py` and loaded from environment variables.

## Model Selection Strategy

Priority (highest → lowest) for any given node:

1. `<AGENT>_<NODE>_MODEL` (e.g., `RESEARCH_PLANNER_MODEL`)
2. `<AGENT>_MODEL` (e.g., `RESEARCH_MODEL`)
3. `MODEL_NAME` (global fallback)

Example:
```bash
# Use specific model for research planner node only
export RESEARCH_PLANNER_MODEL="gpt-4"

# Use same model for all research agent nodes
export RESEARCH_MODEL="claude-3-opus"

# Fallback for any unspecified nodes
export MODEL_NAME="nvidia/nemotron-3-nano-30b-a3b:free"
```

---

# API Reference

## Research Agent

```python
from agents.research_agent import run_agent

def run_agent(topic: str, query_count: int = 5) -> ResearchState:
    """
    Execute research agent on a topic.

    Args:
        topic: Research question or query (required, non-empty)
        query_count: Number of search queries to generate and run (default 5)

    Returns:
        ResearchState with:
            - synthesis: Final research brief (str)
            - search_results: List of papers kept after filtering (list[dict])
            - date_filter: Extracted date constraints (dict)
            - blocked: Whether agent encountered error (bool)
            - block_reason: Error message if blocked (str)

    Raises:
        ValueError: If topic is empty
    """
```

## Self-Reflection Agent (v1)

```python
from agent import run_agent

def run_agent(task: str, max_iterations: int = 3) -> dict:
    """
    Execute self-reflection agent on a task.

    Args:
        task: Task description or prompt (required, non-empty)
        max_iterations: Max reflection loops (1-10, default 3)

    Returns:
        dict with:
            - output: Final generated answer (str)
            - web_context: Search results used (str)
            - iterations: Actual iterations used (int)
    """
```

---

# Troubleshooting

## Research Agent Issues

**"Duckling service not available"**
```bash
docker compose up duckling
# Verify at http://localhost:8000/parse
```

**"No papers found"**
- Refine your query (use specific technical terms)
- Try without date constraints
- Check arXiv has papers on your topic

**"All results filtered out"**
- Lower similarity threshold (edit `SIMILARITY_THRESHOLD` in `agents/research_agent.py`, currently 0.1)
- Use fewer, broader search terms
- Note: Embedding-based filtering uses local `BAAI/bge-large-en-v1.5` model (no API needed)

## Docker Issues

**Port already in use**
```bash
# Find and kill process on port 3000
lsof -i :3000
kill -9 <PID>

# Or stop the conflicting local process and retry
lsof -i :2024
```

**Image build failures**
```bash
# Clean and rebuild
docker compose down
docker system prune
docker compose up --build
```

## Test Failures

**Import errors**
```bash
# Reinstall dependencies
conda run -n agents pip install -r requirements.txt --force-reinstall
```

**Network errors in tests**
- Ensure `OPENROUTER_API_KEY` is set
- Check internet connection
- Run `conda run -n agents python -m pytest tests/ -v` with verbose output

---

# Contributing

Before contributing:

1. Run tests: `conda run -n agents python -m pytest tests/ -v`
2. Check code style (follow existing patterns)
3. Update tests for new functionality
4. Update this README if adding new agents or features

---

# License

See LICENSE file.
