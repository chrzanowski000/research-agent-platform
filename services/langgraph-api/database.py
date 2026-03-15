"""Database engine, session factory, and initialization."""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/research.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

_initialized = False


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return
    Base.metadata.create_all(bind=engine)
    _initialized = True


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yield a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
