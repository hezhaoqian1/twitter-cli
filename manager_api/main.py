"""FastAPI application factory and runtime health endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from sqlalchemy import create_engine, text

from .api.routers.accounts import router as accounts_router
from .api.routers.bindings import router as bindings_router
from .api.routers.balances import router as balances_router
from .api.routers.imports import router as imports_router
from .api.routers.vault import router as vault_router
from .api.routers.wallets import router as wallets_router
from .api.routers.tasks import router as tasks_router
from .config import ManagerSettings, get_settings
from .services.vault import VaultRuntime

Probe = Callable[[], None]


def _postgres_probe(settings: ManagerSettings) -> None:
    """Probe PostgreSQL without opening a long-lived application session."""
    engine = create_engine(
        settings.sqlalchemy_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": int(settings.healthcheck_timeout_seconds)},
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    finally:
        engine.dispose()


def _redis_probe(settings: ManagerSettings) -> None:
    """Probe Redis and close the short-lived client immediately."""
    import redis

    client = redis.Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.healthcheck_timeout_seconds,
        socket_timeout=settings.healthcheck_timeout_seconds,
        decode_responses=True,
    )
    try:
        client.ping()
    finally:
        client.close()


def create_app(
    settings: ManagerSettings | None = None,
    *,
    postgres_probe: Probe | None = None,
    redis_probe: Probe | None = None,
) -> FastAPI:
    """Create the manager API with injectable probes for deterministic tests."""
    runtime = settings or get_settings()
    check_postgres = postgres_probe or (lambda: _postgres_probe(runtime))
    check_redis = redis_probe or (lambda: _redis_probe(runtime))

    app = FastAPI(title="Account Wallet Task Manager", version="0.9.0")
    app.state.vault_runtime = VaultRuntime(
        cache_ttl_seconds=runtime.vault_cache_ttl_seconds,
    )
    app.include_router(imports_router)
    app.include_router(accounts_router)
    app.include_router(bindings_router)
    app.include_router(balances_router)
    app.include_router(vault_router)
    app.include_router(wallets_router)
    app.include_router(tasks_router)

    @app.get("/health/live")
    def live() -> dict[str, str]:
        """Report process liveness without contacting external services."""
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> dict[str, Any]:
        """Report dependency readiness with a redacted failure summary."""
        checks: dict[str, str] = {}
        for name, probe in (("postgres", check_postgres), ("redis", check_redis)):
            try:
                probe()
            except Exception:
                checks[name] = "down"
            else:
                checks[name] = "ok"

        status = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
        return {"status": status, "checks": checks}

    return app
