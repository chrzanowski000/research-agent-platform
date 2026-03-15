"""Tests for the persistence layer (query deduplication, cascade delete, run_id on sources)."""
from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agents.query_utils import normalize_query, make_slug
from research_persistence_api.models import Base, Query, Run, Source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    """In-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Point DATA_DIR at a temp directory so folder creation is isolated."""
    data_dir = str(tmp_path / "research")
    monkeypatch.setenv("DATA_DIR", data_dir)
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


# ---------------------------------------------------------------------------
# query_utils tests
# ---------------------------------------------------------------------------


def test_normalize_query_lowercases():
    assert normalize_query("Quantum SENSING") == "quantum sensing"


def test_normalize_query_strips_punctuation():
    assert normalize_query("hello, world!") == "hello world"


def test_normalize_query_collapses_whitespace():
    assert normalize_query("  lots   of   space  ") == "lots of space"


def test_make_slug_basic():
    slug = make_slug("quantum sensing 2024")
    assert "quantum" in slug
    assert " " not in slug


# ---------------------------------------------------------------------------
# find_or_create_query tests
# ---------------------------------------------------------------------------


def test_find_or_create_query_new(db, tmp_data_dir):
    from agents.persistence import find_or_create_query

    q = find_or_create_query(db, "Quantum Sensing")
    assert q.id is not None
    assert q.normalized_query == "quantum sensing"
    assert "quantum" in q.slug
    assert os.path.isdir(q.folder_path)


def test_find_or_create_query_dedup(db, tmp_data_dir):
    from agents.persistence import find_or_create_query

    q1 = find_or_create_query(db, "Quantum Sensing")
    q2 = find_or_create_query(db, "  quantum sensing!  ")
    assert q1.id == q2.id


def test_find_or_create_query_different_queries_get_different_records(db, tmp_data_dir):
    from agents.persistence import find_or_create_query

    q1 = find_or_create_query(db, "Quantum Sensing")
    q2 = find_or_create_query(db, "Machine Learning")
    assert q1.id != q2.id


# ---------------------------------------------------------------------------
# Run lifecycle tests
# ---------------------------------------------------------------------------


def test_create_run(db, tmp_data_dir):
    from agents.persistence import create_run, find_or_create_query

    q = find_or_create_query(db, "test topic")
    run = create_run(db, q)
    assert run.id is not None
    assert run.status == "running"
    assert run.started_at is not None


def test_complete_run(db, tmp_data_dir):
    from agents.persistence import complete_run, create_run, find_or_create_query

    q = find_or_create_query(db, "test topic")
    run = create_run(db, q)
    run = complete_run(db, run, "## Summary\ngreat stuff", {"start_date": "2024-01-01"})
    assert run.status == "completed"
    assert run.completed_at is not None
    assert "Summary" in run.summary_markdown
    assert "2024" in run.date_filter_json


def test_fail_run(db, tmp_data_dir):
    from agents.persistence import create_run, fail_run, find_or_create_query

    q = find_or_create_query(db, "test topic")
    run = create_run(db, q)
    run = fail_run(db, run, "Something went wrong")
    assert run.status == "failed"
    assert run.error_message == "Something went wrong"


# ---------------------------------------------------------------------------
# persist_sources: run_id on every source
# ---------------------------------------------------------------------------


SAMPLE_RESULTS = [
    {
        "title": "Paper A",
        "url": "https://example.com/a",
        "authors": ["Alice", "Bob"],
        "year": 2024,
        "abstract": "Great paper",
        "source": "semantic_scholar",
        "similarity_score": 0.85,
        "citation_count": 10,
    },
    {
        "title": "Paper B",
        "url": "https://example.com/b",
        "authors": ["Carol"],
        "year": 2023,
        "abstract": "Another paper",
        "source": "semantic_scholar",
        "similarity_score": 0.72,
        "citation_count": 5,
    },
]


def test_persist_sources_sets_run_id(db, tmp_data_dir):
    from agents.persistence import create_run, find_or_create_query, persist_sources

    q = find_or_create_query(db, "test topic")
    run = create_run(db, q)
    persist_sources(db, run, SAMPLE_RESULTS)

    sources = db.query(Source).filter(Source.run_id == run.id).all()
    assert len(sources) == 2
    for s in sources:
        assert s.run_id == run.id
        assert s.query_id == q.id


# ---------------------------------------------------------------------------
# Deletion tests
# ---------------------------------------------------------------------------


def test_delete_query_cascades(db, tmp_data_dir):
    from agents.persistence import (
        create_run,
        delete_query_and_artifacts,
        find_or_create_query,
        persist_sources,
    )

    q = find_or_create_query(db, "delete me")
    run = create_run(db, q)
    persist_sources(db, run, SAMPLE_RESULTS)

    qid = q.id
    delete_query_and_artifacts(db, qid)

    assert db.query(Query).filter(Query.id == qid).first() is None
    assert db.query(Run).filter(Run.query_id == qid).count() == 0
    assert db.query(Source).filter(Source.query_id == qid).count() == 0


def test_delete_query_removes_folder(db, tmp_data_dir):
    from agents.persistence import create_run, delete_query_and_artifacts, find_or_create_query

    q = find_or_create_query(db, "folder test")
    folder = q.folder_path
    assert os.path.isdir(folder)

    delete_query_and_artifacts(db, q.id)
    assert not os.path.exists(folder)
