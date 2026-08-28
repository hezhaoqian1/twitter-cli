"""FastAPI dependencies for transaction-scoped manager sessions."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from ..db.session import session_scope


def get_db() -> Iterator[Session]:
    """Yield one request-scoped transaction and close it afterwards."""
    with session_scope() as session:
        yield session
