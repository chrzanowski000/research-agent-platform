from __future__ import annotations

import os
import re
from typing import Literal, TypedDict

from langchain.agents import create_agent
from langchain.agents.middleware import PIIMiddleware
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from dotenv import load_dotenv
from langsmith import traceable
from tavily import TavilyClient


load_dotenv()


def configure_langsmith() -> None:
    if os.getenv("LANGCHAIN_API_KEY") and not os.getenv("LANGSMITH_API_KEY"):
        os.environ["LANGSMITH_API_KEY"] = os.environ["LANGCHAIN_API_KEY"]

    if os.getenv("LANGCHAIN_PROJECT") and not os.getenv("LANGSMITH_PROJECT"):
        os.environ["LANGSMITH_PROJECT"] = os.environ["LANGCHAIN_PROJECT"]

    has_api_key = bool(os.getenv("LANGSMITH_API_KEY"))
    tracing_flag_set = os.getenv("LANGSMITH_TRACING") or os.getenv("LANGCHAIN_TRACING_V2")
    if has_api_key and not tracing_flag_set:
        os.environ["LANGSMITH_TRACING"] = "true"

    if not os.getenv("LANGSMITH_PROJECT"):
        os.environ["LANGSMITH_PROJECT"] = "self-reflection-agent"


configure_langsmith()


class AgentState(TypedDict):
    task: str
    draft: str
    feedback: str
    iteration: int
    max_iterations: int
    done: bool
    blocked: bool
    block_reason: str
    web_context: str
    search_needed: bool
    search_query: str
    web_search_count: int
    max_web_searches: int


def normalize_state(state: AgentState) -> AgentState:
    state.setdefault("task", "")
    state.setdefault("draft", "")
    state.setdefault("feedback", "")
    state.setdefault("iteration", 0)
    state.setdefault("max_iterations", 3)
    state.setdefault("done", False)
    state.setdefault("blocked", False)
    state.setdefault("block_reason", "")
    state.setdefault("web_context", "")
    state.setdefault("search_needed", False)
    state.setdefault("search_query", "")
    state.setdefault("web_search_count", 0)
    state.setdefault("max_web_searches", 2)
    return state


def block_state(state: AgentState, source: str, reason: str) -> AgentState:
    state["done"] = True
    state["blocked"] = True
    state["feedback"] = ""
    state["block_reason"] = (
        f"Blocked: sensitive or personal information detected in {source}. "
        f"Reason: {reason}"
    )
    state["draft"] = (
        "Request blocked for safety. Please remove or anonymize sensitive/personal "
        f"data and retry. ({state['block_reason']})"
    )
    return state


def get_model() -> ChatOpenAI:
    model_name = os.getenv("MODEL_NAME", "nvidia/nemotron-nano-9b-v2:free")
    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    api_key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
    return ChatOpenAI(model=model_name, temperature=0, base_url=base_url, api_key=api_key)


def parse_search_decision(text: str) -> tuple[bool, str]:
    needs_search = False
    query = ""

    needs_match = re.search(r"NEEDS_SEARCH:\s*(yes|no)", text, re.IGNORECASE)
    if needs_match:
        needs_search = needs_match.group(1).lower() == "yes"

    query_match = re.search(r"QUERY:\s*(.+)", text, re.IGNORECASE)
    if query_match:
        query = query_match.group(1).strip()

    return needs_search, query


def get_tavily_client() -> TavilyClient:
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        raise ValueError("Missing TAVILY_API_KEY")
    return TavilyClient(api_key=api_key)


def maybe_decide_search(state: AgentState, phase: str) -> AgentState:
    state = normalize_state(state)
    state["search_needed"] = False
    state["search_query"] = ""

    if state["web_search_count"] >= state["max_web_searches"]:
        return state

    llm = get_model()
    decision_prompt = (
        "Decide if internet search is needed.\n"
        f"Phase: {phase}\n"
        f"Task: {state['task']}\n\n"
        f"Current draft:\n{state['draft']}\n\n"
        f"Reviewer feedback:\n{state['feedback']}\n\n"
        f"Current web context:\n{state['web_context']}\n\n"
        "Return exactly:\n"
        "NEEDS_SEARCH: yes|no\n"
        "QUERY: <short search query or empty>\n"
        "Use NEEDS_SEARCH=yes for tasks needing current events, factual verification, or missing external data."
    )
    raw = llm.invoke([HumanMessage(content=decision_prompt)]).content
    if not isinstance(raw, str):
        raw = str(raw)
    needs_search, query = parse_search_decision(raw)

    if needs_search:
        state["search_needed"] = True
        state["search_query"] = query if query else state["task"]
    return state


@traceable(name="search_decision", run_type="chain")
def search_decision(state: AgentState) -> AgentState:
    phase = "before_generate" if normalize_state(state)["iteration"] == 0 else "after_reflect"
    return maybe_decide_search(state, phase=phase)


