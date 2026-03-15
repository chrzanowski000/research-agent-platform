"""Persistence service for research runs: DB writes and disk artifacts."""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from agents.query_utils import make_slug, normalize_query
from research_persistence_api.database import SessionLocal
from research_persistence_api.models import Query, Run, Source

logger = logging.getLogger(__name__)

DATA_DIR = os.getenv("DATA_DIR", "data/research")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def find_or_create_query(db: Session, raw: str) -> Query:
    """Find existing query by normalized form or create a new one with folder."""
    normalized = normalize_query(raw)
    query = db.query(Query).filter(Query.normalized_query == normalized).first()
    if query:
        query.updated_at = _now()
        db.commit()
        db.refresh(query)
        return query

    slug = make_slug(normalized)
    # Ensure slug uniqueness by appending counter if needed
    base_slug = slug
    counter = 1
    while db.query(Query).filter(Query.slug == slug).first():
        slug = f"{base_slug}-{counter}"
        counter += 1

    folder_path = os.path.join(DATA_DIR, slug)
    os.makedirs(os.path.join(folder_path, "runs"), exist_ok=True)

    now = _now()
    query = Query(
        raw_query=raw,
        normalized_query=normalized,
        slug=slug,
        folder_path=folder_path,
        created_at=now,
        updated_at=now,
    )
    db.add(query)
    db.commit()
    db.refresh(query)
    return query


def create_run(db: Session, query: Query) -> Run:
    """Create a new run record with status 'running'."""
    now = _now()
    run = Run(
        query_id=query.id,
        status="running",
        started_at=now,
        created_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def complete_run(db: Session, run: Run, synthesis: str, date_filter: dict | None) -> Run:
    """Mark run as completed and store summary + date filter."""
    run.status = "completed"
    run.completed_at = _now()
    run.summary_markdown = synthesis
    run.date_filter_json = json.dumps(date_filter) if date_filter else None
    db.commit()
    db.refresh(run)
    return run


def fail_run(db: Session, run: Run, error: str) -> Run:
    """Mark run as failed and store error message."""
    run.status = "failed"
    run.completed_at = _now()
    run.error_message = error
    db.commit()
    db.refresh(run)
    return run


def persist_sources(db: Session, run: Run, search_results: list[dict]) -> None:
    """Insert one Source row per result dict from ResearchState.search_results."""
    now = _now()
    for result in search_results:
        authors = result.get("authors", [])
        if isinstance(authors, list):
            authors_json = json.dumps([str(a) for a in authors])
        else:
            authors_json = json.dumps([str(authors)])

        # Build metadata dict from remaining fields
        metadata = {}
        for key in ("citation_count", "venue", "fields_of_study", "external_ids"):
            if key in result:
                metadata[key] = result[key]

        source = Source(
            run_id=run.id,
            query_id=run.query_id,
            source_type=result.get("source"),
            title=result.get("title"),
            authors_json=authors_json,
            publication_date=result.get("publication_date"),
            url=result.get("url"),
            snippet=result.get("snippet"),
            similarity_score=result.get("similarity_score"),
            metadata_json=json.dumps(metadata) if metadata else None,
            created_at=now,
        )
        db.add(source)
    db.commit()


def write_disk_artifacts(run: Run, query: Query, state: dict) -> None:
    """Write latest-summary.md, latest-sources.json, and runs/run_{id}.json."""
    folder = query.folder_path
    os.makedirs(os.path.join(folder, "runs"), exist_ok=True)

    # latest-summary.md
    summary = state.get("synthesis") or ""
    with open(os.path.join(folder, "latest-summary.md"), "w", encoding="utf-8") as f:
        f.write(summary)

    # latest-sources.json — each source dict gets run_id added
    sources = state.get("search_results") or []
    sources_with_run = [dict(s, run_id=run.id) for s in sources]
    with open(os.path.join(folder, "latest-sources.json"), "w", encoding="utf-8") as f:
        json.dump(sources_with_run, f, ensure_ascii=False, indent=2, default=str)

    # runs/run_{id}.json — snapshot of the full state (exclude messages for size)
    snapshot = {k: v for k, v in state.items() if k != "messages"}
    snapshot["run_id"] = run.id
    with open(os.path.join(folder, "runs", f"run_{run.id}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2, default=str)


def delete_query_and_artifacts(db: Session, query_id: int) -> None:
    """Delete DB records (cascade) and remove folder from disk."""
    query = db.query(Query).filter(Query.id == query_id).first()
    if not query:
        return
    folder_path = query.folder_path
    db.delete(query)
    db.commit()
    shutil.rmtree(folder_path, ignore_errors=True)
