from __future__ import annotations

from typing import Generator
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.api.routers.tasks import (
    create_paired_bind_stage,
    create_task,
    list_tasks,
    queue_bind_status_sync_tasks,
    transition_task,
)
from manager_api.api.routers.tasks import requeue_stage_poll_tasks, retry_stage_failed_tasks
from manager_api.config import ManagerSettings
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.models.accounts import AccountHealth, AccountSecret, LifecycleState, SocialAccount
from manager_api.models.bindings import BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet, WalletSecret
from manager_api.schemas.tasks import (
    BindStatusSyncRequest,
    PairedBindStageRequest,
    StagePollRequeueRequest,
    StageRetryRequest,
    TaskCreateRequest,
    TaskTransitionRequest,
    WorkflowStage,
)
from manager_api.services.bind_status_sync import KREDO_BIND_STATUS_TARGET
from manager_api.services.bindings import BindingService
from manager_api.db.base import utc_now
from manager_api.services.imports import AccountImportService
from manager_api.services.vault import VaultService
from manager_api.services.wallets import WalletService


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _resources(session: Session) -> tuple[SocialAccount, Wallet]:
    account = SocialAccount(
        handle="api-task-account",
        normalized_handle="api-task-account",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet = Wallet(
        address="0x" + "a" * 40,
        normalized_address="0x" + "a" * 40,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
    return account, wallet


def test_task_api_is_registered_idempotent_and_redacted(session: Session) -> None:
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/tasks" in app.openapi()["paths"]
    account, wallet = _resources(session)
    request = TaskCreateRequest(
        kind=TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="https://provider.example/private-target",
    )

    first = create_task(request, session=session)
    second = create_task(request, session=session)
    listed = list_tasks(offset=0, limit=50, session=session)

    assert first.id == second.id
    assert listed.total == 1
    assert "provider.example" not in first.model_dump_json()
    assert "private-target" not in first.model_dump_json()
    assert first.target_configured is True
    assert first.state == TaskState.QUEUED.value
    assert isinstance(first.id, UUID)
    assert listed.items[0].events[0].to_state == TaskState.QUEUED.value


def test_task_api_exposes_stage_batches_without_ordered_workflow_endpoint() -> None:
    """公开 API 只暴露单阶段批次，不暴露自动串联 workflow 入口。"""
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    paths = app.openapi()["paths"]

    assert "/api/tasks/stages" in paths
    assert "/api/tasks/paired-bind" in paths
    assert "/api/tasks/bind-status-sync" in paths
    assert "/api/tasks/stage-polls" in paths
    assert "/api/tasks/stage-retries" in paths
    assert "/api/tasks/workflows" not in paths


def test_paired_bind_api_previews_and_creates_only_bind_jobs(
    session: Session,
) -> None:
    """页面配对绑定入口复用导入数据并且只创建绑定阶段任务。"""
    vault = VaultService(session)
    vault.initialize("manager-password-fixture")
    accounts_content = "\n".join(
        [
            "\t".join(
                (
                    "paired-api-alpha",
                    "password-alpha",
                    "JBSWY3DPEHPK3PXP",
                    "alpha@example.test",
                    "mail-alpha",
                    "token-alpha",
                    "auth_token=token-alpha; ct0=csrf-alpha",
                )
            ),
            "\t".join(
                (
                    "paired-api-bravo",
                    "password-bravo",
                    "JBSWY3DPEHPK3PXP",
                    "bravo@example.test",
                    "mail-bravo",
                    "token-bravo",
                    "auth_token=token-bravo; ct0=csrf-bravo",
                )
            ),
        ]
    )
    private_keys_content = "\n".join(
        [
            "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
            "59c6995e998f97a5a0044966f0945382e9dae8adf733bcf4a936f586124f0e86",
        ]
    )
    AccountImportService(session, vault).commit(accounts_content)
    WalletService(session, vault).commit_private_keys(private_keys_content)
    for account in session.query(SocialAccount).all():
        account.health = AccountHealth.HEALTHY
    session.flush()

    preview = create_paired_bind_stage(
        PairedBindStageRequest(
            accounts_content=accounts_content,
            private_keys_content=private_keys_content,
            name="paired bind preview",
            limit=10,
            dispatch_limit=2,
        ),
        session=session,
    )

    assert preview.apply is False
    assert preview.selected_pairs == 2
    assert preview.created_jobs == 0
    assert session.query(TaskJob).count() == 0

    applied = create_paired_bind_stage(
        PairedBindStageRequest(
            accounts_content=accounts_content,
            private_keys_content=private_keys_content,
            name="paired bind apply",
            limit=10,
            dispatch_limit=2,
            apply=True,
        ),
        session=session,
    )

    rendered = applied.model_dump_json()
    assert applied.apply is True
    assert applied.created_jobs == 2
    assert session.query(AccountSecret).count() == 2
    assert session.query(WalletSecret).count() == 2
    assert [job.kind for job in session.query(TaskJob).order_by(TaskJob.created_at).all()] == [
        TaskKind.BIND,
        TaskKind.BIND,
    ]
    assert "password-alpha" not in rendered
    assert "token-alpha" not in rendered
    assert "ac0974" not in rendered


def test_task_api_maps_conflicts_and_rejects_invalid_resource_references(
    session: Session,
) -> None:
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/tasks/{task_id}/transition" in app.openapi()["paths"]

    request = TaskCreateRequest(
        kind=TaskKind.VERIFY_ACCOUNT,
        social_account_id=UUID("00000000-0000-0000-0000-000000000001"),
        external_target="account-health",
    )
    with pytest.raises(HTTPException) as error:
        create_task(request, session=session)
    assert error.value.status_code == 409
    assert error.value.detail == {
        "code": "account_not_found",
        "message": "account not found",
    }

    account, wallet = _resources(session)
    created = create_task(
        TaskCreateRequest(
            kind=TaskKind.BIND,
            social_account_id=account.id,
            wallet_id=wallet.id,
            external_target="bind-fixture",
        ),
        session=session,
    )
    with pytest.raises(HTTPException) as transition_error:
        transition_task(
            created.id,
            TaskTransitionRequest(to_state=TaskState.RUNNING),
            session=session,
        )
    assert transition_error.value.status_code == 409
    detail = transition_error.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_transition"


def test_claim_task_api_requires_completed_repost_and_stays_idempotent(
    session: Session,
) -> None:
    """通用任务入口也遵守领取前置条件，并保留重复请求幂等性。"""
    account, wallet = _resources(session)
    binding = BindingService(session).confirm(
        BindingService(session).create_pending(account.id, wallet.id).binding.id,
        "fixture:bound",
    ).binding
    request = TaskCreateRequest(
        kind=TaskKind.CLAIM,
        binding_id=binding.id,
        external_target="kredo:claim",
    )

    with pytest.raises(HTTPException) as early_error:
        create_task(request, session=session)
    assert early_error.value.status_code == 409
    assert early_error.value.detail == {
        "code": "claim_not_ready",
        "message": "repost validation has not succeeded",
    }

    session.add(
        TaskJob(
            kind=TaskKind.REPOST,
            state=TaskState.SUCCEEDED,
            attempt=1,
            priority=0,
            binding_id=binding.id,
            social_account_id=account.id,
            wallet_id=wallet.id,
            external_target="fixture:tweet",
            idempotency_key="repost:fixture:tweet",
            lease_keys=[],
            scheduled_at=utc_now(),
        )
    )
    session.flush()

    created = create_task(request, session=session)
    duplicate = create_task(request, session=session)

    assert created.id == duplicate.id
    assert created.state == TaskState.QUEUED.value


def test_stage_poll_api_previews_and_requeues_waiting_jobs(session: Session) -> None:
    """阶段轮询 API 只返回聚合数量，并通过任务状态机重新入队。"""
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/tasks/stage-polls" in app.openapi()["paths"]
    account, wallet = _resources(session)
    binding = BindingService(session).confirm(
        BindingService(session).create_pending(account.id, wallet.id).binding.id,
        "fixture:bound",
    ).binding
    assert binding.state is BindingState.BOUND
    job = TaskJob(
        kind=TaskKind.BIND,
        state=TaskState.WAITING_EXTERNAL_VALIDATION,
        attempt=1,
        priority=0,
        binding_id=binding.id,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="hidden-provider-target",
        external_operation_ref="external-ref",
        idempotency_key="bind:stage-poll",
        lease_keys=[],
        scheduled_at=utc_now(),
        next_poll_at=utc_now(),
    )
    session.add(job)
    session.flush()

    preview = requeue_stage_poll_tasks(
        StagePollRequeueRequest(stage=WorkflowStage.BIND, limit=10),
        session=session,
    )
    session.refresh(job)
    assert preview.selected == 1
    assert preview.requeued == 0
    assert preview.apply is False
    assert job.state is TaskState.WAITING_EXTERNAL_VALIDATION

    applied = requeue_stage_poll_tasks(
        StagePollRequeueRequest(stage=WorkflowStage.BIND, limit=10, apply=True),
        session=session,
    )
    session.refresh(job)
    dumped = applied.model_dump_json()
    assert applied.selected == 1
    assert applied.requeued == 1
    assert job.state is TaskState.QUEUED
    assert "hidden-provider-target" not in dumped
    assert "external-ref" not in dumped


def test_bind_status_sync_api_queues_status_only_jobs(session: Session) -> None:
    """绑定状态同步只创建只读任务，并暂停未执行的首绑动作任务。"""
    account, wallet = _resources(session)
    binding = BindingService(session).create_pending(account.id, wallet.id).binding
    session.add_all(
        [
            AccountSecret(
                social_account_id=account.id,
                version=1,
                is_current=True,
                envelope=b"fixture",
                envelope_version=1,
                secret_fingerprint="account-fixture",
                redacted_metadata="{}",
            ),
            WalletSecret(
                wallet_id=wallet.id,
                version=1,
                is_current=True,
                envelope=b"fixture",
                envelope_version=1,
                secret_fingerprint="wallet-fixture",
                redacted_metadata="{}",
            ),
            TaskJob(
                kind=TaskKind.BIND,
                state=TaskState.QUEUED,
                attempt=0,
                priority=0,
                social_account_id=account.id,
                wallet_id=wallet.id,
                binding_id=binding.id,
                external_target="kredo:bind",
                idempotency_key="bind:first-click-fixture",
                lease_keys=[],
                scheduled_at=utc_now(),
            ),
        ]
    )
    session.flush()

    preview = queue_bind_status_sync_tasks(BindStatusSyncRequest(limit=10), session=session)

    assert preview.apply is False
    assert preview.pending_bindings == 1
    assert preview.selected == 1
    assert preview.created_jobs == 0
    assert session.query(TaskJob).count() == 1

    applied = queue_bind_status_sync_tasks(
        BindStatusSyncRequest(name="sync pending bind", limit=10, apply=True),
        session=session,
    )

    jobs = session.query(TaskJob).order_by(TaskJob.created_at, TaskJob.external_target).all()
    action_job = next(job for job in jobs if job.external_target == "kredo:bind")
    status_job = next(job for job in jobs if job.external_target == KREDO_BIND_STATUS_TARGET)
    rendered = applied.model_dump_json()
    assert applied.created_jobs == 1
    assert applied.paused_action_jobs == 1
    assert action_job.state is TaskState.PAUSED
    assert status_job.state is TaskState.QUEUED
    assert status_job.external_operation_ref == f"kredo:bind-status:{binding.id}"
    assert status_job.binding_id == binding.id
    assert session.query(TaskJob).filter(TaskJob.kind == TaskKind.REPOST).count() == 0
    assert session.query(TaskJob).filter(TaskJob.kind == TaskKind.CLAIM).count() == 0
    assert account.handle not in rendered
    assert wallet.address not in rendered


def test_stage_retry_api_previews_and_requeues_failed_jobs(session: Session) -> None:
    """阶段重试 API 只返回聚合数量，并复用任务 retry 状态机。"""
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/tasks/stage-retries" in app.openapi()["paths"]
    account, _wallet = _resources(session)
    job = TaskJob(
        kind=TaskKind.VERIFY_ACCOUNT,
        state=TaskState.FAILED,
        attempt=1,
        priority=0,
        social_account_id=account.id,
        external_target="hidden-provider-target",
        idempotency_key="verify:stage-retry",
        lease_keys=[],
        scheduled_at=utc_now(),
        finished_at=utc_now(),
        failure_code="fixture_failure",
    )
    session.add(job)
    session.flush()

    preview = retry_stage_failed_tasks(
        StageRetryRequest(stage=WorkflowStage.VERIFY, limit=10),
        session=session,
    )
    session.refresh(job)
    assert preview.selected == 1
    assert preview.retried == 0
    assert preview.apply is False
    assert job.state is TaskState.FAILED

    applied = retry_stage_failed_tasks(
        StageRetryRequest(stage=WorkflowStage.VERIFY, limit=10, apply=True),
        session=session,
    )
    session.refresh(job)
    dumped = applied.model_dump_json()
    assert applied.selected == 1
    assert applied.retried == 1
    assert job.state is TaskState.QUEUED
    assert job.attempt == 2
    assert "hidden-provider-target" not in dumped
