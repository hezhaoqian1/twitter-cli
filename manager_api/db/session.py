"""SQLAlchemy engine and transaction-scoped session helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import ManagerSettings, get_settings


def build_engine(settings: ManagerSettings | None = None) -> Engine:
    """Build an engine without creating a global connection at import time."""
    runtime = settings or get_settings()
    return create_engine(runtime.sqlalchemy_url, pool_pre_ping=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine after environment configuration is loaded."""
    return build_engine()


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a transaction factory for the supplied engine."""
    return sessionmaker(bind=engine or get_engine(), autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """Commit once on success and roll back every failed transaction."""
    session = session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
