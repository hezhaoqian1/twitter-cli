"""Bulk retry helpers for failed stage tasks."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.tasks import TaskJob, TaskKind, TaskState
from .tasks import TaskService


STAGE_TO_KIND = {
    "verify": TaskKind.VERIFY_ACCOUNT,
    "bind": TaskKind.BIND,
    "repost": TaskKind.REPOST,
    "claim": TaskKind.CLAIM,
}


@dataclass(frozen=True)
class StageRetrySummary:
    """Redacted summary for UI, CLI logs, and tests."""

    stage: str
    selected: int
    retried: int
    apply: bool


def select_failed_stage_jobs(session: Session, *, kind: TaskKind, limit: int) -> list[TaskJob]:
    """选择指定阶段的失败任务，不读取账号、Cookie、地址或目标原文。"""
    if limit < 1:
        raise ValueError("limit must be positive")
    return list(
        session.scalars(
            select(TaskJob)
            .where(TaskJob.kind == kind, TaskJob.state == TaskState.FAILED)
            .order_by(TaskJob.finished_at, TaskJob.created_at, TaskJob.id)
            .limit(limit)
        ).all()
    )


def retry_stage_failures(
    session: Session,
    *,
    stage: str,
    limit: int,
    apply: bool,
) -> StageRetrySummary:
    """按阶段预览或重试失败任务，复用单任务状态机和事件历史。"""
    try:
        kind = STAGE_TO_KIND[stage]
    except KeyError as error:
        raise ValueError("stage must be one of: verify, bind, repost, claim") from error

    jobs = select_failed_stage_jobs(session, kind=kind, limit=limit)
    retried = 0
    if apply:
        task_service = TaskService(session)
        for job in jobs:
            task_service.retry(job.id)
            retried += 1
    return StageRetrySummary(
        stage=stage,
        selected=len(jobs),
        retried=retried,
        apply=apply,
    )
