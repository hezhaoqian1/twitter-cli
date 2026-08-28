from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.config import ManagerSettings
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.services.vault import VaultRuntime, VaultService


def _settings() -> ManagerSettings:
    return ManagerSettings(
        database_url="postgresql://manager:manager@localhost/manager",
        redis_url="redis://localhost/0",
        session_secret="test-session-secret-123",
    )


def _call_health_route(app, path: str) -> dict:
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint()
    raise AssertionError(f"health route not registered: {path}")


def test_settings_normalize_postgres_driver() -> None:
    settings = _settings()

    assert settings.sqlalchemy_url == "postgresql+psycopg://manager:manager@localhost/manager"
    assert settings.worker_concurrency == 3
    assert settings.browser_concurrency == 2
    assert settings.vault_cache_ttl_seconds == 900.0


def test_live_health_does_not_probe_dependencies() -> None:
    calls: list[str] = []
    app = create_app(
        _settings(),
        postgres_probe=lambda: calls.append("postgres"),
        redis_probe=lambda: calls.append("redis"),
    )

    response = _call_health_route(app, "/health/live")

    assert response == {"status": "ok"}
    assert calls == []


def test_ready_health_reports_dependency_states_without_secrets() -> None:
    app = create_app(
        _settings(),
        postgres_probe=lambda: None,
        redis_probe=lambda: (_ for _ in ()).throw(RuntimeError("password leaked")),
    )

    response = _call_health_route(app, "/health/ready")

    assert response == {
        "status": "degraded",
        "checks": {"postgres": "ok", "redis": "down"},
    }


def test_vault_services_share_the_app_runtime_key() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    runtime = VaultRuntime(cache_ttl_seconds=60)

    with Session(engine) as session:
        first = VaultService(session, runtime=runtime)
        first.initialize("manager-password-fixture")
        session.commit()

    with Session(engine) as session:
        second = VaultService(session, runtime=runtime)
        envelope = second.encrypt_field(
            "account_secrets",
            "account-fixture",
            "token",
            "secret-fixture",
        )
        assert second.decrypt_field(
            "account_secrets",
            "account-fixture",
            "token",
            envelope,
        ) == b"secret-fixture"
