from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.config import ManagerSettings
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.models.tasks import ResourceLease, TaskJob, TaskState
from manager_api.services.runtime import collect_runtime_metrics
from manager_api.services.vault import VaultRuntime, VaultService
from datetime import datetime, timedelta, timezone
from uuid import uuid4


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


class _RuntimeRedis:
    """Deterministic Redis metrics double."""

    def __init__(self) -> None:
        self.lengths = {"ready": 4, "processing": 1}
        self.heartbeats = {"worker-a": "2026-08-29T10:00:00+00:00"}

    def llen(self, name: str) -> int:
        return self.lengths.get(name, 0)

    def hgetall(self, name: str) -> dict[str, str]:
        return self.heartbeats if name == "heartbeats" else {}


def test_runtime_metrics_aggregate_queue_tasks_leases_and_heartbeats() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 29, 10, 0, 20, tzinfo=timezone.utc)
    with Session(engine) as session:
        finished = TaskJob(
            kind="bind",
            state=TaskState.SUCCEEDED,
            external_target="fixture",
            idempotency_key=f"finished-{uuid4()}",
            scheduled_at=now - timedelta(minutes=2),
            finished_at=now - timedelta(minutes=1),
        )
        active = TaskJob(
            kind="repost",
            state=TaskState.RUNNING,
            external_target="fixture",
            idempotency_key=f"active-{uuid4()}",
            scheduled_at=now - timedelta(minutes=1),
            lease_keys=["account:fixture"],
        )
        session.add_all([finished, active])
        session.flush()
        session.add(
            ResourceLease(
                lease_key="account:fixture",
                task_job_id=active.id,
                owner_token="worker-fixture",
                acquired_at=now - timedelta(seconds=20),
                expires_at=now + timedelta(seconds=10),
            )
        )
        session.flush()

        metrics = collect_runtime_metrics(
            session,
            _RuntimeRedis(),
            now=now,
            ready_key="ready",
            processing_key="processing",
            heartbeat_key="heartbeats",
        )

    assert metrics.queues.ready == 4
    assert metrics.queues.processing == 1
    assert metrics.tasks.total == 2
    assert metrics.tasks.active == 1
    assert metrics.tasks.counts[TaskState.SUCCEEDED.value] == 1
    assert metrics.leases.active == 1
    assert metrics.leases.expiring_soon == 1
    assert metrics.workers.active == 1


def test_runtime_metrics_route_is_registered() -> None:
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/runtime/metrics" in app.openapi()["paths"]
