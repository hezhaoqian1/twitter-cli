"""FastAPI dependencies for transaction-scoped manager sessions."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..db.session import session_scope
from ..services.vault import VaultRuntime, VaultService


def get_db() -> Iterator[Session]:
    """Yield one request-scoped transaction and close it afterwards."""
    with session_scope() as session:
        yield session


def get_vault_runtime(request: Request) -> VaultRuntime:
    """Return the process-local Vault runtime owned by the FastAPI app."""
    return request.app.state.vault_runtime


def get_vault(
    request: Request,
    session: Session = Depends(get_db),
) -> VaultService:
    """Bind a request transaction to the shared in-memory Vault runtime."""
    return VaultService(session, runtime=get_vault_runtime(request))
