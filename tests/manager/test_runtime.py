from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.config import ManagerSettings
from manager_api.api.routers.runtime import acceptance_audit as route_acceptance_audit
from manager_api.api.routers.runtime import next_stage_recommendation as route_next_stage
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.models.tasks import ResourceLease, TaskJob, TaskState
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskKind
from manager_api.models.wallets import Wallet
from manager_api.services.runtime import collect_runtime_metrics
from manager_api.services.runtime import collect_operations_summary
from manager_api.services.vault import VaultRuntime, VaultService
from datetime import datetime, timedelta, timezone
from uuid import uuid4


def _settings() -> ManagerSettings:
    return ManagerSettings(
        database_url="postgresql://localhost/manager",
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

    assert settings.sqlalchemy_url == "postgresql+psycopg://localhost/manager"
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
    assert "/api/runtime/operations-summary" in app.openapi()["paths"]
    assert "/api/runtime/next-stage" in app.openapi()["paths"]
    assert "/api/runtime/acceptance-audit" in app.openapi()["paths"]


def test_next_stage_route_returns_actionable_recommendation() -> None:
    """下一阶段 API 复用服务层决策，并且只返回聚合建议。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        account = SocialAccount(
            handle="route-account",
            normalized_handle="route-account",
            state=LifecycleState.ACTIVE,
            health=AccountHealth.HEALTHY,
        )
        wallet = Wallet(
            address="0x" + "7" * 40,
            normalized_address="0x" + "7" * 40,
            state="active",
        )
        session.add_all([account, wallet])
        session.flush()
        binding = AccountWalletBinding(
            social_account_id=account.id,
            wallet_id=wallet.id,
            binding_key="route-pending",
            state=BindingState.PENDING,
        )
        session.add(binding)
        session.flush()
        session.add(
            TaskJob(
                kind=TaskKind.BIND,
                state=TaskState.WAITING_EXTERNAL_VALIDATION,
                attempt=1,
                priority=0,
                binding_id=binding.id,
                social_account_id=account.id,
                wallet_id=wallet.id,
                external_target="hidden-bind-target",
                external_operation_ref="bind-ref",
                idempotency_key="bind:route-poll",
                lease_keys=[],
                scheduled_at=now,
            )
        )
        session.flush()

        response = route_next_stage(limit=10, session=session)

    assert response.action == "poll"
    assert response.stage == "bind"
    assert "manager_requeue_stage_polls.py bind" in response.command
    assert "hidden-bind-target" not in response.model_dump_json()


def test_acceptance_audit_route_returns_redacted_action_list() -> None:
    """验收接口只暴露聚合动作，不暴露外部目标原文。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        account = SocialAccount(
            handle="audit-account",
            normalized_handle="audit-account",
            state=LifecycleState.ACTIVE,
            health=AccountHealth.HEALTHY,
        )
        wallet = Wallet(
            address="0x" + "8" * 40,
            normalized_address="0x" + "8" * 40,
            state="active",
        )
        session.add_all([account, wallet])
        session.flush()
        binding = AccountWalletBinding(
            social_account_id=account.id,
            wallet_id=wallet.id,
            binding_key="audit-pending",
            state=BindingState.PENDING,
        )
        session.add(binding)
        session.flush()
        session.add(
            TaskJob(
                kind=TaskKind.BIND,
                state=TaskState.WAITING_EXTERNAL_VALIDATION,
                attempt=1,
                priority=0,
                binding_id=binding.id,
                social_account_id=account.id,
                wallet_id=wallet.id,
                external_target="sensitive-bind-target",
                external_operation_ref="bind-ref",
                idempotency_key="bind:audit-poll",
                lease_keys=[],
                scheduled_at=now,
            )
        )
        session.flush()

        response = route_acceptance_audit(limit=10, session=session)

    body = response.model_dump(mode="json")
    assert body["next_action"]["action"] == "poll"
    poll_action = next(
        action for action in body["actions"] if action["action"] == "poll" and action["stage"] == "bind"
    )
    assert "manager_requeue_stage_polls.py bind" in poll_action["command"]
    assert "sensitive-bind-target" not in response.model_dump_json()


def test_operations_summary_counts_independent_stage_readiness() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)
    with Session(engine) as session:
        accounts = [
            SocialAccount(
                handle=f"stage-account-{index}",
                normalized_handle=f"stage-account-{index}",
                state=LifecycleState.ACTIVE,
                health=AccountHealth.HEALTHY if index != 3 else AccountHealth.UNKNOWN,
            )
            for index in range(1, 6)
        ]
        wallets = [
            Wallet(
                address=f"0x{index:02x}" + "b" * 38,
                normalized_address=f"0x{index:02x}" + "b" * 38,
                state="active",
            )
            for index in range(1, 6)
        ]
        session.add_all([*accounts, *wallets])
        session.flush()
        pending = AccountWalletBinding(
            social_account_id=accounts[0].id,
            wallet_id=wallets[0].id,
            binding_key="pending",
            state=BindingState.PENDING,
        )
        bound = AccountWalletBinding(
            social_account_id=accounts[1].id,
            wallet_id=wallets[1].id,
            binding_key="bound",
            state=BindingState.BOUND,
            bound_at=now,
        )
        claimed = AccountWalletBinding(
            social_account_id=accounts[2].id,
            wallet_id=wallets[2].id,
            binding_key="claimed",
            state=BindingState.BOUND,
            bound_at=now,
        )
        fresh_bound = AccountWalletBinding(
            social_account_id=accounts[3].id,
            wallet_id=wallets[3].id,
            binding_key="fresh-bound",
            state=BindingState.BOUND,
            bound_at=now,
        )
        session.add_all([pending, bound, claimed, fresh_bound])
        session.flush()
        session.add_all(
            [
                TaskJob(
                    kind=TaskKind.REPOST,
                    state=TaskState.SUCCEEDED,
                    binding_id=bound.id,
                    social_account_id=bound.social_account_id,
                    wallet_id=bound.wallet_id,
                    external_target="fixture",
                    idempotency_key="repost-ready",
                    scheduled_at=now,
                    finished_at=now,
                ),
                TaskJob(
                    kind=TaskKind.REPOST,
                    state=TaskState.SUCCEEDED,
                    binding_id=claimed.id,
                    social_account_id=claimed.social_account_id,
                    wallet_id=claimed.wallet_id,
                    external_target="fixture",
                    idempotency_key="repost-claimed",
                    scheduled_at=now,
                    finished_at=now,
                ),
                TaskJob(
                    kind=TaskKind.CLAIM,
                    state=TaskState.SUCCEEDED,
                    binding_id=claimed.id,
                    social_account_id=claimed.social_account_id,
                    wallet_id=claimed.wallet_id,
                    external_target="fixture",
                    idempotency_key="claim-done",
                    scheduled_at=now,
                    finished_at=now,
                ),
                TaskJob(
                    kind=TaskKind.REPOST,
                    state=TaskState.WAITING_EXTERNAL_VALIDATION,
                    binding_id=bound.id,
                    social_account_id=bound.social_account_id,
                    wallet_id=bound.wallet_id,
                    external_target="fixture",
                    external_operation_ref="repost-ref",
                    idempotency_key="repost-waiting",
                    scheduled_at=now,
                ),
                TaskJob(
                    kind=TaskKind.BIND,
                    state=TaskState.FAILED,
                    binding_id=pending.id,
                    social_account_id=pending.social_account_id,
                    wallet_id=pending.wallet_id,
                    external_target="fixture",
                    idempotency_key="bind-failed",
                    scheduled_at=now,
                ),
            ]
        )
        session.flush()

        summary = collect_operations_summary(session, now=now)

    assert summary.resources.accounts_active == 5
    assert summary.resources.accounts_healthy == 4
    assert summary.resources.accounts_available_for_binding == 1
    assert summary.resources.wallets_available_for_binding == 1
    stages = {stage.key: stage for stage in summary.stages}
    assert stages["verify"].ready == 5
    assert stages["bind"].ready == 1
    assert stages["bind"].waiting == 1
    assert stages["bind"].failed == 1
    assert stages["bind"].retryable == 1
    assert stages["repost"].ready == 1
    assert stages["repost"].waiting == 1
    assert stages["repost"].pollable == 1
    assert stages["claim"].ready == 1
