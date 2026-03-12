"""Research Agent: Plan-and-Execute deep research with web, arXiv, and GitHub search."""
from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Annotated, Literal

import numpy as np
import httpx
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from sentence_transformers import SentenceTransformer
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

_EMBED_SIMILARITY_THRESHOLD = 0.7
_QUERY_COUNT = 50
_RESULTS_PER_QUERY = 15
_ARXIV_PAGE_SIZE = 50       # results per paginated request (date-filtered searches)
_ARXIV_MAX_PAGES = 10       # max pages to fetch per date-filtered query
_ARXIV_CANDIDATE_CAP = 200  # hard cap on candidate pool before truncation

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
        default=_QUERY_COUNT,
        description="Maximum number of search queries to execute per run.",
    )]
    date_filter: Annotated[dict, Field(
        default_factory=dict,
        description="Extracted date/year constraints from the topic. Keys: 'year' (int) or 'from'/'to' (int).",
    )]
    topics: Annotated[list[str], Field(
        default_factory=list,
        description="Flat list of all phrases from research_intent (domains + methods + concepts).",
    )]
    research_intent: Annotated[dict, Field(
        default_factory=dict,
        description="Structured research intent with problem_domains, methods, related_concepts.",
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
def get_embedding_model() -> SentenceTransformer:
    """Embedding model for semantic similarity filtering."""
    if cfg is None:
        raise ConfigError("Agent config not loaded — check your environment variables.")
    return SentenceTransformer(cfg.research_embedding_model)


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


def _parse_arxiv_entries(entries: list[str]) -> list[dict]:
    """Parse a list of raw arXiv Atom <entry> strings into result dicts."""
    results = []
    for entry in entries:
        title_m = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
        summary_m = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
        link_m = re.search(r"<id>(.*?)</id>", entry, re.DOTALL)
        results.append({
            "source": "arxiv",
            "title": title_m.group(1).strip() if title_m else "",
            "url": link_m.group(1).strip() if link_m else "",
            "snippet": summary_m.group(1).strip()[:500] if summary_m else "",
        })
    return results


def _arxiv_search(query: str, date_filter: dict | None = None) -> list[dict]:
    """Search arXiv for academic papers using the public API.

    Without date_filter: single relevance-sorted request.
    With date_filter: paginated submission-date traversal to build a larger
    candidate pool, then truncated to _RESULTS_PER_QUERY.
    """
    start_date = (date_filter or {}).get("start_date", "")
    end_date = (date_filter or {}).get("end_date", "")

    if date_filter and start_date and end_date:
        # Paginated high-recall path for date-filtered searches
        from_ts = start_date.replace("-", "") + "000000"
        to_ts = end_date.replace("-", "") + "235959"
        search_query = f"all:{query}+AND+submittedDate:[{from_ts}+TO+{to_ts}]"

        candidates: list[dict] = []
        seen_urls: set[str] = set()

        for page in range(_ARXIV_MAX_PAGES):
            if len(candidates) >= _ARXIV_CANDIDATE_CAP:
                break
            try:
                resp = httpx.get(
                    "https://export.arxiv.org/api/query",
                    params={
                        "search_query": search_query,
                        "start": page * _ARXIV_PAGE_SIZE,
                        "max_results": _ARXIV_PAGE_SIZE,
                        "sortBy": "submittedDate",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
            except Exception as exc:
                logger.warning("arXiv paginated fetch failed (page %d): %s", page, exc)
                break

            entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)
            if not entries:
                break

            for item in _parse_arxiv_entries(entries):
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    candidates.append(item)

        logger.info("arXiv paginated: %d candidates for query %r", len(candidates), query[:60])
        return candidates[:_RESULTS_PER_QUERY]

    # Single relevance-sorted request (no date filter)
    try:
        resp = httpx.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f"all:{query}",
                "max_results": _RESULTS_PER_QUERY,
                "sortBy": "relevance",
            },
            timeout=15,
        )
        resp.raise_for_status()
        entries = re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL)
        return _parse_arxiv_entries(entries[:_RESULTS_PER_QUERY])
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

    # Duckling fallback: regex for year ranges like "2023 to 2026" or "2023-2026"
    m = re.search(r"\b(20\d{2})\s*(?:to|-|through|–)\s*(20\d{2})\b", text)
    if m:
        start_year, end_year = m.group(1), m.group(2)
        return {"start_date": f"{start_year}-01-01", "end_date": f"{end_year}-12-31"}

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


