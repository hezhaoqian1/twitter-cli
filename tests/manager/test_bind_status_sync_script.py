from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, AccountSecret, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet, WalletSecret
from manager_api.services.bind_status_sync import KREDO_BIND_STATUS_TARGET, queue_bind_status_sync
from scripts.manager_sync_bind_status import _format_summary


def _session() -> Session:
    """创建隔离内存库测试只读绑定状态同步。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _pending_binding(session: Session) -> AccountWalletBinding:
    """创建一个 pending 绑定及其当前密钥标记。"""
    account = SocialAccount(
        handle="status-sync-fixture",
        normalized_handle="status-sync-fixture",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.HEALTHY,
    )
    wallet = Wallet(
        address="0x" + "e" * 40,
        normalized_address="0x" + "e" * 40,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
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
        ]
    )
    binding = AccountWalletBinding(
        social_account_id=account.id,
        wallet_id=wallet.id,
        binding_key="status-sync-fixture",
        state=BindingState.PENDING,
    )
    session.add(binding)
    session.flush()
    return binding


def test_bind_status_sync_preview_is_read_only_and_redacted() -> None:
    """预览只返回聚合数量，不创建任务或泄露资源身份。"""
    session = _session()
    try:
        binding = _pending_binding(session)

        summary = queue_bind_status_sync(
            session,
            name="preview",
            limit=10,
            dispatch_limit=10,
            apply=False,
        )

        rendered = _format_summary(summary)
        assert summary.selected == 1
        assert session.query(TaskJob).count() == 0
        assert "status-sync-fixture" not in rendered
        assert str(binding.wallet_id) not in rendered
    finally:
        session.close()


def test_bind_status_sync_apply_pauses_first_bind_and_queues_status_job() -> None:
    """应用时暂停首绑任务，再创建带外部引用的只读状态任务。"""
    session = _session()
    try:
        binding = _pending_binding(session)
        session.add(
            TaskJob(
                kind=TaskKind.BIND,
                state=TaskState.QUEUED,
                attempt=0,
                priority=0,
                binding_id=binding.id,
                social_account_id=binding.social_account_id,
                wallet_id=binding.wallet_id,
                external_target="kredo:bind",
                idempotency_key="bind:first-action",
                lease_keys=[],
                scheduled_at=utc_now(),
            )
        )
        session.flush()

        summary = queue_bind_status_sync(
            session,
            name="apply",
            limit=10,
            dispatch_limit=10,
            apply=True,
        )

        first_action = session.query(TaskJob).filter(TaskJob.external_target == "kredo:bind").one()
        status_job = session.query(TaskJob).filter(TaskJob.external_target == KREDO_BIND_STATUS_TARGET).one()
        assert summary.created_jobs == 1
        assert summary.paused_action_jobs == 1
        assert first_action.state is TaskState.PAUSED
        assert status_job.external_operation_ref == f"kredo:bind-status:{binding.id}"
    finally:
        session.close()
