from manager_api.config import ManagerSettings
from manager_api.main import create_app


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