def extract_research_intent(state: ResearchState) -> dict:
    """Extract structured research intent from the user query."""
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
            "research_intent": {},
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
        f"Extract structured research intent as a JSON object with three keys:\n"
        f"- problem_domains: main scientific problems or subjects (2–5 word phrases)\n"
        f"- methods: research techniques or approaches mentioned or implied\n"
        f"- related_concepts:\
        - Related concepts must remain within the same research problem or objective.\
        - Do not introduce different tasks, applications, or problem types unrelated to the user query.\
        - Related concepts may broaden terminology but must not change the underlying task being studied.\n\n"
        f"Rules:\n"
        f"- Each value is a list of short phrases (2–5 words)\n"
        f"- Avoid paper titles or overly specific phrases\n"
        f"- Remain semantically aligned with the user query\n"
        f"- Return ONLY valid JSON, e.g.:\n"
        f'{{"problem_domains": ["quantum parameter estimation"], '
        f'"methods": ["machine learning"], "related_concepts": ["quantum sensing"]}}'
    )

    intent: dict = {}
    try:
        raw = get_topic_extractor_model().invoke([HumanMessage(content=prompt)]).content
        if not isinstance(raw, str):
            raw = str(raw)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            parsed = json.loads(match.group())
            intent = {
                "problem_domains": [s for s in parsed.get("problem_domains", []) if isinstance(s, str) and s.strip()][:5],
                "methods": [s for s in parsed.get("methods", []) if isinstance(s, str) and s.strip()][:5],
                "related_concepts": [s for s in parsed.get("related_concepts", []) if isinstance(s, str) and s.strip()][:5],
            }
    except Exception as exc:
        logger.warning("extract_topics LLM failed: %s", exc)

    if not intent:
        intent = {"problem_domains": [topic] if topic else [], "methods": [], "related_concepts": []}

    flat_topics = intent["problem_domains"] + intent["methods"] + intent["related_concepts"]
    logger.info("extract_topics: %d intent phrases across 3 categories", len(flat_topics))
    return {**reset, "research_intent": intent, "topics": flat_topics}


_FILLER_WORDS = {"for", "with", "using", "in", "the"}


def generate_semantic_queries(state: ResearchState) -> dict:
    """Generate broad keyword retrieval queries via combinatorial expansion of research intent.

    Produces ~QUERY_COUNT queries by expanding problem_domains × methods × concepts.
    These queries are designed for high-recall keyword-based search (e.g., arXiv API).
    """
    intent: dict = state.get("research_intent", {})
    topics_fallback = state.get("topics", [])
    query_count = state.get("query_count", _QUERY_COUNT)

    domains = intent.get("problem_domains", [])
    methods = intent.get("methods", [])
    concepts = intent.get("related_concepts", [])

    if not domains and not methods:
        domains = topics_fallback[:3]

    candidates: list[str] = []

    # domain × method
    for d in domains:
        for m in methods:
            candidates.append(f"{d} {m}")

    # domain × concept
    for d in domains:
        for c in concepts:
            candidates.append(f"{d} {c}")

    # domain × method × concept (trimmed to 6 words max)
    for d in domains:
        for m in methods:
            for c in concepts:
                raw = f"{d} {m} {c}"
                words = [w for w in raw.split() if w not in _FILLER_WORDS]
                candidates.append(" ".join(words[:6]))

    # Bare terms: each domain, method, concept as its own query
    for d in domains:
        candidates.append(d)
    for m in methods:
        candidates.append(m)
    for c in concepts:
        candidates.append(c)

    # Normalize: lowercase, remove filler words, deduplicate
    seen: dict[str, None] = {}
    for q in candidates:
        words = [w.lower() for w in q.split() if w.lower() not in _FILLER_WORDS]
        normalized = " ".join(words)
        if normalized and normalized not in seen:
            seen[normalized] = None

    queries = list(seen)[:query_count]
    logger.info("generate_semantic_queries: %d keyword queries", len(queries))
    return {"expanded_keywords": queries}


_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "by",
    "for", "with", "from", "via", "using", "based", "into", "about",
}

_PUNCT_RE = re.compile(r"[^\w\s]")


