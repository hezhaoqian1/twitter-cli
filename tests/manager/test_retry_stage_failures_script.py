from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.services.stage_retries import (
    retry_stage_failures,
    select_failed_stage_jobs,
)
from scripts.manager_retry_stage_failures import _format_summary


def _session() -> Session:
    """创建隔离内存库测试阶段失败重试脚本。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _account(session: Session) -> SocialAccount:
    """创建不包含凭据材料的账号夹具。"""
    account = SocialAccount(
        handle="retry-fixture",
        normalized_handle="retry-fixture",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.HEALTHY,
    )
    session.add(account)
    session.flush()
    return account


def _failed_job(session: Session, account: SocialAccount, *, kind: TaskKind) -> TaskJob:
    """创建一个失败任务夹具，目标原文只留在数据库内。"""
    job = TaskJob(
        kind=kind,
        state=TaskState.FAILED,
        attempt=1,
        priority=0,
        social_account_id=account.id,
        external_target="hidden-provider-target",
        idempotency_key=f"{kind.value}:retry-fixture",
        lease_keys=[],
        scheduled_at=utc_now(),
        finished_at=utc_now(),
        failure_code="fixture_failure",
    )
    session.add(job)
    session.flush()
    return job


def test_select_failed_stage_jobs_filters_by_kind() -> None:
    """批量失败重试只选择指定阶段的失败任务。"""
    session = _session()
    try:
        account = _account(session)
        selected = _failed_job(session, account, kind=TaskKind.VERIFY_ACCOUNT)
        _failed_job(session, account, kind=TaskKind.BIND)

        jobs = select_failed_stage_jobs(session, kind=TaskKind.VERIFY_ACCOUNT, limit=10)

        assert [job.id for job in jobs] == [selected.id]
    finally:
        session.close()


def test_retry_stage_failures_dry_run_does_not_change_state() -> None:
    """默认预览只返回数量，不修改失败任务。"""
    session = _session()
    try:
        account = _account(session)
        job = _failed_job(session, account, kind=TaskKind.VERIFY_ACCOUNT)

        summary = retry_stage_failures(session, stage="verify", limit=10, apply=False)

        session.refresh(job)
        assert summary.selected == 1
        assert summary.retried == 0
        assert job.state is TaskState.FAILED
    finally:
        session.close()


def test_retry_stage_failures_apply_requeues_selected_jobs() -> None:
    """显式 apply 时通过任务服务重试，保留事件历史。"""
    session = _session()
    try:
        account = _account(session)
        job = _failed_job(session, account, kind=TaskKind.VERIFY_ACCOUNT)

        summary = retry_stage_failures(session, stage="verify", limit=10, apply=True)

        session.refresh(job)
        assert summary.selected == 1
        assert summary.retried == 1
        assert job.state is TaskState.QUEUED
        assert job.attempt == 2
        assert job.failure_code is None
        assert job.events[-1].summary == "task retry queued"
    finally:
        session.close()


def test_format_summary_is_redacted_and_stable() -> None:
    """摘要只包含聚合数字，方便服务器日志检索。"""
    summary = retry_stage_failures(_session(), stage="bind", limit=10, apply=False)

    assert _format_summary(summary) == "\n".join(
        [
            "stage=bind",
            "selected=0",
            "retried=0",
            "apply=false",
        ]
    )
