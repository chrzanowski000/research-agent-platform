"""Unit tests for parse_dates node in research_agent."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Stub config before importing agent
_mock_cfg = MagicMock()
_mock_cfg.duckling_url = "http://localhost:8000"

sys.modules.setdefault("config", MagicMock(
    Config=MagicMock(from_env=MagicMock(return_value=_mock_cfg)),
    ConfigError=Exception,
))

from langchain_core.messages import HumanMessage  # noqa: E402

import agents.research_agent as ra  # noqa: E402
ra.cfg = _mock_cfg

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def _duckling_interval_response():
    """Simulate duckling returning a time interval."""
    return [
        {
            "dim": "time",
            "value": {
                "type": "interval",
                "from": {"value": "2026-01-01T00:00:00.000Z", "grain": "day"},
                "to": {"value": "2026-01-16T00:00:00.000Z", "grain": "day"},
            },
        }
    ]


def _duckling_point_year_response():
    return [
        {
            "dim": "time",
            "value": {
                "type": "value",
                "value": "2024-01-01T00:00:00.000Z",
                "grain": "year",
            },
        }
    ]


def _duckling_interval_years_response():
    # Duckling returns "2027-01-01" as the EXCLUSIVE upper bound for "2024-2026"
    return [
        {
            "dim": "time",
            "value": {
                "type": "interval",
                "from": {"value": "2024-01-01T00:00:00.000Z", "grain": "year"},
                "to": {"value": "2027-01-01T00:00:00.000Z", "grain": "year"},
            },
        }
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_dates_interval():
    state = {
        "messages": [HumanMessage(content="nuclear energy from 1 january 2026 to 15 january 2026")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = _duckling_interval_response()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    assert result["date_filter"]["start_date"] == "2026-01-01"
    assert result["date_filter"]["end_date"] == "2026-01-15"


def test_parse_dates_single_year():
    state = {
        "messages": [HumanMessage(content="nuclear energy in 2024")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = _duckling_point_year_response()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    assert result["date_filter"]["start_date"] == "2024-01-01"
    assert result["date_filter"]["end_date"] == "2024-12-31"


def test_parse_dates_year_range():
    state = {
        "messages": [HumanMessage(content="nuclear energy publications in 2024-2026")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = _duckling_interval_years_response()
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    # exclusive 2027-01-01 → subtract 1 day → 2026-12-31 → year-grain end → 2026-12-31
    assert result["date_filter"]["start_date"] == "2024-01-01"
    assert result["date_filter"]["end_date"] == "2026-12-31"


def test_parse_dates_no_result():
    state = {
        "messages": [HumanMessage(content="tell me about nuclear energy")],
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = []
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.post", return_value=mock_resp):
        result = ra.parse_dates(state)

    assert result["date_filter"] == {}


def test_parse_dates_http_error():
    state = {
        "messages": [HumanMessage(content="nuclear energy in 2025")],
    }
    with patch("httpx.post", side_effect=Exception("connection refused")):
        result = ra.parse_dates(state)

    assert result["date_filter"] == {}


def test_parse_dates_no_messages():
    state = {"messages": []}
    result = ra.parse_dates(state)
    assert result["date_filter"] == {}


# ---------------------------------------------------------------------------
# apply_date_filter tests
# ---------------------------------------------------------------------------


def test_apply_date_filter_embeds_date_clause():
    state = {
        "arxiv_queries": ["nuclear reactor safety", "fusion energy"],
        "date_filter": {"start_date": "2026-01-01", "end_date": "2026-01-15"},
        "max_searches": 5,
    }
    result = ra.apply_date_filter(state)
    plan = result["search_plan"]
    assert len(plan) == 2
    for task in plan:
        assert task["source"] == "arxiv"
        assert "+AND+submittedDate:[202601010000+TO+202601152359]" in task["query"]


def test_apply_date_filter_no_date_filter():
    state = {
        "arxiv_queries": ["neural networks", "deep learning"],
        "date_filter": {},
        "max_searches": 5,
    }
    result = ra.apply_date_filter(state)
    plan = result["search_plan"]
    assert len(plan) == 2
    assert plan[0]["query"] == "neural networks"
    assert plan[1]["query"] == "deep learning"
    for task in plan:
        assert "submittedDate" not in task["query"]


def test_apply_date_filter_respects_max_searches():
    state = {
        "arxiv_queries": ["a", "b", "c", "d", "e"],
        "date_filter": {},
        "max_searches": 3,
    }
    result = ra.apply_date_filter(state)
    assert len(result["search_plan"]) == 3


def test_apply_date_filter_no_queries_blocks():
    state = {
        "arxiv_queries": [],
        "topic": "",
        "date_filter": {},
        "max_searches": 5,
    }
    result = ra.apply_date_filter(state)
    assert result["blocked"] is True
    assert result["search_plan"] == []


# ---------------------------------------------------------------------------
# validate_date_range tests
# ---------------------------------------------------------------------------


def test_validate_date_range_keeps_in_range():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "arxiv", "title": "Paper A", "url": "https://arxiv.org/abs/2403.12345", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result["search_results"][0]["title"] == "Paper A"


def test_validate_date_range_removes_out_of_range():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "arxiv", "title": "Old Paper", "url": "https://arxiv.org/abs/2312.99999", "snippet": ""},
            {"source": "arxiv", "title": "In Range", "url": "https://arxiv.org/abs/2403.12345", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert len(result["search_results"]) == 1
    assert result["search_results"][0]["title"] == "In Range"


def test_validate_date_range_no_date_filter_returns_empty():
    state = {
        "date_filter": {},
        "search_results": [
            {"source": "arxiv", "title": "Any Paper", "url": "https://arxiv.org/abs/2403.12345", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result == {}


def test_validate_date_range_all_removed_returns_originals():
    state = {
        "date_filter": {"start_date": "2025-01-01", "end_date": "2025-12-31"},
        "search_results": [
            {"source": "arxiv", "title": "Old", "url": "https://arxiv.org/abs/2101.00001", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    # Returns {} so state is left unchanged (originals preserved)
    assert result == {}


def test_validate_date_range_keeps_non_arxiv():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "web", "title": "Web Result", "url": "https://example.com/page", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result["search_results"][0]["title"] == "Web Result"


def test_validate_date_range_unparseable_url_kept():
    state = {
        "date_filter": {"start_date": "2024-01-01", "end_date": "2024-06-30"},
        "search_results": [
            {"source": "arxiv", "title": "Old Format", "url": "https://arxiv.org/abs/math/0501234", "snippet": ""},
        ],
    }
    result = ra.validate_date_range(state)
    assert result["search_results"][0]["title"] == "Old Format"


# ---------------------------------------------------------------------------
# extract_research_intent / generate_semantic_queries / normalize_queries / rank_results_by_similarity tests
# ---------------------------------------------------------------------------


def test_extract_research_intent_returns_valid_schema():
    """Intent extraction returns a dict with all three required keys as lists."""
    state = {
        "messages": [HumanMessage(content="quantum parameter estimation using machine learning")],
        "turn": 0,
    }
    mock_resp = MagicMock()
    mock_resp.content = (
        '{"problem_domains": ["quantum parameter estimation", "quantum metrology"], '
        '"methods": ["machine learning", "neural networks"], '
        '"related_concepts": ["quantum sensing"]}'
    )
    with patch.object(ra, "get_topic_extractor_model", return_value=MagicMock(invoke=MagicMock(return_value=mock_resp))):
        result = ra.extract_research_intent(state)

    intent = result["research_intent"]
    assert isinstance(intent.get("problem_domains"), list)
    assert isinstance(intent.get("methods"), list)
    assert isinstance(intent.get("related_concepts"), list)
    assert len(intent["problem_domains"]) >= 1


def test_generate_semantic_queries_include_problem_domain():
    """Semantic queries produced by generate_semantic_queries include at least one problem domain term."""
    state = {
        "research_intent": {
            "problem_domains": ["quantum metrology"],
            "methods": ["machine learning"],
            "related_concepts": [],
        },
        "topics": [],
    }
    mock_resp = MagicMock()
    mock_resp.content = '["quantum metrology machine learning", "quantum metrology neural networks"]'
    with patch.object(ra, "get_keyword_expander_model", return_value=MagicMock(invoke=MagicMock(return_value=mock_resp))):
        result = ra.generate_semantic_queries(state)

    queries = result["expanded_keywords"]
    assert any("quantum metrology" in q for q in queries)


def test_normalize_queries_deduplicates():
    """Query normalization removes duplicate strings from LLM output."""
    state = {
        "expanded_keywords": ["quantum sensing machine learning"],
        "research_intent": {"problem_domains": ["quantum sensing"], "methods": [], "related_concepts": []},
        "max_searches": 5,
        "topic": "quantum sensing",
    }
    mock_resp = MagicMock()
    # LLM returns duplicates
    mock_resp.content = (
        '["quantum sensing machine learning", "quantum sensing machine learning", '
        '"quantum sensing neural network"]'
    )
    with patch.object(ra, "get_query_generator_model", return_value=MagicMock(invoke=MagicMock(return_value=mock_resp))):
        result = ra.normalize_queries(state)

    queries = result["arxiv_queries"]
    assert len(queries) == len(set(queries)), "Duplicate queries were not removed"


def test_rank_results_by_similarity_removes_low_similarity_paper():
    """Embedding-based ranking drops papers whose cosine similarity is below threshold."""
    state = {
        "topic": "quantum sensing",
        "search_results": [
            {"source": "arxiv", "title": "Relevant Paper", "url": "u1", "snippet": "quantum sensing stuff"},
            {"source": "arxiv", "title": "Unrelated Paper", "url": "u2", "snippet": "cooking recipes"},
        ],
    }
    # query_emb and relevant_emb are aligned (high dot product); unrelated_emb is not
    query_emb = [1.0, 0.0]
    relevant_emb = [1.0, 0.0]   # sim = 1.0 → kept
    unrelated_emb = [0.0, 1.0]  # sim = 0.0 → dropped

    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = query_emb
    mock_embedder.embed_documents.return_value = [relevant_emb, unrelated_emb]

    with patch.object(ra, "get_embedding_model", return_value=mock_embedder):
        result = ra.rank_results_by_similarity(state)

    assert len(result["search_results"]) == 1
    assert result["search_results"][0]["title"] == "Relevant Paper"


def test_node_pipeline_state_propagation():
    """extract_research_intent → generate_semantic_queries → normalize_queries passes state correctly."""
    messages = [HumanMessage(content="quantum error correction")]

    # --- extract_research_intent ---
    mock_intent_resp = MagicMock()
    mock_intent_resp.content = (
        '{"problem_domains": ["quantum error correction"], '
        '"methods": ["stabilizer codes"], "related_concepts": ["fault tolerance"]}'
    )
    with patch.object(ra, "get_topic_extractor_model", return_value=MagicMock(invoke=MagicMock(return_value=mock_intent_resp))):
        s1 = ra.extract_research_intent({"messages": messages, "turn": 0})

    assert "research_intent" in s1
    assert "topics" in s1

    # --- generate_semantic_queries ---
    mock_kw_resp = MagicMock()
    mock_kw_resp.content = '["quantum error correction stabilizer codes", "fault tolerant quantum computation"]'
    with patch.object(ra, "get_keyword_expander_model", return_value=MagicMock(invoke=MagicMock(return_value=mock_kw_resp))):
        s2 = ra.generate_semantic_queries({**s1})

    assert "expanded_keywords" in s2
    assert len(s2["expanded_keywords"]) >= 1

    # --- normalize_queries ---
    mock_q_resp = MagicMock()
    mock_q_resp.content = '["quantum error correction stabilizer", "fault tolerance quantum codes"]'
    with patch.object(ra, "get_query_generator_model", return_value=MagicMock(invoke=MagicMock(return_value=mock_q_resp))):
        s3 = ra.normalize_queries({**s1, **s2, "max_searches": 5})

    assert "arxiv_queries" in s3
    assert len(s3["arxiv_queries"]) >= 1