def _clean_query(q: str) -> str:
    """Lowercase, strip punctuation, remove stopwords, collapse whitespace."""
    q = _PUNCT_RE.sub(" ", q.lower())
    words = [w for w in q.split() if w and w not in _STOPWORDS]
    return " ".join(words).strip()


def _shares_domain(query: str, domains: list[str]) -> bool:
    """True if any domain word appears in the query."""
    domain_words = {w for d in domains for w in d.lower().split()}
    query_words = set(query.split())
    return bool(domain_words & query_words)


def normalize_queries(state: ResearchState) -> dict:
    return state # do not remove it is for debuging
    """Normalize and deduplicate semantic queries into bare arXiv-compatible strings."""
    queries = state.get("expanded_keywords", [])
    intent: dict = state.get("research_intent", {})
    domains = intent.get("problem_domains", [])
    max_searches = state.get("max_searches", _QUERY_COUNT)
    if not queries:
        queries = domains or [state.get("topic", "")]

    cleaned: list[str] = []
    for q in queries:
        query = _clean_query(q)
        if not query:
            continue

        # Ensure at least one domain term is present
        if domains and not _shares_domain(query, domains):
            query = f"{_clean_query(domains[0])} {query}"

        cleaned.append(query)

    # Exact deduplication only
    seen: set[str] = set()
    unique: list[str] = []

    for q in cleaned:
        if q not in seen:
            seen.add(q)
            unique.append(q)

    logger.info("normalize_queries: %d → %d", len(queries), len(unique[:max_searches]))

    return {"arxiv_queries": unique[:max_searches]}


def apply_date_filter(state: ResearchState) -> dict:
    """Assemble search_plan from arxiv_queries. Date filtering is applied in _arxiv_search via date_filter."""
    queries = state.get("arxiv_queries", [])
    max_searches = state.get("max_searches", _QUERY_COUNT)
    if not queries:
        queries = state.get("expanded_keywords", [])
    if not queries:
        topic = state.get("topic", "")
        queries = [topic] if topic else []

    if not queries:
        logger.warning("apply_date_filter: no queries, blocking")
        return {
            "search_plan": [],
            "blocked": True,
            "block_reason": "No search queries could be generated.",
            "synthesis": "No sources found.",
            "messages": [AIMessage(content="No sources found.")],
        }

    queries = queries[:max_searches]

    search_plan = [
        {"source": "arxiv", "query": q}
        for q in queries
    ]
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


def validate_date_range(state: ResearchState) -> dict:
    """Remove arXiv results whose submission date (from URL ID) falls outside date_filter range.

    arXiv paper IDs post-2007: YYMM.NNNNN — first 4 digits encode submission year+month.
    Example: https://arxiv.org/abs/2401.12345v1 → 2024-01.
    Non-arXiv results and results with unparseable IDs are kept unchanged.
    If no date_filter is set, returns immediately without modifying results.
    """
    date_filter: dict = state.get("date_filter", {})
    if not date_filter:
        return {}

    start = date_filter.get("start_date", "")
    end = date_filter.get("end_date", "")
    if not start or not end:
        return {}

    results: list[dict] = state.get("search_results", [])
    if not results:
        return {}

    try:
        start_year, start_month = int(start[:4]), int(start[5:7])
        end_year, end_month = int(end[:4]), int(end[5:7])
    except (ValueError, IndexError):
        logger.warning("validate_date_range: could not parse date_filter bounds, skipping")
        return {}

    _arxiv_id_re = re.compile(r"/abs/(\d{4})\.")

    kept: list[dict] = []
    removed = 0
    for result in results:
        if result.get("source") != "arxiv":
            kept.append(result)
            continue
        url = result.get("url", "")
        m = _arxiv_id_re.search(url)
        if not m:
            kept.append(result)
            continue
        yymm = m.group(1)
        paper_year = 2000 + int(yymm[:2])
        paper_month = int(yymm[2:])
        if (start_year, start_month) <= (paper_year, paper_month) <= (end_year, end_month):
            kept.append(result)
        else:
            logger.info(
                "validate_date_range: removing out-of-range result %s (%04d-%02d outside %s–%s)",
                url[:60], paper_year, paper_month, start, end,
            )
            removed += 1

    logger.info("validate_date_range: kept %d / %d results (%d removed)", len(kept), len(results), removed)
    if not kept:
        logger.warning("validate_date_range: all results removed by date filter")
        return {"search_results": kept}
    return {"search_results": kept}




