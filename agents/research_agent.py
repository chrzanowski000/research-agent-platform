"""Research Agent: Plan-and-Execute deep research with web, arXiv, and GitHub search."""
from __future__ import annotations

import calendar
import json
import logging
import re
from functools import lru_cache
from typing import Annotated, Literal

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
    cfg.log_models()
except ConfigError as _cfg_err:
    logger.warning("Config incomplete: %s — some features may be unavailable.", _cfg_err)
    cfg = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

_DEFAULT_MAX_SEARCHES = 5

# TODO: remove this constraint when ready to use all sources
_ALLOWED_SOURCES: set[str] | None = {"arxiv"}  # set to None to allow all sources


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
    date_filter: Annotated[dict, Field(
        default_factory=dict,
        description="Extracted date/year constraints from the topic. Keys: 'year' (int) or 'from'/'to' (int).",
    )]

# ---------------------------------------------------------------------------
# Models (one cached instance per node)
# ---------------------------------------------------------------------------


def _make_model(model_name: str) -> ChatOpenAI:
    if cfg is None:
        raise ConfigError("Agent config not loaded — check your environment variables.")
    return ChatOpenAI(
        model=model_name,
        temperature=0,
        base_url=cfg.base_url,
        api_key=cfg.openrouter_api_key,
        timeout=cfg.request_timeout,
    )


@lru_cache(maxsize=1)
def get_planner_model() -> ChatOpenAI:
    """Model used by the plan_research node."""
    return _make_model(cfg.research_planner_model)


@lru_cache(maxsize=1)
def get_synthesizer_model() -> ChatOpenAI:
    """Model used by the synthesize_research node."""
    return _make_model(cfg.research_synthesizer_model)


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _tavily_search(query: str, date_filter: dict | None = None) -> list[dict]:
    """Run a Tavily web search and return result snippets."""
    if not cfg or not cfg.tavily_api_key:
        raise ConfigError("TAVILY_API_KEY is not set")
    if date_filter:
        year = date_filter.get("year") or date_filter.get("from")
        month = date_filter.get("month")
        if year and month:
            month_name = next(k for k, v in _MONTH_NAMES.items() if v == month and len(k) > 3)
            query = f"{query} {month_name} {year}"
        elif year:
            query = f"{query} {year}"
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


def _arxiv_search(query: str, date_filter: dict | None = None) -> list[dict]:
    """Search arXiv for academic papers using the public API."""
    try:
        search_query = f"all:{query}"
        sort_by = "relevance"
        if date_filter:
            year = date_filter.get("year")
            month = date_filter.get("month")
            from_year = date_filter.get("from", year)
            to_year = date_filter.get("to", year)
            if from_year and to_year:
                if month:
                    last_day = calendar.monthrange(year, month)[1]
                    from_ts = f"{year}{month:02d}010000"
                    to_ts = f"{year}{month:02d}{last_day:02d}2359"
                else:
                    from_ts = f"{from_year}01010000"
                    to_ts = f"{to_year}12312359"
                # arXiv date range syntax — must be passed as a raw URL, not via httpx.URL()
                # which would percent-encode the brackets and plus signs
                date_clause = f"AND+submittedDate:[{from_ts}+TO+{to_ts}]"
                search_query = f"all:{query}+{date_clause}"
                sort_by = "submittedDate"
        url = f"https://export.arxiv.org/api/query?search_query={search_query}&max_results=3&sortBy={sort_by}"
        resp = httpx.get(url, timeout=15)
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


def _github_search(query: str, date_filter: dict | None = None) -> list[dict]:
    """Search GitHub repositories using the public search API."""
    try:
        if date_filter:
            year = date_filter.get("year")
            month = date_filter.get("month")
            from_year = date_filter.get("from", year)
            to_year = date_filter.get("to", year)
            if year and month:
                last_day = calendar.monthrange(year, month)[1]
                query = f"{query} created:{year}-{month:02d}-01..{year}-{month:02d}-{last_day:02d}"
            elif year:
                query = f"{query} created:{year}-01-01..{year}-12-31"
            elif from_year and to_year:
                query = f"{query} created:{from_year}-01-01..{to_year}-12-31"
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


_MONTH_NAMES = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

_MONTH_PATTERN = "|".join(_MONTH_NAMES)


