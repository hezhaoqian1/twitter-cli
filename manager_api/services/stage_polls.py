"""Bulk requeue helpers for delayed stage status polling."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.tasks import TaskJob, TaskKind, TaskState
from .tasks import TaskService


STAGE_TO_KIND = {
    "bind": TaskKind.BIND,
    "repost": TaskKind.REPOST,
    "claim": TaskKind.CLAIM,
}


@dataclass(frozen=True)
class StagePollRequeueSummary:
    """Redacted summary for UI, CLI logs, and tests."""

    stage: str
    selected: int
    requeued: int
    skipped_missing_ref: int
    apply: bool


def select_waiting_poll_jobs(
    session: Session,
    *,
    kind: TaskKind,
    limit: int,
    require_external_ref: bool = True,
) -> list[TaskJob]:
    """选择等待外部校验的任务，不读取账号、Cookie 或私钥。"""
    if limit < 1:
        raise ValueError("limit must be positive")
    query = (
        select(TaskJob)
        .where(TaskJob.kind == kind, TaskJob.state == TaskState.WAITING_EXTERNAL_VALIDATION)
        .order_by(TaskJob.next_poll_at, TaskJob.created_at, TaskJob.id)
        .limit(limit)
    )
    if require_external_ref:
        query = query.where(TaskJob.external_operation_ref.is_not(None))
    return list(session.scalars(query).all())


def requeue_stage_polls(
    session: Session,
    *,
    stage: str,
    limit: int,
    apply: bool,
) -> StagePollRequeueSummary:
    """按阶段预览或重新入队 waiting 任务，保持状态机事件历史。"""
    try:
        kind = STAGE_TO_KIND[stage]
    except KeyError as error:
        raise ValueError("stage must be one of: bind, repost, claim") from error

    waiting_total = int(
        session.scalar(
            select(func.count())
            .select_from(TaskJob)
            .where(TaskJob.kind == kind, TaskJob.state == TaskState.WAITING_EXTERNAL_VALIDATION)
        )
        or 0
    )
    jobs = select_waiting_poll_jobs(session, kind=kind, limit=limit)
    skipped_missing_ref = max(waiting_total - len(jobs), 0)
    requeued = 0
    if apply:
        task_service = TaskService(session)
        for job in jobs:
            task_service.poll(job.id)
            requeued += 1
    return StagePollRequeueSummary(
        stage=stage,
        selected=len(jobs),
        requeued=requeued,
        skipped_missing_ref=skipped_missing_ref,
        apply=apply,
    )
