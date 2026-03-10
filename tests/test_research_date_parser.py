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
