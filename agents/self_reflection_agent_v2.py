"""Self-reflection agent v2: generates answers and iteratively improves them via LLM review.
Web search is disabled — the agent goes directly generate → reflect without any Tavily calls.
"""
from __future__ import annotations

import argparse
import json
import logging
from functools import lru_cache
from typing import Annotated, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import PIIMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, MessagesState, StateGraph
from langsmith import traceable
from pydantic import Field

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
except ConfigError as _cfg_err:
    logger.warning("Config incomplete: %s — some features may be unavailable.", _cfg_err)
    cfg = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

_DEFAULT_MAX_ITERATIONS = 3


class AgentState(MessagesState, total=False):
    """Runtime state for the v2 self-reflection graph (no web search)."""

    turn: Annotated[int, Field(
        default=0,
        description="Number of conversation turns completed.",
    )]
    task: Annotated[str, Field(
        default="",
        description="The question or task the agent must answer.",
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


# ---------------------------------------------------------------------------
# State update helpers
# ---------------------------------------------------------------------------


def _block_update(source: str, reason: str) -> dict:
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
# Model (singleton via lru_cache)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_model() -> ChatOpenAI:
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
# PII middleware
# ---------------------------------------------------------------------------


def get_pii_middleware() -> list[PIIMiddleware]:
    return [
        PIIMiddleware("email",       strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("credit_card", strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("ip",          strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("mac_address", strategy="mask", apply_to_input=True, apply_to_output=True),
    ]


def get_generation_agent():
    return create_agent(
        model=get_model(),
        tools=[],
        system_prompt=(
            "You are a concise assistant. Write a clear answer that addresses the user task. "
            "If feedback is provided, improve the answer based on it."
        ),
        middleware=get_pii_middleware(),
    )


def get_reflection_agent():
    return create_agent(
        model=get_model(),
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
    return exc.__class__.__name__ == "PIIDetectionError"


# ---------------------------------------------------------------------------
# Generation and reflection
# ---------------------------------------------------------------------------


def generate_answer(state: AgentState) -> dict:
    """Generate or improve the draft answer. Resets per-turn counters on new turns."""
    messages = state.get("messages", [])
    human_msg_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    current_turn = state.get("turn", 0)

    reset: dict = {}
    if human_msg_count > current_turn:
        task = next(
            (m.content if isinstance(m.content, str) else str(m.content)
             for m in reversed(messages) if isinstance(m, HumanMessage)),
            "",
        )
        reset = {
            "turn": human_msg_count,
            "task": task,
            "iteration": 0,
            "done": False,
            "blocked": False,
            "block_reason": "",
            "draft": "",
            "feedback": "",
        }
        logger.info("New turn %d: task=%r", human_msg_count, task[:80])

    task = reset.get("task", state.get("task", ""))
    feedback = reset.get("feedback", state.get("feedback", ""))
    draft = reset.get("draft", state.get("draft", ""))
    iteration = reset.get("iteration", state.get("iteration", 0))

    generator = get_generation_agent()

    if feedback:
        user_prompt = (
            f"Task: {task}\n\n"
            f"Current draft:\n{draft}\n\n"
            f"Feedback to address:\n{feedback}\n\n"
            "Return an improved draft only."
        )
    else:
        user_prompt = (
            f"Task: {task}\n\n"
            "Write the best possible first draft."
        )

    logger.info("Generating answer (iteration=%d)", iteration + 1)
    try:
        response = generator.invoke({"messages": [HumanMessage(content=user_prompt)]})
    except Exception as exc:
        if is_pii_detection_error(exc):
            logger.warning("PII detected in generation — blocking request")
            return {**_block_update(source="generate", reason=str(exc)), **reset}
        raise

    new_draft = extract_last_message_text(response)
    logger.info("Draft generated (%d chars)", len(new_draft))
    return {"draft": new_draft, "iteration": iteration + 1, **reset}


def reflect_on_answer(state: AgentState) -> dict:
    """Evaluate the current draft and mark done or set feedback."""
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


def route_after_reflect(
    state: AgentState,
) -> Literal["generate", "__end__"]:
    """Route after reflect: loop back to generate or terminate."""
    if state.get("done", False) or state.get("blocked", False):
        return "__end__"
    return "generate"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph() -> StateGraph:
    """Build and compile the v2 LangGraph self-reflection agent (no web search)."""
    graph = StateGraph(AgentState)
    graph.add_node("generate", generate_answer)
    graph.add_node("reflect",  reflect_on_answer)

    graph.set_entry_point("generate")
    graph.add_edge("generate", "reflect")
    graph.add_conditional_edges(
        "reflect",
        route_after_reflect,
        {"generate": "generate", "__end__": END},
    )

    return graph.compile()


app = build_graph()

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@traceable(
    name="run_agent_v2",
    run_type="chain",
    tags=["self-reflection", "agentic", "v2"],
    metadata={"agent_type": "reflection", "version": "2.0"},
)
def run_agent(task: str, max_iterations: int = _DEFAULT_MAX_ITERATIONS) -> AgentState:
    """Run the v2 self-reflection agent (no web search) on a task."""
    if not task or not task.strip():
        raise ValueError("Task must be a non-empty string")
    if not 1 <= max_iterations <= 10:
        raise ValueError("max_iterations must be between 1 and 10")

    logger.info("Starting agent v2: task=%r max_iterations=%d", task[:80], max_iterations)
    return app.invoke({"task": task.strip(), "max_iterations": max_iterations})


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Self-reflection agent v2 (no web search): generates and iteratively improves answers."
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
            "task":       result.get("task", ""),
            "draft":      result.get("draft", ""),
            "iterations": result.get("iteration", 0),
            "blocked":    result.get("blocked", False),
            "block_reason": result.get("block_reason", ""),
        }, indent=2))
    else:
        print("Task:")
        print(result.get("task", ""))
        print("\nFinal answer:")
        print(result.get("draft", ""))
        print(f"\nIterations used: {result.get('iteration', 0)}")
        if result.get("blocked"):
            print(f"\nBLOCKED: {result.get('block_reason', '')}")
