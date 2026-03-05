# Simple LangGraph Agent with Self-Reflection

This project contains a minimal LangGraph agent that:
1. Generates an answer.
2. Reflects on the answer.
3. Loops to improve it until approved or max iterations are reached.

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
pip install -r requirements.rtxt
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
