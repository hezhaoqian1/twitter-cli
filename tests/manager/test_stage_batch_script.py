from __future__ import annotations

from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import (
    AccountHealth,
    AccountSecret,
    LifecycleState,
    SocialAccount,
)
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet, WalletSecret
from manager_api.services.tasks import TaskService
from manager_api.services.workflows import WorkflowStage
from scripts.manager_create_stage_batch import (
    create_stage_batch_from_selection,
    select_stage_items,
)


def _session() -> Session:
    """创建内存数据库会话，隔离脚本选择器测试。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _account(
    session: Session,
    handle: str,
    *,
    health: AccountHealth = AccountHealth.HEALTHY,
    with_secret: bool = True,
) -> SocialAccount:
    """创建一个可被阶段脚本选择的账号夹具。"""
    account = SocialAccount(
        handle=handle,
        normalized_handle=handle.casefold(),
        state=LifecycleState.ACTIVE,
        health=health,
    )
    session.add(account)
    session.flush()
    if with_secret:
        session.add(
            AccountSecret(
                social_account_id=account.id,
                version=1,
                is_current=True,
                envelope=b"fixture",
                envelope_version=1,
                secret_fingerprint=f"account-{handle}",
            )
        )
        session.flush()
    return account


def _wallet(session: Session, suffix: str, *, with_secret: bool = True) -> Wallet:
    """创建一个可被绑定脚本选择的钱包夹具。"""
    wallet = Wallet(
        address=f"0x{suffix * 40}",
        normalized_address=f"0x{suffix * 40}",
        state="active",
    )
    session.add(wallet)
    session.flush()
    if with_secret:
        session.add(
            WalletSecret(
                wallet_id=wallet.id,
                version=1,
                is_current=True,
                envelope=b"fixture",
                envelope_version=1,
                secret_fingerprint=f"wallet-{suffix}",
            )
        )
        session.flush()
    return wallet


def _binding(
    session: Session,
    account: SocialAccount,
    wallet: Wallet,
    *,
    state: BindingState = BindingState.BOUND,
) -> AccountWalletBinding:
    """创建账号地址绑定夹具。"""
    binding = AccountWalletBinding(
        social_account_id=account.id,
        wallet_id=wallet.id,
        binding_key=f"binding-{uuid4()}",
        state=state,
        bound_at=utc_now() if state is BindingState.BOUND else None,
    )
    session.add(binding)
    session.flush()
    return binding


def _task(
    session: Session,
    binding: AccountWalletBinding,
    *,
    kind: TaskKind,
    state: TaskState,
    target: str,
) -> TaskJob:
    """创建一个绑定范围的公开任务状态夹具。"""
    digest = TaskService._target_digest(target)
    job = TaskJob(
        kind=kind,
        state=state,
        attempt=1,
        priority=0,
        binding_id=binding.id,
        social_account_id=binding.social_account_id,
        wallet_id=binding.wallet_id,
        external_target=target,
        idempotency_key=f"{kind.value}:binding:{binding.id}:{digest}",
        lease_keys=[],
        scheduled_at=utc_now(),
    )
    session.add(job)
    session.flush()
    return job


def test_verify_selection_uses_active_accounts_with_current_secret() -> None:
    """校验阶段只选择可解密账号材料的活跃账号。"""
    session = _session()
    try:
        ready = _account(session, "ready")
        _account(session, "missing-secret", with_secret=False)
        archived = _account(session, "archived")
        archived.state = LifecycleState.ARCHIVED
        archived.archived_at = utc_now()
        session.flush()

        items = select_stage_items(
            session,
            stage=WorkflowStage.VERIFY,
            limit=10,
            external_target="x:verify",
        )

        assert [item.social_account_id for item in items] == [ready.id]
    finally:
        session.close()


def test_bind_selection_pairs_healthy_unoccupied_accounts_and_wallets() -> None:
    """绑定阶段默认只拿健康账号，并跳过已占用资源。"""
    session = _session()
    try:
        healthy = _account(session, "healthy")
        unknown = _account(session, "unknown", health=AccountHealth.UNKNOWN)
        occupied_account = _account(session, "occupied")
        wallet_a = _wallet(session, "1")
        wallet_b = _wallet(session, "2")
        occupied_wallet = _wallet(session, "3")
        _binding(session, occupied_account, occupied_wallet, state=BindingState.PENDING)

        strict_items = select_stage_items(
            session,
            stage=WorkflowStage.BIND,
            limit=10,
            external_target="kredo:bind",
        )
        relaxed_items = select_stage_items(
            session,
            stage=WorkflowStage.BIND,
            limit=10,
            external_target="kredo:bind",
            include_unverified=True,
        )

        assert [(item.social_account_id, item.wallet_id) for item in strict_items] == [
            (healthy.id, wallet_a.id)
        ]
        assert [(item.social_account_id, item.wallet_id) for item in relaxed_items] == [
            (healthy.id, wallet_a.id),
            (unknown.id, wallet_b.id),
        ]
    finally:
        session.close()


def test_repost_selection_skips_same_target_existing_jobs() -> None:
    """转发阶段不会重复创建同目标任务，失败项在任务页重试。"""
    session = _session()
    try:
        first = _binding(session, _account(session, "first"), _wallet(session, "4"))
        second = _binding(session, _account(session, "second"), _wallet(session, "5"))
        third = _binding(session, _account(session, "third"), _wallet(session, "6"))
        failed = _binding(session, _account(session, "failed-repost"), _wallet(session, "a"))
        target = "https://x.test/status/123"
        _task(session, first, kind=TaskKind.REPOST, state=TaskState.WAITING_EXTERNAL_VALIDATION, target=target)
        _task(session, second, kind=TaskKind.REPOST, state=TaskState.SUCCEEDED, target=target)
        _task(session, failed, kind=TaskKind.REPOST, state=TaskState.FAILED, target=target)
        _task(session, third, kind=TaskKind.REPOST, state=TaskState.SUCCEEDED, target="https://x.test/status/old")

        items = select_stage_items(
            session,
            stage=WorkflowStage.REPOST,
            limit=10,
            external_target=target,
        )

        assert [item.binding_id for item in items] == [third.id]
    finally:
        session.close()


def test_claim_selection_requires_successful_repost_and_no_claim_job() -> None:
    """领取阶段只选择转发已成功且没有领取任务的绑定。"""
    session = _session()
    try:
        ready = _binding(session, _account(session, "ready-claim"), _wallet(session, "7"))
        waiting = _binding(session, _account(session, "waiting-claim"), _wallet(session, "8"))
        claiming = _binding(session, _account(session, "claiming"), _wallet(session, "9"))
        failed_claim = _binding(session, _account(session, "failed-claim"), _wallet(session, "c"))
        _task(session, ready, kind=TaskKind.REPOST, state=TaskState.SUCCEEDED, target="tweet-a")
        _task(session, waiting, kind=TaskKind.REPOST, state=TaskState.WAITING_EXTERNAL_VALIDATION, target="tweet-a")
        _task(session, claiming, kind=TaskKind.REPOST, state=TaskState.SUCCEEDED, target="tweet-a")
        _task(session, claiming, kind=TaskKind.CLAIM, state=TaskState.QUEUED, target="kredo:claim")
        _task(session, failed_claim, kind=TaskKind.REPOST, state=TaskState.SUCCEEDED, target="tweet-a")
        _task(session, failed_claim, kind=TaskKind.CLAIM, state=TaskState.FAILED, target="kredo:claim")

        items = select_stage_items(
            session,
            stage=WorkflowStage.CLAIM,
            limit=10,
            external_target="kredo:claim",
        )

        assert [item.binding_id for item in items] == [ready.id]
    finally:
        session.close()


def test_create_stage_batch_from_selection_creates_only_selected_jobs() -> None:
    """脚本入口根据选择结果创建单阶段批次。"""
    session = _session()
    try:
        first = _account(session, "batch-first")
        second = _account(session, "batch-second")

        result = create_stage_batch_from_selection(
            session,
            name="Verify batch",
            stage=WorkflowStage.VERIFY,
            limit=1,
            dispatch_limit=3,
            external_target="x:verify",
        )

        jobs = session.query(TaskJob).all()
        assert result.name == "Verify batch"
        assert result.dispatch_limit == 3
        assert [item.social_account_id for item in result.items] == [first.id]
        assert len(jobs) == 1
        assert jobs[0].social_account_id == first.id
        assert jobs[0].social_account_id != second.id
    finally:
        session.close()
