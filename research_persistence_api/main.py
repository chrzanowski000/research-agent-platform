"""FastAPI app for browsing and deleting persisted research runs."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from research_persistence_api.database import get_db, init_db
from research_persistence_api.models import Query, Run, Source
from research_persistence_api.schemas import QueryDetailOut, QueryOut, RunOut, SourceOut


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    os.makedirs(os.getenv("DATA_DIR", "data/research"), exist_ok=True)
    yield


app = FastAPI(title="Research Persistence API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _source_to_out(source: Source) -> SourceOut:
    authors: list[str] = []
    if source.authors_json:
        try:
            authors = json.loads(source.authors_json)
        except Exception:
            authors = []
    return SourceOut(
        id=source.id,
        run_id=source.run_id,
        query_id=source.query_id,
        source_type=source.source_type,
        title=source.title,
        authors=authors,
        publication_date=source.publication_date,
        url=source.url,
        snippet=source.snippet,
        similarity_score=source.similarity_score,
    )


def _run_to_out(run: Run, include_sources: bool = False) -> RunOut:
    sources = [_source_to_out(s) for s in run.sources] if include_sources else []
    return RunOut(
        id=run.id,
        query_id=run.query_id,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
        summary_markdown=run.summary_markdown,
        sources=sources,
    )


@app.get("/research/queries", response_model=list[QueryOut])
def list_queries(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    queries = db.query(Query).order_by(Query.updated_at.desc()).offset(skip).limit(limit).all()
    result = []
    for q in queries:
        run_count = db.query(func.count(Run.id)).filter(Run.query_id == q.id).scalar() or 0
        last_run = (
            db.query(Run.started_at)
            .filter(Run.query_id == q.id)
            .order_by(Run.started_at.desc())
            .first()
        )
        result.append(
            QueryOut(
                id=q.id,
                raw_query=q.raw_query,
                slug=q.slug,
                folder_path=q.folder_path,
                created_at=q.created_at,
                updated_at=q.updated_at,
                run_count=run_count,
                last_run_at=last_run[0] if last_run else None,
            )
        )
    return result


@app.get("/research/queries/{query_id}", response_model=QueryDetailOut)
def get_query(query_id: int, db: Session = Depends(get_db)):
    q = db.query(Query).filter(Query.id == query_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Query not found")
    runs = db.query(Run).filter(Run.query_id == q.id).order_by(Run.started_at.desc()).all()
    run_count = len(runs)
    last_run_at = runs[0].started_at if runs else None
    return QueryDetailOut(
        id=q.id,
        raw_query=q.raw_query,
        slug=q.slug,
        folder_path=q.folder_path,
        created_at=q.created_at,
        updated_at=q.updated_at,
        run_count=run_count,
        last_run_at=last_run_at,
        runs=[_run_to_out(r, include_sources=False) for r in runs],
    )


@app.get("/research/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_to_out(run, include_sources=True)


@app.delete("/research/runs/{run_id}", status_code=204)
def delete_run(run_id: int, db: Session = Depends(get_db)):
    from agents.persistence import delete_run_and_artifacts
    delete_run_and_artifacts(db, run_id)


@app.delete("/research/queries/{query_id}", status_code=204)
def delete_query(query_id: int, db: Session = Depends(get_db)):
    from agents.persistence import delete_query_and_artifacts
    delete_query_and_artifacts(db, query_id)
