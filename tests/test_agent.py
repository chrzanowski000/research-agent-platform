"""Unit tests for agent.py — all external calls are mocked."""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out config before importing agent so tests don't need real API keys
# ---------------------------------------------------------------------------

_mock_cfg = MagicMock()
_mock_cfg.max_web_searches = 2
_mock_cfg.max_iterations = 3
_mock_cfg.model_name = "test-model"
_mock_cfg.base_url = "https://example.com"
_mock_cfg.openrouter_api_key = "test-key"
_mock_cfg.request_timeout = 30
_mock_cfg.tavily_api_key = "test-tavily-key"

sys.modules.setdefault("config", MagicMock(
    Config=MagicMock(from_env=MagicMock(return_value=_mock_cfg)),
    ConfigError=Exception,
))

# Patch cfg at module level before import
with patch.dict("sys.modules", {}):
    import agent  # noqa: E402

agent.cfg = _mock_cfg  # inject mock config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**overrides) -> agent.AgentState:
    base: agent.AgentState = {
        "task": "Explain gravity",
        "draft": "",
        "feedback": "",
        "iteration": 0,
        "max_iterations": 3,
        "done": False,
        "blocked": False,
        "block_reason": "",
        "web_context": "",
        "search_needed": False,
        "search_query": "",
        "web_search_count": 0,
    }
    base.update(overrides)
    return base

# ---------------------------------------------------------------------------
# mask_pii
# ---------------------------------------------------------------------------


def test_mask_pii_email():
    result = agent.mask_pii("Contact me at user@example.com please")
    assert "[EMAIL]" in result
    assert "user@example.com" not in result


def test_mask_pii_ip():
    result = agent.mask_pii("Server IP is 192.168.1.100")
    assert "[IP_ADDRESS]" in result
    assert "192.168.1.100" not in result


def test_mask_pii_no_match():
    text = "Nothing sensitive here"
    assert agent.mask_pii(text) == text

# ---------------------------------------------------------------------------
# parse_search_decision
# ---------------------------------------------------------------------------


def test_parse_search_decision_yes():
    text = "NEEDS_SEARCH: yes\nQUERY: quantum mechanics"
    needs, query = agent.parse_search_decision(text)
    assert needs is True
    assert query == "quantum mechanics"


def test_parse_search_decision_no():
    needs, query = agent.parse_search_decision("NEEDS_SEARCH: no\nQUERY:")
    assert needs is False
    assert query == ""


def test_parse_search_decision_missing_pattern():
    needs, query = agent.parse_search_decision("I think no search is needed")
    assert needs is False
    assert query == ""


def test_parse_search_decision_case_insensitive():
    needs, query = agent.parse_search_decision("needs_search: YES\nquery: latest news")
    assert needs is True
    assert query == "latest news"


def test_parse_search_decision_query_capped_at_200():
    long_query = "x" * 300
    text = f"NEEDS_SEARCH: yes\nQUERY: {long_query}"
    _, query = agent.parse_search_decision(text)
    assert len(query) == 200

# ---------------------------------------------------------------------------
# block_state
# ---------------------------------------------------------------------------


def test_block_state():
    state = _make_state()
    result = agent.block_state(state, source="web_search", reason="timeout")
    assert result["done"] is True
    assert result["blocked"] is True
    assert "web_search" in result["block_reason"]
    assert "timeout" in result["block_reason"]
    assert "blocked" in result["draft"].lower() or "Request blocked" in result["draft"]

# ---------------------------------------------------------------------------
# generate_answer
# ---------------------------------------------------------------------------


def test_generate_answer_first_draft():
    state = _make_state()
    with patch.object(agent, "_invoke_llm_with_retry", return_value="Draft answer") as mock_llm:
        result = agent.generate_answer(state)
    assert result["draft"] == "Draft answer"
    assert result["iteration"] == 1
    mock_llm.assert_called_once()