@traceable(name="tavily_web_search", run_type="chain")
def tavily_web_search(state: AgentState) -> AgentState:
    state = normalize_state(state)
    if not state["search_needed"]:
        return state

    try:
        client = get_tavily_client()
        result = client.search(
            query=state["search_query"] or state["task"],
            topic="general",
            max_results=5,
            search_depth="advanced",
        )
    except Exception as exc:
        return block_state(state, source="web search", reason=str(exc))

    snippets: list[str] = []
    for item in result.get("results", []):
        title = item.get("title", "")
        url = item.get("url", "")
        content = item.get("content", "")
        snippets.append(f"- {title}\n  URL: {url}\n  Snippet: {content}")

    search_block = (
        f"Search query: {state['search_query'] or state['task']}\n"
        "Results:\n"
        + ("\n".join(snippets) if snippets else "- No results returned.")
    )

    state["web_context"] = (state["web_context"] + "\n\n" + search_block).strip()
    state["web_search_count"] += 1
    state["search_needed"] = False
    state["search_query"] = ""
    state["done"] = False
    return state


def get_pii_middleware() -> list[PIIMiddleware]:
    # Built-in all set to partial masking.
    return [
        PIIMiddleware("email", strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("credit_card", strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("ip", strategy="mask", apply_to_input=True, apply_to_output=True),
        PIIMiddleware("mac_address", strategy="mask", apply_to_input=True, apply_to_output=True),
    ]


def extract_last_message_text(result: dict) -> str:
    messages = result.get("messages", [])
    if not messages:
        return ""

    last = messages[-1]
    content = getattr(last, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip()


def is_pii_detection_error(exc: Exception) -> bool:
    return exc.__class__.__name__ == "PIIDetectionError"


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


@traceable(name="generate_answer", run_type="chain")
def generate_answer(state: AgentState) -> AgentState:
    state = normalize_state(state)
    generator = get_generation_agent()

    if state["feedback"]:
        user_prompt = (
            f"Task: {state['task']}\n\n"
            f"Web context (if any):\n{state['web_context']}\n\n"
            f"Current draft:\n{state['draft']}\n\n"
            f"Feedback to address:\n{state['feedback']}\n\n"
            "Return an improved draft only."
        )
    else:
        user_prompt = (
            f"Task: {state['task']}\n\n"
            f"Web context (if any):\n{state['web_context']}\n\n"
            "Write the best possible first draft."
        )

    try:
        response = generator.invoke({"messages": [HumanMessage(content=user_prompt)]})
    except Exception as exc:
        if is_pii_detection_error(exc):
            # If masking middleware raises unexpectedly, continue with a fallback call.
            response = {
                "messages": [
                    get_model().invoke([HumanMessage(content=user_prompt)])
                ]
            }
            state["blocked"] = False
            state["block_reason"] = ""
        else:
            raise

    state["draft"] = extract_last_message_text(response)
    state["iteration"] += 1
    return state


@traceable(name="reflect_on_answer", run_type="chain")
def reflect_on_answer(state: AgentState) -> AgentState:
    state = normalize_state(state)
    reviewer = get_reflection_agent()
    review_prompt = (
        f"Task: {state['task']}\n\n"
        f"Draft:\n{state['draft']}\n\n"
        "Review now."
    )

    try:
        result = reviewer.invoke({"messages": [HumanMessage(content=review_prompt)]})
    except Exception as exc:
        if is_pii_detection_error(exc):
            # If masking middleware raises unexpectedly, continue with a fallback call.
            result = {
                "messages": [
                    get_model().invoke([HumanMessage(content=review_prompt)])
                ]
            }
            state["blocked"] = False
            state["block_reason"] = ""
        else:
            raise
    review = extract_last_message_text(result)

    if review == "APPROVED" or state["iteration"] >= state["max_iterations"]:
        state["done"] = True
        state["feedback"] = ""
    else:
        state["done"] = False
        state["feedback"] = review

    return state


def should_continue(state: AgentState) -> str:
    state = normalize_state(state)
    return END if state["done"] or state["blocked"] else "generate"


def route_after_search_decision(
    state: AgentState,
) -> Literal["web_search", "generate", "__end__"]:
    state = normalize_state(state)
    if state["blocked"]:
        return END
    if state["search_needed"]:
        return "web_search"
    return END if state["done"] else "generate"


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("search_decision", search_decision)
    graph.add_node("web_search", tavily_web_search)
    graph.add_node("generate", generate_answer)
    graph.add_node("reflect", reflect_on_answer)
    

    graph.set_entry_point("search_decision")
    graph.add_conditional_edges(
        "search_decision",
        route_after_search_decision,
        {"web_search": "web_search", "generate": "generate", "__end__": END},
    )
    graph.add_edge("web_search", "generate")
    graph.add_edge("generate", "reflect")
    graph.add_edge("reflect", "search_decision")

    return graph.compile()


app = build_graph()


@traceable(name="run_agent", run_type="chain")
def run_agent(task: str, max_iterations: int = 3) -> AgentState:
    initial_state: AgentState = {
        "task": task,
        "draft": "",
        "feedback": "",
        "iteration": 0,
        "max_iterations": max_iterations,
        "done": False,
        "blocked": False,
        "block_reason": "",
        "web_context": "",
        "search_needed": False,
        "search_query": "",
        "web_search_count": 0,
        "max_web_searches": 2,
    }
    return app.invoke(initial_state)


if __name__ == "__main__":
    prompt = "Explain Basics of quantum mechanics"
    result = run_agent(prompt, max_iterations=3)

    print("Task:")
    print(result["task"])
    print("\nFinal answer:")
    print(result["draft"])
    print("\nIterations used:", result["iteration"])
