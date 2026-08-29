from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.services.stage_polls import (
    requeue_stage_polls,
    select_waiting_poll_jobs,
)
from scripts.manager_requeue_stage_polls import _format_summary


def _session() -> Session:
    """创建隔离内存库测试阶段轮询脚本。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _binding(session: Session) -> AccountWalletBinding:
    """创建一个已绑定资源夹具，不包含密钥材料。"""
    account = SocialAccount(
        handle="poll-fixture",
        normalized_handle="poll-fixture",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.HEALTHY,
    )
    wallet = Wallet(
        address="0x" + "d" * 40,
        normalized_address="0x" + "d" * 40,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
    binding = AccountWalletBinding(
        social_account_id=account.id,
        wallet_id=wallet.id,
        binding_key="poll-fixture",
        state=BindingState.BOUND,
        bound_at=utc_now(),
    )
    session.add(binding)
    session.flush()
    return binding


def _waiting_job(
    session: Session,
    binding: AccountWalletBinding,
    *,
    kind: TaskKind = TaskKind.BIND,
    operation_ref: str | None = "kredo:bind:fixture",
) -> TaskJob:
    """创建一个等待外部状态的任务夹具。"""
    job = TaskJob(
        kind=kind,
        state=TaskState.WAITING_EXTERNAL_VALIDATION,
        attempt=1,
        priority=0,
        binding_id=binding.id,
        social_account_id=binding.social_account_id,
        wallet_id=binding.wallet_id,
        external_target="fixture",
        external_operation_ref=operation_ref,
        idempotency_key=f"{kind.value}:{operation_ref or 'missing'}",
        lease_keys=[],
        scheduled_at=utc_now(),
        next_poll_at=utc_now(),
    )
    session.add(job)
    session.flush()
    return job


def test_select_waiting_poll_jobs_requires_external_ref() -> None:
    """等待任务缺少外部引用时不进入批量轮询。"""
    session = _session()
    try:
        binding = _binding(session)
        selected = _waiting_job(session, binding)
        _waiting_job(session, binding, operation_ref=None)

        jobs = select_waiting_poll_jobs(session, kind=TaskKind.BIND, limit=10)

        assert [job.id for job in jobs] == [selected.id]
    finally:
        session.close()


def test_requeue_stage_polls_dry_run_does_not_change_state() -> None:
    """默认预览只返回数量，不修改任务状态。"""
    session = _session()
    try:
        binding = _binding(session)
        job = _waiting_job(session, binding)

        summary = requeue_stage_polls(session, stage="bind", limit=10, apply=False)

        session.refresh(job)
        assert summary.selected == 1
        assert summary.requeued == 0
        assert job.state is TaskState.WAITING_EXTERNAL_VALIDATION
    finally:
        session.close()


def test_requeue_stage_polls_apply_requeues_selected_jobs() -> None:
    """显式 apply 时通过任务服务重新入队，保留事件历史。"""
    session = _session()
    try:
        binding = _binding(session)
        job = _waiting_job(session, binding, kind=TaskKind.REPOST, operation_ref="kredo:repost:fixture")

        summary = requeue_stage_polls(session, stage="repost", limit=10, apply=True)

        session.refresh(job)
        assert summary.selected == 1
        assert summary.requeued == 1
        assert job.state is TaskState.QUEUED
        assert job.events[-1].summary == "external status poll queued"
    finally:
        session.close()


def test_format_summary_is_redacted_and_stable() -> None:
    """摘要只包含聚合数字，方便服务器日志检索。"""
    session = _session()
    try:
        binding = _binding(session)
        _waiting_job(session, binding)
        _waiting_job(session, binding, operation_ref=None)

        summary = requeue_stage_polls(session, stage="bind", limit=10, apply=False)

        assert _format_summary(summary) == "\n".join(
            [
                "stage=bind",
                "selected=1",
                "requeued=0",
                "skipped_missing_ref=1",
                "apply=false",
            ]
        )
    finally:
        session.close()
