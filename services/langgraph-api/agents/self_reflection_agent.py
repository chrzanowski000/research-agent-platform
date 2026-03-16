"""Self-reflection agent: generates answers and iteratively improves them via LLM review."""
from __future__ import annotations

import argparse
import json
import logging
import re
from functools import lru_cache
from typing import Annotated, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import PIIMiddleware
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
# Config (loaded once at import time)
# ---------------------------------------------------------------------------

try:
    cfg = Config.from_env()
    cfg.log_models()
except ConfigError as _cfg_err:
    logger.warning("Config incomplete: %s — some features may be unavailable.", _cfg_err)
    cfg = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# State schema
#
# Inheriting MessagesState gives us the `messages` field with the add_messages
# reducer — this is what LangSmith Studio uses to detect and enable the chat
# interface. All other fields are added as optional TypedDict entries.
#
# Nodes receive state as a plain dict; use state.get("field", default) for access.
# Nodes return partial dicts; LangGraph merges only the changed keys.
# ---------------------------------------------------------------------------

_DEFAULT_MAX_ITERATIONS = 3
_DEFAULT_MAX_WEB_SEARCHES = 3


class AgentState(MessagesState, total=False):
    """Runtime state shared across all nodes in the self-reflection graph.

    ``messages`` is inherited from MessagesState and carries the full
    conversation history used by the LangSmith Studio chat interface.
    """

    turn: Annotated[int, Field(
        default=0,
        description="Number of conversation turns completed. Used to detect new turns and reset per-turn counters.",
    )]
    task: Annotated[str, Field(
        default="",
        description="The question or task the agent must answer. Auto-populated from the last HumanMessage if left empty.",
    )]
    draft: Annotated[str, Field(
        default="",
        description="Current draft answer produced by the generation node.",
    )]
    feedback: Annotated[str, Field(
        default="",
        description="Actionable feedback from the reflection node (empty when approved).",
    )]
    iteration: Annotated[int, Field(
        default=0,
        description="Number of generate-reflect loops completed so far.",
    )]
    max_iterations: Annotated[int, Field(
        default=_DEFAULT_MAX_ITERATIONS,
        description="Maximum number of generate-reflect loops before the agent stops.",
        ge=1,
        le=10,
    )]
    web_search_count: Annotated[int, Field(
        default=0,
        description="Total number of web searches performed in this run.",
    )]
    max_web_searches: Annotated[int, Field(
        default=_DEFAULT_MAX_WEB_SEARCHES,
        description="Maximum number of web searches allowed per run. Override in Studio to control search budget.",
        ge=0,
        le=10,
    )]
    done: Annotated[bool, Field(
        default=False,
        description="True when the draft is approved or max iterations are reached.",
    )]
    blocked: Annotated[bool, Field(
        default=False,
        description="True when the agent was stopped for safety reasons (e.g. PII detected).",
    )]
    block_reason: Annotated[str, Field(
        default="",
        description="Human-readable explanation of why the agent was blocked.",
    )]
    web_context: Annotated[str, Field(
        default="",
        description="Accumulated web search results injected into generation prompts.",
    )]
    search_needed: Annotated[bool, Field(
        default=False,
        description="True when the search_decision node decided a web search is required.",
    )]
    search_query: Annotated[str, Field(
        default="",
        description="Query string for the next Tavily web search.",
    )]


# ---------------------------------------------------------------------------
# State update helpers
# ---------------------------------------------------------------------------


def _block_update(source: str, reason: str) -> dict:
    """Return a partial state update dict that marks the agent as blocked.

    Args:
        source: Node or system where the block originated.
        reason: Human-readable reason.

    Returns:
        Dict with done, blocked, block_reason, and draft fields set.
    """
    logger.warning("Blocking agent: source=%s reason=%s", source, reason)
    block_reason = (
        f"Blocked: sensitive or personal information detected in {source}. "
        f"Reason: {reason}"
    )
    return {
        "done": True,
        "blocked": True,
        "block_reason": block_reason,
        "draft": (
            "Request blocked for safety. Please remove or anonymize sensitive/personal "
            f"data and retry. ({block_reason})"
        ),
    }

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
def get_search_decision_model() -> ChatOpenAI:
    """Model used by the search_decision node."""
    return _make_model(cfg.reflection_v1_search_decision_model)


@lru_cache(maxsize=1)
def get_generate_model() -> ChatOpenAI:
    """Model used by the generate_answer node."""
    return _make_model(cfg.reflection_v1_generate_model)


@lru_cache(maxsize=1)
def get_reflect_model() -> ChatOpenAI:
    """Model used by the reflect_on_answer node."""
    return _make_model(cfg.reflection_v1_reflect_model)