def test_generate_answer_with_feedback():
    state = _make_state(draft="Old draft", feedback="Add more detail", iteration=1)
    with patch.object(agent, "_invoke_llm_with_retry", return_value="Improved draft"):
        result = agent.generate_answer(state)
    assert result["draft"] == "Improved draft"
    assert result["iteration"] == 2

# ---------------------------------------------------------------------------
# reflect_on_answer
# ---------------------------------------------------------------------------


def test_reflect_approved():
    state = _make_state(draft="Good answer", iteration=1)
    with patch.object(agent, "_invoke_llm_with_retry", return_value="APPROVED"):
        result = agent.reflect_on_answer(state)
    assert result["done"] is True
    assert result["feedback"] == ""


def test_reflect_approved_case_insensitive():
    state = _make_state(draft="Good answer", iteration=1)
    with patch.object(agent, "_invoke_llm_with_retry", return_value="  approved  "):
        result = agent.reflect_on_answer(state)
    assert result["done"] is True


def test_reflect_feedback():
    state = _make_state(draft="Weak answer", iteration=1)
    with patch.object(agent, "_invoke_llm_with_retry", return_value="- Add examples\n- Improve clarity"):
        result = agent.reflect_on_answer(state)
    assert result["done"] is False
    assert "Add examples" in result["feedback"]


def test_reflect_max_iterations_stops():
    state = _make_state(draft="Answer", iteration=3, max_iterations=3)
    with patch.object(agent, "_invoke_llm_with_retry", return_value="- Still needs work"):
        result = agent.reflect_on_answer(state)
    assert result["done"] is True
    assert result["feedback"] == ""

# ---------------------------------------------------------------------------
# route_after_search_decision
# ---------------------------------------------------------------------------


def test_route_after_search_decision_blocked():
    state = _make_state(blocked=True)
    assert agent.route_after_search_decision(state) == "__end__"


def test_route_after_search_decision_search_needed():
    state = _make_state(search_needed=True)
    assert agent.route_after_search_decision(state) == "web_search"


def test_route_after_search_decision_generate():
    state = _make_state()
    assert agent.route_after_search_decision(state) == "generate"

# ---------------------------------------------------------------------------
# route_after_reflect
# ---------------------------------------------------------------------------


def test_route_after_reflect_done():
    state = _make_state(done=True)
    assert agent.route_after_reflect(state) == "__end__"


def test_route_after_reflect_blocked():
    state = _make_state(blocked=True)
    assert agent.route_after_reflect(state) == "__end__"


def test_route_after_reflect_continue():
    state = _make_state(done=False)
    assert agent.route_after_reflect(state) == "search_decision"

# ---------------------------------------------------------------------------
# run_agent input validation
# ---------------------------------------------------------------------------


def test_run_agent_empty_task():
    with pytest.raises(ValueError, match="non-empty"):
        agent.run_agent("")


def test_run_agent_whitespace_task():
    with pytest.raises(ValueError, match="non-empty"):
        agent.run_agent("   ")


def test_run_agent_invalid_max_iterations_zero():
    with pytest.raises(ValueError, match="max_iterations"):
        agent.run_agent("task", max_iterations=0)


def test_run_agent_invalid_max_iterations_too_high():
    with pytest.raises(ValueError, match="max_iterations"):
        agent.run_agent("task", max_iterations=11)


def test_run_agent_integration():
    """Full graph run with all external calls mocked."""
    with (
        patch.object(agent, "_invoke_llm_with_retry", side_effect=[
            "NEEDS_SEARCH: no\nQUERY:",  # search_decision
            "Draft answer about gravity",  # generate_answer
            "APPROVED",  # reflect_on_answer
        ]),
    ):
        result = agent.run_agent("Explain gravity", max_iterations=3)

    assert result["draft"] == "Draft answer about gravity"
    assert result["iteration"] == 1
    assert result["done"] is True
    assert result["blocked"] is False