def rank_results_by_similarity(state: ResearchState) -> dict:
    """Filter search results by embedding cosine similarity to the user query."""
    topic = state.get("topic", "")
    results: list[dict] = state.get("search_results", [])

    if not results:
        return {}

    try:
        embedder = get_embedding_model()
        texts = [f"{r.get('title', '')} {r.get('snippet', '')}" for r in results]
        query_emb = embedder.encode(topic, normalize_embeddings=True)
        result_embs = embedder.encode(texts, normalize_embeddings=True)
    except Exception as exc:
        logger.warning("filter_results embedding failed (%s) — keeping all results", exc)
        return {}

    kept: list[dict] = []
    seen_titles: set[str] = set()
    for result, doc_emb in zip(results, result_embs):
        sim = float(np.dot(query_emb, doc_emb))
        title_key = result.get("title", "").lower().strip()
        if sim >= _EMBED_SIMILARITY_THRESHOLD and title_key not in seen_titles:
            kept.append(result)
            seen_titles.add(title_key)
        else:
            logger.info("rank_results_by_similarity: dropping %r (sim=%.3f)", result.get("title", "")[:60], sim)

    logger.info(
        "filter_results: kept %d / %d results (threshold=%.2f)",
        len(kept), len(results), _EMBED_SIMILARITY_THRESHOLD,
    )
    if not kept:
        logger.warning("rank_results_by_similarity: all results dropped by similarity filter")
        return {
            "search_results": [],
            "synthesis": "No sources found.",
            "messages": [AIMessage(content="No sources found.")],
        }
    return {"search_results": kept}


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


def route_after_rank_results(
    state: ResearchState,
) -> Literal["synthesize", "__end__"]:
    """Route after rank_results_by_similarity: end with message if no results remain."""
    if not state.get("search_results"):
        return "__end__"
    return "synthesize"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Build and compile the LangGraph research agent graph."""
    graph = StateGraph(ResearchState)
    graph.add_node("parse_dates", parse_dates)
    graph.add_node("extract_research_intent", extract_research_intent)
    graph.add_node("generate_semantic_queries", generate_semantic_queries)
    graph.add_node("normalize_queries", normalize_queries)
    graph.add_node("apply_date_filter", apply_date_filter)
    graph.add_node("execute_searches", execute_searches)
    graph.add_node("validate_date_range", validate_date_range)
    graph.add_node("rank_results_by_similarity", rank_results_by_similarity)
    graph.add_node("synthesize", synthesize_research)

    graph.set_entry_point("parse_dates")
    graph.add_edge("parse_dates", "extract_research_intent")
    graph.add_edge("extract_research_intent", "generate_semantic_queries")
    graph.add_edge("generate_semantic_queries", "normalize_queries")
    graph.add_edge("normalize_queries", "apply_date_filter")
    graph.add_conditional_edges(
        "apply_date_filter",
        route_after_apply_date_filter,
        {"execute_searches": "execute_searches", "__end__": END},
    )
    graph.add_edge("execute_searches", "validate_date_range")
    graph.add_edge("validate_date_range", "rank_results_by_similarity")
    graph.add_conditional_edges(
        "rank_results_by_similarity",
        route_after_rank_results,
        {"synthesize": "synthesize", "__end__": END},
    )
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
def run_agent(topic: str, query_count: int = _QUERY_COUNT) -> ResearchState:
    """Run the research agent on a topic.

    Args:
        topic: The research question or topic. Must be non-empty.
        query_count: Number of search queries to generate and run (default: _QUERY_COUNT).

    Returns:
        Final ResearchState with synthesis, search_results, and metadata.

    Raises:
        ValueError: If topic is empty.
    """
    if not topic or not topic.strip():
        raise ValueError("Topic must be a non-empty string")

    logger.info("Starting research agent: topic=%r query_count=%d", topic[:80], query_count)
    return app.invoke({
        "topic": topic.strip(),
        "max_searches": query_count,
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
        "--query-count",
        type=int,
        default=_QUERY_COUNT,
        metavar="N",
        help=f"Number of search queries to run (default {_QUERY_COUNT})",
    )
    args = parser.parse_args()

    try:
        result = run_agent(args.topic, query_count=args.query_count)
        print(result.get("synthesis", "No synthesis produced."))
    except (ValueError, ConfigError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