def _extract_date_filter(topic: str) -> dict:
    """Parse temporal constraints from the topic string.

    Recognises patterns like:
      - "in 2026 march" / "2025 february" / "march 2026"  → {"year": 2026, "month": 3}
      - "in 2026" / "from 2026" / "since 2026"            → {"year": 2026}
      - "2024-2025" / "2024 to 2025"                      → {"from": 2024, "to": 2025}
    Returns an empty dict when no year is found.
    """
    # Year + month in either order: "2026 march" or "march 2026" or "in 2026 march"
    ym_m = re.search(
        rf"\b(20\d{{2}})\s+({_MONTH_PATTERN})\b|\b({_MONTH_PATTERN})\s+(20\d{{2}})\b",
        topic,
        re.IGNORECASE,
    )
    if ym_m:
        if ym_m.group(1):
            year, month_name = int(ym_m.group(1)), ym_m.group(2).lower()
        else:
            year, month_name = int(ym_m.group(4)), ym_m.group(3).lower()
        return {"year": year, "month": _MONTH_NAMES[month_name]}

    # Range: "2024-2025" or "2024 to 2025"
    range_m = re.search(r"\b(20\d{2})\s*(?:-|to)\s*(20\d{2})\b", topic, re.IGNORECASE)
    if range_m:
        return {"from": int(range_m.group(1)), "to": int(range_m.group(2))}

    # Single year with qualifiers or bare
    single_m = re.search(
        r"(?:in|from|since|before|after|year|published|created|submitted)?\s*(20\d{2})\b",
        topic,
        re.IGNORECASE,
    )
    if single_m:
        return {"year": int(single_m.group(1))}
    return {}


def _run_search(source: str, query: str, date_filter: dict | None = None) -> list[dict]:
    """Dispatch to the correct search backend."""
    if source == "web":
        return _tavily_search(query, date_filter)
    if source == "arxiv":
        return _arxiv_search(query, date_filter)
    if source == "github":
        return _github_search(query, date_filter)
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
        date_filter = _extract_date_filter(topic)
        reset = {
            "turn": human_msg_count,
            "topic": topic,
            "search_plan": [],
            "search_results": [],
            "synthesis": "",
            "done": False,
            "blocked": False,
            "block_reason": "",
            "date_filter": date_filter,
        }
        logger.info("New research turn %d: topic=%r date_filter=%r", human_msg_count, topic[:80], date_filter)
    else:
        topic = state.get("topic", "")
        date_filter = state.get("date_filter", {})

    max_searches = state.get("max_searches", _DEFAULT_MAX_SEARCHES)

    date_instruction = ""
    if date_filter:
        if "year" in date_filter and "month" in date_filter:
            month_name = calendar.month_name[date_filter["month"]]
            date_instruction = f"\nIMPORTANT: The user wants results specifically from {month_name} {date_filter['year']}. Include both the month and year in every query string."
        elif "year" in date_filter:
            date_instruction = f"\nIMPORTANT: The user wants results specifically from {date_filter['year']}. Include the year in every query string."
        elif "from" in date_filter and "to" in date_filter:
            date_instruction = f"\nIMPORTANT: The user wants results from {date_filter['from']} to {date_filter['to']}. Include this date range in every query string."

    valid_sources = _ALLOWED_SOURCES or {"web", "arxiv", "github"}

    plan_prompt = (
        f"You are a research planner. Given the topic below, create a structured search plan.\n"
        f"Topic: {topic}\n"
        f"{date_instruction}\n"
        f"Return a JSON array of up to {max_searches} search tasks. Each task must have:\n"
        f'  - "source": one of {", ".join(sorted(valid_sources))}\n'
        '  - "query": a concise search query string\n\n'
        "Focus: cover fundamentals, recent research, and practical implementations.\n"
        "Return ONLY the JSON array, no other text."
    )

    logger.info("Planning research for topic: %r", topic[:80])
    try:
        raw = get_planner_model().invoke([HumanMessage(content=plan_prompt)]).content
        if not isinstance(raw, str):
            raw = str(raw)
        # Extract JSON array from response
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            plan: list[dict] = json.loads(match.group())
        else:
            plan = []
        # Validate structure; drop malformed entries
        plan = [
            p for p in plan
            if isinstance(p, dict) and p.get("source") in valid_sources and p.get("query")
        ][:max_searches]
    except Exception as exc:
        logger.error("Failed to parse search plan: %s", exc)
        default_source = next(iter(_ALLOWED_SOURCES)) if _ALLOWED_SOURCES else "web"
        plan = [{"source": default_source, "query": topic}]

    logger.info("Search plan: %d tasks", len(plan))
    return {**reset, "search_plan": plan}


def execute_searches(state: ResearchState) -> dict:
    """Execute all search tasks from the plan and collect results."""
    plan: list[dict] = state.get("search_plan", [])
    if not plan:
        logger.warning("No search plan found — skipping searches")
        return {}

    date_filter: dict = state.get("date_filter", {})

    all_results: list[dict] = []
    for task in plan:
        source = task.get("source", "web")
        query = task.get("query", "")
        logger.info("Searching [%s]: %r (date_filter=%r)", source, query[:80], date_filter)
        try:
            results = _run_search(source, query, date_filter)
            all_results.extend(results)
        except Exception as exc:
            logger.error("Search failed [%s] %r: %s", source, query[:60], exc)

    # Deduplicate by URL, preserving order
    seen: set[str] = set()
    unique_results = [r for r in all_results if not (r.get("url") in seen or seen.add(r.get("url", "")))]
    logger.info("Collected %d results (%d after dedup)", len(all_results), len(unique_results))
    return {"search_results": unique_results}


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
        raw = get_synthesizer_model().invoke([HumanMessage(content=synthesis_prompt)]).content
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