# ---------------------------------------------------------------------------
# PII middleware
# ---------------------------------------------------------------------------


def get_pii_middleware() -> list[PIIMiddleware]:
    """Return PII middleware list with masking applied to all inputs and outputs."""
    return [
        PIIMiddleware("email",       strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("credit_card", strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("ip",          strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("mac_address", strategy="mask", apply_to_input=True, apply_to_output=True),
    ]


def get_generation_agent():
    """Return a generation agent with PII middleware applied.

    Returns:
        Middleware-wrapped agent that writes or improves draft answers.
    """
    return create_agent(
        model=get_generate_model(),
        tools=[],
        system_prompt=(
            "You are a concise assistant. Write a clear answer that addresses the user task. "
            "If feedback is provided, improve the answer based on it."
        ),
        middleware=get_pii_middleware(),
    )


def get_reflection_agent():
    """Return a reflection agent with PII middleware applied.

    Returns:
        Middleware-wrapped agent that reviews drafts for quality.
    """
    return create_agent(
        model=get_reflect_model(),
        tools=[],
        system_prompt=(
            "You are a strict reviewer. Evaluate the draft for: correctness, completeness, and clarity. "
            "If it is good enough, output exactly: APPROVED. "
            "Otherwise, output concise actionable feedback in 2-4 bullet points."
        ),
        middleware=get_pii_middleware(),
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_last_message_text(result: dict) -> str:
    """Extract plain text from the last message in an agent result dict.

    Args:
        result: Agent invoke result with a 'messages' list.

    Returns:
        Stripped text content of the last message.
    """
    messages = result.get("messages", [])
    if not messages:
        return ""
    last = messages[-1]
    content = getattr(last, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip()


def is_pii_detection_error(exc: Exception) -> bool:
    """Return True if the exception is a PIIDetectionError."""
    return exc.__class__.__name__ == "PIIDetectionError"

# ---------------------------------------------------------------------------
# Search decision
# ---------------------------------------------------------------------------


def parse_search_decision(text: str) -> tuple[bool, str]:
    """Parse NEEDS_SEARCH and QUERY fields from LLM output.

    Args:
        text: Raw LLM response text.

    Returns:
        Tuple of (needs_search, query). Defaults to (False, '') if pattern not found.
    """
    needs_search = False
    query = ""

    needs_match = re.search(r"NEEDS_SEARCH:\s*(yes|no)", text, re.IGNORECASE)
    if needs_match:
        needs_search = needs_match.group(1).lower() == "yes"
    else:
        logger.warning("Could not parse NEEDS_SEARCH from LLM output; defaulting to no search")

    if needs_search:
        query_match = re.search(r"QUERY:\s*(.+)", text, re.IGNORECASE)
        if query_match:
            query = query_match.group(1).strip()[:200]  # cap to prevent injection

    return needs_search, query


def search_decision(state: AgentState) -> dict:
    """Ask the LLM whether a web search is needed for the current iteration.

    On each new conversation turn (detected via HumanMessage count vs stored turn),
    resets all per-turn counters so max_iterations and max_web_searches apply
    fresh to each turn and conversations can continue indefinitely.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with search_needed, search_query, and any reset fields.
    """
    messages = state.get("messages", [])
    human_msg_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    current_turn = state.get("turn", 0)

    reset: dict = {}
    if human_msg_count > current_turn:
        # New external turn — extract task from latest HumanMessage and reset per-turn fields
        task = next(
            (m.content if isinstance(m.content, str) else str(m.content)
             for m in reversed(messages) if isinstance(m, HumanMessage)),
            "",
        )
        reset = {
            "turn": human_msg_count,
            "task": task,
            "iteration": 0,
            "web_search_count": 0,
            "done": False,
            "blocked": False,
            "block_reason": "",
            "draft": "",
            "feedback": "",
            "web_context": "",
        }
        logger.info("New turn %d: task=%r", human_msg_count, task[:80])
    else:
        task = state.get("task", "")

    max_searches = state.get("max_web_searches", _DEFAULT_MAX_WEB_SEARCHES)
    web_search_count = reset.get("web_search_count", state.get("web_search_count", 0))
    if web_search_count >= max_searches:
        logger.info("Max web searches reached (%d), skipping", max_searches)
        return {"search_needed": False, "search_query": "", **reset}

    iteration = reset.get("iteration", state.get("iteration", 0))
    logger.info("Deciding if web search is needed (iteration=%d)", iteration)
    decision_prompt = (
        "Decide if internet search is needed.\n"
        f"Task: {task}\n\n"
        f"Current draft:\n{reset.get('draft', state.get('draft', ''))}\n\n"
        f"Reviewer feedback:\n{reset.get('feedback', state.get('feedback', ''))}\n\n"
        f"Current web context:\n{reset.get('web_context', state.get('web_context', ''))}\n\n"
        "Return exactly:\n"
        "NEEDS_SEARCH: yes|no\n"
        "QUERY: <short search query or empty>\n"
        "Use NEEDS_SEARCH=yes for current events, factual verification, or missing external data."
    )

    raw = get_search_decision_model().invoke([HumanMessage(content=decision_prompt)]).content
    if not isinstance(raw, str):
        raw = str(raw)
    needs_search, query = parse_search_decision(raw)

    if needs_search:
        logger.info("Search needed, query: %r", query)
        return {"search_needed": True, "search_query": query or task, **reset}

    logger.info("No search needed")
    return {"search_needed": False, "search_query": "", **reset}

# ---------------------------------------------------------------------------
# Web search (Tavily)
# ---------------------------------------------------------------------------


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _run_tavily_search(query: str) -> dict:
    """Execute a Tavily search query with retry on transient errors.

    Args:
        query: Search query string.

    Returns:
        Tavily API response dict.

    Raises:
        ConfigError: If TAVILY_API_KEY is not set.
    """
    if not cfg or not cfg.tavily_api_key:
        raise ConfigError("TAVILY_API_KEY is not set")
    client = TavilyClient(api_key=cfg.tavily_api_key)
    return client.search(
        query=query,
        topic="general",
        max_results=1,
        search_depth="advanced",
    )


def tavily_web_search(state: AgentState) -> dict:
    """Execute web search and append results to web_context.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with web_context extended, or block update on failure.
    """
    if not state.get("search_needed", False):
        return {}

    query = state.get("search_query", "") or state.get("task", "")
    logger.info("Running web search: %r", query)

    try:
        result = _run_tavily_search(query)
    except Exception as exc:
        logger.error("Web search failed: %s", exc)
        return _block_update(source="web_search", reason=str(exc))

    snippets: list[str] = []
    for item in result.get("results", []):
        title   = item.get("title", "")
        url     = item.get("url", "")
        content = item.get("content", "")
        snippets.append(f"- {title}\n  URL: {url}\n  Snippet: {content}")

    search_block = (
        f"Search query: {query}\n"
        "Results:\n"
        + ("\n".join(snippets) if snippets else "- No results returned.")
    )
    web_context = state.get("web_context", "")
    parts = [p for p in [web_context, search_block] if p]
    logger.info("Web search complete, %d result(s) appended", len(snippets))
    return {
        "web_context":      "\n\n".join(parts),
        "web_search_count": state.get("web_search_count", 0) + 1,
        "search_needed":    False,
        "search_query":     "",
    }

# ---------------------------------------------------------------------------
# Generation and reflection
# ---------------------------------------------------------------------------


def generate_answer(state: AgentState) -> dict:
    """Generate or improve the draft answer using the generation agent.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with a new draft and incremented iteration count.
    """
    generator = get_generation_agent()
    task = state.get("task", "")
    feedback = state.get("feedback", "")
    web_context = state.get("web_context", "")
    draft = state.get("draft", "")
    iteration = state.get("iteration", 0)

    if feedback:
        user_prompt = (
            f"Task: {task}\n\n"
            f"Web context (if any):\n{web_context}\n\n"
            f"Current draft:\n{draft}\n\n"
            f"Feedback to address:\n{feedback}\n\n"
            "Return an improved draft only."
        )
    else:
        user_prompt = (
            f"Task: {task}\n\n"
            f"Web context (if any):\n{web_context}\n\n"
            "Write the best possible first draft."
        )

    logger.info("Generating answer (iteration=%d)", iteration + 1)
    try:
        response = generator.invoke({"messages": [HumanMessage(content=user_prompt)]})
    except Exception as exc:
        if is_pii_detection_error(exc):
            logger.warning("PII detected in generation — blocking request")
            return _block_update(source="generate", reason=str(exc))
        raise

    new_draft = extract_last_message_text(response)
    logger.info("Draft generated (%d chars)", len(new_draft))
    return {"draft": new_draft, "iteration": iteration + 1}


def reflect_on_answer(state: AgentState) -> dict:
    """Evaluate the current draft and mark done or set feedback.

    Args:
        state: Current agent state.

    Returns:
        Partial state update with done=True and empty feedback, or done=False and feedback text.
    """
    reviewer = get_reflection_agent()
    task = state.get("task", "")
    draft = state.get("draft", "")
    iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", _DEFAULT_MAX_ITERATIONS)

    review_prompt = (
        f"Task: {task}\n\n"
        f"Draft:\n{draft}\n\n"
        "Review whether Draft have enough information to answer Task. Check if form expected in Task matches Draft"
    )

    logger.info("Reflecting on draft (iteration=%d)", iteration)
    try:
        result = reviewer.invoke({"messages": [HumanMessage(content=review_prompt)]})
    except Exception as exc:
        if is_pii_detection_error(exc):
            logger.warning("PII detected in reflection — blocking request")
            return _block_update(source="reflect", reason=str(exc))
        raise

    review = extract_last_message_text(result)

    if review.strip().upper() == "APPROVED":
        logger.info("Draft APPROVED after %d iteration(s)", iteration)
        return {"done": True, "feedback": "", "messages": [AIMessage(content=draft)]}

    if iteration >= max_iterations:
        logger.info("Max iterations reached (%d), stopping", max_iterations)
        return {"done": True, "feedback": "", "messages": [AIMessage(content=draft)]}

    logger.info("Draft needs improvement: %s", review[:120])
    return {"done": False, "feedback": review}

# ---------------------------------------------------------------------------
# Graph routing
# ---------------------------------------------------------------------------


def route_after_search_decision(
    state: AgentState,
) -> Literal["web_search", "generate", "__end__"]:
    """Route after search_decision: to web_search, generate, or end if blocked."""
    if state.get("blocked", False):
        return "__end__"
    if state.get("search_needed", False):
        return "web_search"
    return "generate"


def route_after_reflect(
    state: AgentState,
) -> Literal["search_decision", "__end__"]:
    """Route after reflect: loop back to search_decision or terminate."""
    if state.get("done", False) or state.get("blocked", False):
        return "__end__"
    return "search_decision"

# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Build and compile the LangGraph self-reflection agent graph.

    Returns:
        Compiled LangGraph application.
    """
    graph = StateGraph(AgentState)
    graph.add_node("search_decision", search_decision)
    graph.add_node("web_search",      tavily_web_search)
    graph.add_node("generate",        generate_answer)
    graph.add_node("reflect",         reflect_on_answer)

    graph.set_entry_point("search_decision")
    graph.add_conditional_edges(
        "search_decision",
        route_after_search_decision,
        {"web_search": "web_search", "generate": "generate", "__end__": END},
    )
    graph.add_edge("web_search", "generate")
    graph.add_edge("generate",   "reflect")
    graph.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"search_decision": "search_decision", "__end__": END},
    )

    return graph.compile()


app = build_graph()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@traceable(
    name="run_agent",
    run_type="chain",
    tags=["self-reflection", "agentic"],
    metadata={"agent_type": "reflection", "version": "1.0"},
)
def run_agent(task: str, max_iterations: int = _DEFAULT_MAX_ITERATIONS) -> AgentState:
    """Run the self-reflection agent on a task.

    Args:
        task: The question or task to answer. Must be non-empty.
        max_iterations: Max generate-reflect loops (1-10, default 3).

    Returns:
        Final AgentState dict with draft, iteration count, and metadata.

    Raises:
        ValueError: If task is empty or max_iterations is out of range.
    """
    if not task or not task.strip():
        raise ValueError("Task must be a non-empty string")
    if not 1 <= max_iterations <= 10:
        raise ValueError("max_iterations must be between 1 and 10")

    logger.info("Starting agent: task=%r max_iterations=%d", task[:80], max_iterations)
    return app.invoke({"task": task.strip(), "max_iterations": max_iterations})

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Self-reflection agent: generates and iteratively improves answers."
    )
    parser.add_argument("task", help="Task or question to answer")
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=_DEFAULT_MAX_ITERATIONS,
        metavar="N",
        help=f"Max generate-reflect loops, 1-10 (default: {_DEFAULT_MAX_ITERATIONS})",
    )
    parser.add_argument(
        "--output",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    args = parser.parse_args()

    result = run_agent(args.task, max_iterations=args.max_iterations)

    if args.output == "json":
        print(json.dumps({
            "task":         result.get("task", ""),
            "draft":        result.get("draft", ""),
            "iterations":   result.get("iteration", 0),
            "web_searches": result.get("web_search_count", 0),
            "blocked":      result.get("blocked", False),
            "block_reason": result.get("block_reason", ""),
        }, indent=2))
    else:
        print("Task:")
        print(result.get("task", ""))
        print("\nFinal answer:")
        print(result.get("draft", ""))
        print(f"\nIterations used: {result.get('iteration', 0)}")
        print(f"Web searches used: {result.get('web_search_count', 0)}")
        if result.get("blocked"):
            print(f"\nBLOCKED: {result.get('block_reason', '')}")
