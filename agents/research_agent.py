"""Research Agent: Plan-and-Execute deep research with web, arXiv, and GitHub search."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Annotated, Any, Literal

import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, MessagesState, StateGraph
from langsmith import traceable
from pydantic import Field
from tavily import TavilyClient
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import Config, ConfigError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

try:
    cfg = Config.from_env()
except ConfigError as _cfg_err:
    logger.warning("Config incomplete: %s — some features may be unavailable.", _cfg_err)
    cfg = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

_DEFAULT_MAX_SEARCHES = 5


class ResearchState(MessagesState, total=False):
    """Runtime state for the research agent graph.

    ``messages`` is inherited from MessagesState and carries the full
    conversation history used by the LangSmith Studio chat interface.
    """

    turn: Annotated[int, Field(
        default=0,
        description="Number of conversation turns completed. Used to detect new turns and reset per-turn counters.",
    )]
    topic: Annotated[str, Field(
        default="",
        description="The research topic extracted from the latest HumanMessage.",
    )]
    search_plan: Annotated[list[dict], Field(
        default_factory=list,
        description="Ordered list of search tasks, each with 'source' and 'query' keys.",
    )]
    search_results: Annotated[list[dict], Field(
        default_factory=list,
        description="Accumulated search results from all sources.",
    )]
    synthesis: Annotated[str, Field(
        default="",
        description="Final synthesized research brief produced by the synthesis node.",
    )]
    done: Annotated[bool, Field(
        default=False,
        description="True when synthesis is complete.",
    )]
    blocked: Annotated[bool, Field(
        default=False,
        description="True when the agent was stopped due to an error.",
    )]
    block_reason: Annotated[str, Field(
        default="",
        description="Human-readable explanation of why the agent was blocked.",
    )]
    max_searches: Annotated[int, Field(
        default=_DEFAULT_MAX_SEARCHES,
        description="Maximum number of search queries to execute per run.",
        ge=1,
        le=10,
    )]

# ---------------------------------------------------------------------------
# Model (singleton via lru_cache)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_model() -> ChatOpenAI:
    """Return a cached ChatOpenAI instance configured from env."""
    if cfg is None:
        raise ConfigError("Agent config not loaded — check your environment variables.")
    return ChatOpenAI(
        model=cfg.model_name,
        temperature=0,
        base_url=cfg.base_url,
        api_key=cfg.openrouter_api_key,
        timeout=cfg.request_timeout,
    )


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _tavily_search(query: str) -> list[dict]:
    """Run a Tavily web search and return result snippets."""
    if not cfg or not cfg.tavily_api_key:
        raise ConfigError("TAVILY_API_KEY is not set")
    client = TavilyClient(api_key=cfg.tavily_api_key)
    resp = client.search(query=query, topic="general", max_results=3, search_depth="advanced")
    results = []
    for item in resp.get("results", []):
        results.append({
            "source": "web",
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
        })
    return results


def _arxiv_search(query: str) -> list[dict]:
    """Search arXiv for academic papers using the public API."""
    try:
        encoded = httpx.URL(f"https://export.arxiv.org/api/query?search_query=all:{query}&max_results=3&sortBy=relevance")
        resp = httpx.get(str(encoded), timeout=15)
        resp.raise_for_status()
        # Parse atom XML minimally
        entries = re.findall(
            r"<entry>(.*?)</entry>", resp.text, re.DOTALL
        )
        results = []
        for entry in entries[:3]:
            title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
            summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            link_m = re.search(r'<id>(.*?)</id>', entry, re.DOTALL)
            results.append({
                "source": "arxiv",
                "title": title_m.group(1).strip() if title_m else "",
                "url": link_m.group(1).strip() if link_m else "",
                "snippet": summary_m.group(1).strip()[:500] if summary_m else "",
            })
        return results
    except Exception as exc:
        logger.warning("arXiv search failed: %s", exc)
        return []


def _github_search(query: str) -> list[dict]:
    """Search GitHub repositories using the public search API."""
    try:
        resp = httpx.get(
            "https://api.github.com/search/repositories",
            params={"q": query, "sort": "stars", "per_page": 3},
            headers={"Accept": "application/vnd.github+json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for repo in data.get("items", [])[:3]:
            results.append({
                "source": "github",
                "title": repo.get("full_name", ""),
                "url": repo.get("html_url", ""),
                "snippet": repo.get("description", "") or "",
            })
        return results
    except Exception as exc:
        logger.warning("GitHub search failed: %s", exc)
        return []


def _run_search(source: str, query: str) -> list[dict]:
    """Dispatch to the correct search backend."""
    if source == "web":
        return _tavily_search(query)
    if source == "arxiv":
        return _arxiv_search(query)
    if source == "github":
        return _github_search(query)
    logger.warning("Unknown search source: %s", source)
    return []

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def plan_research(state: ResearchState) -> dict:
    """Extract the topic and generate a structured search plan.

    Detects new turns (new HumanMessage) and resets per-turn state.
    Asks the LLM to return a JSON search plan with up to max_searches tasks.
    """
    messages = state.get("messages", [])
    human_msg_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    current_turn = state.get("turn", 0)

    reset: dict = {}
    if human_msg_count > current_turn:
        topic = next(
            (m.content if isinstance(m.content, str) else str(m.content)
             for m in reversed(messages) if isinstance(m, HumanMessage)),
            "",
        )
        reset = {
            "turn": human_msg_count,
            "topic": topic,
            "search_plan": [],
            "search_results": [],
            "synthesis": "",
            "done": False,
            "blocked": False,
            "block_reason": "",
        }
        logger.info("New research turn %d: topic=%r", human_msg_count, topic[:80])
    else:
        topic = state.get("topic", "")

    max_searches = state.get("max_searches", _DEFAULT_MAX_SEARCHES)

    plan_prompt = (
        f"You are a research planner. Given the topic below, create a structured search plan.\n"
        f"Topic: {topic}\n\n"
        f"Return a JSON array of up to {max_searches} search tasks. Each task must have:\n"
        '  - "source": one of "web", "arxiv", "github"\n'
        '  - "query": a concise search query string\n\n'
        "Focus: cover fundamentals, recent research, and practical implementations.\n"
        "Return ONLY the JSON array, no other text."
    )

    logger.info("Planning research for topic: %r", topic[:80])
    try:
        raw = get_model().invoke([HumanMessage(content=plan_prompt)]).content
        if not isinstance(raw, str):
            raw = str(raw)
        # Extract JSON array from response
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            plan: list[dict] = json.loads(match.group())
        else:
            plan = []
        # Validate structure; drop malformed entries
        valid_sources = {"web", "arxiv", "github"}
        plan = [
            p for p in plan
            if isinstance(p, dict) and p.get("source") in valid_sources and p.get("query")
        ][:max_searches]
    except Exception as exc:
        logger.error("Failed to parse search plan: %s", exc)
        plan = [{"source": "web", "query": topic}]

    logger.info("Search plan: %d tasks", len(plan))
    return {**reset, "search_plan": plan}


def execute_searches(state: ResearchState) -> dict:
    """Execute all search tasks from the plan and collect results."""
    plan: list[dict] = state.get("search_plan", [])
    if not plan:
        logger.warning("No search plan found — skipping searches")
        return {}

    all_results: list[dict] = []
    for task in plan:
        source = task.get("source", "web")
        query = task.get("query", "")
        logger.info("Searching [%s]: %r", source, query[:80])
        try:
            results = _run_search(source, query)
            all_results.extend(results)
        except Exception as exc:
            logger.error("Search failed [%s] %r: %s", source, query[:60], exc)

    logger.info("Collected %d search results total", len(all_results))
    return {"search_results": all_results}


def synthesize_research(state: ResearchState) -> dict:
    """Synthesize all search results into a structured research brief."""
    topic = state.get("topic", "")
    results: list[dict] = state.get("search_results", [])

    if not results:
        logger.warning("No search results to synthesize")
        brief = f"No results were found for: {topic}"
        return {
            "synthesis": brief,
            "done": True,
            "messages": [AIMessage(content=brief)],
        }

    # Format results for the LLM
    formatted: list[str] = []
    for i, r in enumerate(results, 1):
        formatted.append(
            f"[{i}] ({r.get('source', '?')}) {r.get('title', 'No title')}\n"
            f"    URL: {r.get('url', '')}\n"
            f"    {r.get('snippet', '')[:400]}"
        )
    results_text = "\n\n".join(formatted)

    synthesis_prompt = (
        f"You are a research analyst. Synthesize the following search results into a structured brief.\n\n"
        f"Research topic: {topic}\n\n"
        f"Search results:\n{results_text}\n\n"
        "Write a concise research brief with these sections:\n"
        "## Summary\nA 2-3 sentence overview of the topic.\n\n"
        "## Key Findings\nBullet points of the most important insights.\n\n"
        "## Practical Approaches\nMost relevant tools, libraries, or methods found.\n\n"
        "## Sources\nNumbered list of the most useful sources with URLs.\n\n"
        "Be factual, concise, and cite sources by number."
    )

    logger.info("Synthesizing %d results for topic: %r", len(results), topic[:80])
    try:
        raw = get_model().invoke([HumanMessage(content=synthesis_prompt)]).content
        brief = raw.strip() if isinstance(raw, str) else str(raw).strip()
    except Exception as exc:
        logger.error("Synthesis failed: %s", exc)
        brief = f"Synthesis failed: {exc}\n\nRaw results collected:\n{results_text}"

    return {
        "synthesis": brief,
        "done": True,
        "messages": [AIMessage(content=brief)],
    }


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def route_after_plan(state: ResearchState) -> Literal["execute_searches", "__end__"]:
    """Route after planning: to search execution or end if blocked."""
    if state.get("blocked", False):
        return "__end__"
    if not state.get("search_plan"):
        return "__end__"
    return "execute_searches"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Build and compile the LangGraph research agent graph."""
    graph = StateGraph(ResearchState)
    graph.add_node("plan_research", plan_research)
    graph.add_node("execute_searches", execute_searches)
    graph.add_node("synthesize", synthesize_research)

    graph.set_entry_point("plan_research")
    graph.add_conditional_edges(
        "plan_research",
        route_after_plan,
        {"execute_searches": "execute_searches", "__end__": END},
    )
    graph.add_edge("execute_searches", "synthesize")
    graph.add_edge("synthesize", END)

    return graph.compile()


app = build_graph()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@traceable(
    name="run_research_agent",
    run_type="chain",
    tags=["research", "plan-and-execute"],
    metadata={"agent_type": "research", "version": "1.0"},
)
def run_agent(topic: str, max_searches: int = _DEFAULT_MAX_SEARCHES) -> ResearchState:
    """Run the research agent on a topic.

    Args:
        topic: The research question or topic. Must be non-empty.
        max_searches: Maximum number of search queries to run (1-10, default 5).

    Returns:
        Final ResearchState with synthesis, search_results, and metadata.

    Raises:
        ValueError: If topic is empty or max_searches is out of range.
    """
    if not topic or not topic.strip():
        raise ValueError("Topic must be a non-empty string")
    if not 1 <= max_searches <= 10:
        raise ValueError("max_searches must be between 1 and 10")

    logger.info("Starting research agent: topic=%r max_searches=%d", topic[:80], max_searches)
    return app.invoke({
        "topic": topic.strip(),
        "max_searches": max_searches,
        "messages": [HumanMessage(content=topic.strip())],
    })
