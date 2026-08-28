"""FastAPI dependencies for transaction-scoped manager sessions."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..config import get_settings
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


def get_redis_client(request: Request):
    """Return the app-owned Redis client, creating it only for runtime reads."""
    client = getattr(request.app.state, "redis_client", None)
    if client is not None:
        return client

    import redis

    runtime = getattr(request.app.state, "settings", get_settings())
    client = redis.Redis.from_url(runtime.redis_url, decode_responses=True)
    request.app.state.redis_client = client
    return client
