"""Pydantic response models for the research persistence API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SourceOut(BaseModel):
    id: int
    run_id: int
    query_id: int
    source_type: str | None
    title: str | None
    authors: list[str]
    publication_date: str | None
    url: str | None
    snippet: str | None
    similarity_score: float | None

    model_config = {"from_attributes": True}


class RunOut(BaseModel):
    id: int
    query_id: int
    status: str
    started_at: datetime
    completed_at: datetime | None
    error_message: str | None
    summary_markdown: str | None
    sources: list[SourceOut] = []

    model_config = {"from_attributes": True}


class QueryOut(BaseModel):
    id: int
    raw_query: str
    slug: str
    folder_path: str
    created_at: datetime
    updated_at: datetime
    run_count: int
    last_run_at: datetime | None

    model_config = {"from_attributes": True}


class QueryDetailOut(QueryOut):
    runs: list[RunOut] = []
