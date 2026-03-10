"""Research Agent: Plan-and-Execute deep research with web, arXiv, and GitHub search."""
from __future__ import annotations

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
    topics: Annotated[list[str], Field(
        default_factory=list,
        description="3–8 academic sub-topics extracted from the user query.",
    )]
    expanded_keywords: Annotated[list[str], Field(
        default_factory=list,
        description="Up to 20 deduplicated keyword phrases expanded from topics.",
    )]
    arxiv_queries: Annotated[list[str], Field(
        default_factory=list,
        description="5–15 bare keyword query strings before search_plan assembly.",
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


@lru_cache(maxsize=1)
def get_filter_model() -> ChatOpenAI:
    """Model used by the filter_results node."""
    return _make_model(cfg.research_filter_model)


@lru_cache(maxsize=1)
def get_topic_extractor_model() -> ChatOpenAI:
    return _make_model(cfg.research_topic_extractor_model)

@lru_cache(maxsize=1)
def get_keyword_expander_model() -> ChatOpenAI:
    return _make_model(cfg.research_keyword_expander_model)

@lru_cache(maxsize=1)
def get_query_generator_model() -> ChatOpenAI:
    return _make_model(cfg.research_query_generator_model)


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
        start = date_filter.get("start_date", "")
        end = date_filter.get("end_date", "")
        if start:
            year = start[:4]
            query = f"{query} {year}" if end[:4] == year else f"{query} {start} {end}"
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
            start = date_filter.get("start_date", "")
            end = date_filter.get("end_date", "")
            if start and end:
                # arXiv timestamp format: YYYYMMDDHHMMSS
                from_ts = start.replace("-", "") + "010000"
                to_ts = end.replace("-", "") + "2359"
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
            start = date_filter.get("start_date", "")
            end = date_filter.get("end_date", "")
            if start and end:
                query = f"{query} created:{start}..{end}"
            elif start:
                query = f"{query} created:>={start}"
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


def _parse_duckling_time(text: str) -> dict:
    """Call duckling to extract a time interval from text.

    Returns {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"} or {}.
    """
    if not cfg:
        return {}
    duckling_url = getattr(cfg, "duckling_url", "http://localhost:8000")
    try:
        resp = httpx.post(
            f"{duckling_url}/parse",
            data={"locale": "en_US", "text": text, "dims": '["time"]'},
            timeout=5,
        )
        resp.raise_for_status()
        entities = resp.json()
    except Exception as exc:
        logger.warning("Duckling request failed: %s", exc)
        return {}

    for entity in entities:
        if entity.get("dim") != "time":
            continue
        val = entity.get("value", {})
        vtype = val.get("type")

        if vtype == "interval":
            from_val = val.get("from", {}).get("value", "")
            to_val = val.get("to", {}).get("value", "")
            from_grain = val.get("from", {}).get("grain", "day")
            to_grain = val.get("to", {}).get("grain", "day")
            if from_val and to_val:
                start = _duckling_ts_to_date(from_val, grain=from_grain, end=False)
                # to_val is exclusive in duckling intervals — pass exclusive=True
                end = _duckling_ts_to_date(to_val, grain=to_grain, end=True, exclusive=True)
                if start and end:
                    return {"start_date": start, "end_date": end}

        elif vtype == "value":
            point_val = val.get("value", "")
            grain = val.get("grain", "day")
            if point_val:
                start = _duckling_ts_to_date(point_val, grain=grain, end=False)
                end = _duckling_ts_to_date(point_val, grain=grain, end=True)
                if start and end:
                    return {"start_date": start, "end_date": end}

    return {}


def _duckling_ts_to_date(ts: str, grain: str = "day", end: bool = False, exclusive: bool = False) -> str:
    """Convert a duckling ISO timestamp to an inclusive YYYY-MM-DD date string.

    Duckling interval `to` values are EXCLUSIVE (e.g. "2027-01-01" means up to
    but not including that date). Pass exclusive=True for interval `to` values
    so the helper subtracts one day to get the inclusive end date before expanding
    to grain boundary.

    For year grain:  start → YYYY-01-01,  end (inclusive) → YYYY-12-31
    For month grain: start → YYYY-MM-01,  end (inclusive) → YYYY-MM-<last>
    For day/hour/etc: use the date portion as-is.
    """
    import calendar as _cal
    from datetime import date as _date, timedelta as _td
    try:
        date_part = ts[:10]  # "YYYY-MM-DD"
        year, month, day = int(date_part[:4]), int(date_part[5:7]), int(date_part[8:10])

        # Duckling interval `to` is exclusive — subtract one day to make it inclusive
        if exclusive and end:
            d = _date(year, month, day) - _td(days=1)
            year, month, day = d.year, d.month, d.day

        if grain == "year":
            return f"{year}-12-31" if end else f"{year}-01-01"
        if grain == "month":
            last_day = _cal.monthrange(year, month)[1]
            return f"{year}-{month:02d}-{last_day:02d}" if end else f"{year}-{month:02d}-01"
        return f"{year}-{month:02d}-{day:02d}"
    except Exception:
        return ""


def parse_dates(state: ResearchState) -> dict:
    """First node: extract date constraints from the latest HumanMessage via duckling.

    Writes date_filter with start_date/end_date ISO strings, or {} if no dates found.
    """
    messages = state.get("messages", [])
    text = next(
        (m.content if isinstance(m.content, str) else str(m.content)
         for m in reversed(messages) if isinstance(m, HumanMessage)),
        "",
    )
    if not text:
        return {"date_filter": {}}

    date_filter = _parse_duckling_time(text)
    logger.info("parse_dates: %r → %r", text[:80], date_filter)
    return {"date_filter": date_filter}

# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def extract_topics(state: ResearchState) -> dict:
    """First planning node: detect new turn, reset per-turn state, extract academic topics."""
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
            "topics": [],
            "expanded_keywords": [],
            "arxiv_queries": [],
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

    prompt = (
        f"You are an academic research analyst.\n\n"
        f"User query: {topic}\n\n"
        f"Extract 3 to 8 distinct academic research sub-topics or angles that together cover "
        f"the scope of this query. Each topic should be a short noun phrase (3–6 words) "
        f"suitable for academic literature search on arXiv.\n\n"
        f"Rules:\n"
        f"- Return ONLY a JSON array of strings, e.g. [\"topic one\", \"topic two\"]\n"
        f"- No duplicates, no generic topics like \"overview\" or \"introduction\"\n"
        f"- Do NOT include the query verbatim as a topic"
    )
    topics: list[str] = []
    try:
        raw = get_topic_extractor_model().invoke([HumanMessage(content=prompt)]).content
        if not isinstance(raw, str):
            raw = str(raw)
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            topics = [t for t in parsed if isinstance(t, str) and t.strip()][:8]
    except Exception as exc:
        logger.warning("extract_topics LLM failed: %s", exc)

    if not topics:
        topics = [topic] if topic else []
    logger.info("extract_topics: %d topics", len(topics))
    return {**reset, "topics": topics}


def expand_keywords(state: ResearchState) -> dict:
    """Expand extracted topics into a deduplicated set of search keywords (max 20)."""
    topics = state.get("topics", [])
    topics_text = "\n".join(f"{i+1}. {t}" for i, t in enumerate(topics))

    prompt = (
        f"You are a research librarian specializing in academic keyword expansion.\n\n"
        f"Research topics:\n{topics_text}\n\n"
        f"For each topic, generate related keywords, synonyms, and closely related concepts "
        f"that would help find relevant papers on arXiv. Think about:\n"
        f"- Technical synonyms\n"
        f"- Related subtechniques or methods\n"
        f"- Application domains\n\n"
        f"Return ONLY a JSON array of keyword strings (max 20 total, deduplicated). "
        f"Each keyword should be 1–5 words."
    )
    keywords: list[str] = []
    try:
        raw = get_keyword_expander_model().invoke([HumanMessage(content=prompt)]).content
        if not isinstance(raw, str):
            raw = str(raw)
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            seen: dict[str, None] = {}
            for k in parsed:
                if isinstance(k, str) and k.strip() and k not in seen:
                    seen[k] = None
            keywords = list(seen)[:20]
    except Exception as exc:
        logger.warning("expand_keywords LLM failed: %s", exc)

    if not keywords:
        keywords = topics  # fallback: use topics as keywords
    logger.info("expand_keywords: %d keywords", len(keywords))
    return {"expanded_keywords": keywords}


def generate_arxiv_queries(state: ResearchState) -> dict:
    """Generate 5–max_searches bare arXiv keyword query strings from expanded_keywords."""
    keywords = state.get("expanded_keywords", [])
    max_searches = state.get("max_searches", _DEFAULT_MAX_SEARCHES)
    keywords_text = ", ".join(keywords)

    prompt = (
        f"You are an arXiv search expert.\n\n"
        f"Available keywords: {keywords_text}\n\n"
        f"Generate between 5 and {max_searches} arXiv search queries using these keywords.\n"
        f"Each query should combine 2–4 keywords into a meaningful phrase targeting a different angle.\n\n"
        f"Rules:\n"
        f"- Return ONLY a JSON array of query strings\n"
        f"- Do NOT add an 'all:' prefix — the search system adds it automatically\n"
        f"- No duplicate keyword combinations\n\n"
        f"Example: [\"transformer attention mechanism\", \"BERT language model fine-tuning\"]"
    )
    queries: list[str] = []
    try:
        raw = get_query_generator_model().invoke([HumanMessage(content=prompt)]).content
        if not isinstance(raw, str):
            raw = str(raw)
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            queries = [q for q in parsed if isinstance(q, str) and q.strip()][:max_searches]
    except Exception as exc:
        logger.warning("generate_arxiv_queries LLM failed: %s", exc)

    if not queries:
        queries = keywords[:max_searches] or [state.get("topic", "")]
    logger.info("generate_arxiv_queries: %d queries", len(queries))
    return {"arxiv_queries": queries}


def apply_date_filter(state: ResearchState) -> dict:
    """Assemble search_plan from arxiv_queries. date_filter is left intact for _arxiv_search."""
    queries = state.get("arxiv_queries", [])
    max_searches = state.get("max_searches", _DEFAULT_MAX_SEARCHES)

    if not queries:
        topic = state.get("topic", "")
        queries = [topic] if topic else []

    if not queries:
        logger.warning("apply_date_filter: no queries, blocking")
        return {"search_plan": [], "blocked": True, "block_reason": "No search queries could be generated."}

    queries = queries[:max_searches]
    search_plan = [{"source": "arxiv", "query": q} for q in queries]
    logger.info("apply_date_filter: %d search tasks", len(search_plan))
    return {"search_plan": search_plan}


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


def filter_results(state: ResearchState) -> dict:
    """Use an LLM to drop search results that are not relevant to the topic."""
    topic = state.get("topic", "")
    results: list[dict] = state.get("search_results", [])
    date_filter: dict = state.get("date_filter", {})

    if not results:
        return {}

    # Format results for the filter prompt
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"[{i}] ({r.get('source', '?')}) {r.get('title', 'No title')}\n"
            f"    {r.get('snippet', '')[:300]}"
        )
    results_text = "\n\n".join(lines)

    date_constraint_text = ""
    if date_filter:
        start = date_filter.get("start_date", "")
        end = date_filter.get("end_date", "")
        if start and end and start == end:
            date_constraint_text = (
                f"\nDATE CONSTRAINT: Only keep results published on {start}. "
                f"Reject any result that clearly belongs to a different date."
            )
        elif start and end:
            date_constraint_text = (
                f"\nDATE CONSTRAINT: Only keep results published between {start} and {end}. "
                f"Reject any result that clearly falls outside this range."
            )

    filter_prompt = (
        f"You are a research relevance filter.\n"
        f"Topic: {topic}"
        + (f"\n{date_constraint_text}" if date_constraint_text else "")
        + f"\n\nBelow are {len(results)} search results. Return a JSON array of the 1-based indices "
        f"of results that are clearly relevant to the topic"
        + (" and satisfy the date constraint above" if date_constraint_text else "")
        + ".\n"
        f"Drop results that are off-topic, too generic, or clearly unrelated"
        + (", or from the wrong time period" if date_constraint_text else "")
        + ".\n"
        f"Return ONLY the JSON array, e.g. [1, 3, 5].\n\n"
        f"{results_text}"
    )

    logger.info("Filtering %d results for relevance to topic: %r", len(results), topic[:80])
    try:
        raw = get_filter_model().invoke([HumanMessage(content=filter_prompt)]).content
        if not isinstance(raw, str):
            raw = str(raw)
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            indices: list[int] = json.loads(match.group())
            # Keep only valid 1-based indices
            filtered = [results[i - 1] for i in indices if isinstance(i, int) and 1 <= i <= len(results)]
        else:
            filtered = results
        logger.info("Relevance filter: kept %d / %d results", len(filtered), len(results))
        if not filtered:
            logger.warning("Filter dropped all results — keeping originals")
            filtered = results
    except Exception as exc:
        logger.warning("Relevance filter failed (%s) — keeping all results", exc)
        filtered = results

    return {"search_results": filtered}


def synthesize_research(state: ResearchState) -> dict:
    """Synthesize all search results into a structured research brief."""
    topic = state.get("topic", "")
    results: list[dict] = state.get("search_results", [])
    date_filter: dict = state.get("date_filter", {})

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

    temporal_scope_text = ""
    date_note_instruction = ""
    if date_filter:
        start = date_filter.get("start_date", "")
        end = date_filter.get("end_date", "")
        if start and end:
            scope = f"{start} to {end}" if start != end else start
            temporal_scope_text = f"Requested time period: {scope}"
            date_note_instruction = (
                f"For each finding, note the publication/submission date if visible in the snippet. "
                f"If any result appears to be from outside {scope}, flag it explicitly."
            )

    if date_filter:
        synthesis_prompt = (
            f"You are a research analyst. Synthesize the following search results into a structured brief.\n\n"
            f"Research topic: {topic}\n"
            f"{temporal_scope_text}\n\n"
            f"Search results:\n{results_text}\n\n"
            "Write a concise research brief with these sections:\n"
            f"## Summary\nA 2-3 sentence overview of what was found within the requested time period.\n\n"
            f"## Key Findings\nBullet points of the most important insights. {date_note_instruction}\n\n"
            "## Sources\nNumbered list of ALL sources provided above with their URLs and publication dates where available. Do not omit any.\n\n"
            "Be factual, concise, and cite sources by number."
        )
    else:
        synthesis_prompt = (
            f"You are a research analyst. Synthesize the following search results into a structured brief.\n\n"
            f"Research topic: {topic}\n\n"
            f"Search results:\n{results_text}\n\n"
            "Write a concise research brief with these sections:\n"
            "## Summary\nA 2-3 sentence overview of the topic.\n\n"
            "## Key Findings\nBullet points of the most important insights.\n\n"
            "## Practical Approaches\nMost relevant tools, libraries, or methods found.\n\n"
            "## Sources\nNumbered list of ALL sources provided above with their URLs. Do not omit any.\n\n"
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


def route_after_apply_date_filter(
    state: ResearchState,
) -> Literal["execute_searches", "__end__"]:
    """Route after apply_date_filter: to search execution or end if blocked."""
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
    graph.add_node("parse_dates", parse_dates)
    graph.add_node("extract_topics", extract_topics)
    graph.add_node("expand_keywords", expand_keywords)
    graph.add_node("generate_arxiv_queries", generate_arxiv_queries)
    graph.add_node("apply_date_filter", apply_date_filter)
    graph.add_node("execute_searches", execute_searches)
    graph.add_node("filter_results", filter_results)
    graph.add_node("synthesize", synthesize_research)

    graph.set_entry_point("parse_dates")
    graph.add_edge("parse_dates", "extract_topics")
    graph.add_edge("extract_topics", "expand_keywords")
    graph.add_edge("expand_keywords", "generate_arxiv_queries")
    graph.add_edge("generate_arxiv_queries", "apply_date_filter")
    graph.add_conditional_edges(
        "apply_date_filter",
        route_after_apply_date_filter,
        {"execute_searches": "execute_searches", "__end__": END},
    )
    graph.add_edge("execute_searches", "filter_results")
    graph.add_edge("filter_results", "synthesize")
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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Run the research agent on a topic.")
    parser.add_argument("topic", help="Research topic or question")
    parser.add_argument(
        "--max-searches",
        type=int,
        default=_DEFAULT_MAX_SEARCHES,
        metavar="N",
        help=f"Maximum number of search queries to run (1-10, default {_DEFAULT_MAX_SEARCHES})",
    )
    args = parser.parse_args()

    try:
        result = run_agent(args.topic, max_searches=args.max_searches)
        print(result.get("synthesis", "No synthesis produced."))
    except (ValueError, ConfigError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
