"""Read-only runtime metrics for the manager control plane."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..models.tasks import ResourceLease, TaskJob, TaskState
from ..schemas.runtime import (
    RuntimeLeaseMetrics,
    RuntimeMetricsResponse,
    RuntimeQueueMetrics,
    RuntimeTaskMetrics,
    RuntimeWorkerMetrics,
)


class RuntimeRedisClient(Protocol):
    """Minimum Redis read surface needed for runtime metrics."""

    def llen(self, name: str) -> int: ...

    def hgetall(self, name: str) -> Mapping[str, str]: ...


def _state_value(state: object) -> str:
    """把 SQLAlchemy 枚举统一转换成稳定的公开字符串。"""
    return state.value if isinstance(state, TaskState) else str(state)


def _worker_heartbeat_metrics(
    redis_client: RuntimeRedisClient,
    *,
    now: datetime,
    heartbeat_key: str,
    heartbeat_ttl_seconds: float,
) -> RuntimeWorkerMetrics:
    """读取心跳时间并只返回活跃数量和最近时间。"""
    raw_values = redis_client.hgetall(heartbeat_key)
    cutoff = now - timedelta(seconds=heartbeat_ttl_seconds)
    active = 0
    latest: datetime | None = None
    for raw_value in raw_values.values():
        try:
            heartbeat = datetime.fromisoformat(raw_value)
        except (TypeError, ValueError):
            continue
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=now.tzinfo)
        if heartbeat >= cutoff:
            active += 1
        if latest is None or heartbeat > latest:
            latest = heartbeat
    return RuntimeWorkerMetrics(active=active, heartbeat_at=latest)


def collect_runtime_metrics(
    session: Session,
    redis_client: RuntimeRedisClient,
    *,
    now: datetime | None = None,
    ready_key: str = "manager:tasks:ready",
    processing_key: str = "manager:tasks:processing",
    heartbeat_key: str = "manager:workers:heartbeats",
    heartbeat_ttl_seconds: float = 45.0,
) -> RuntimeMetricsResponse:
    """聚合当前队列、任务、租约和 Worker 心跳，不读取任何密文。"""
    current_time = now or utc_now()
    counts = {state.value: 0 for state in TaskState}
    for state, count in session.execute(
        select(TaskJob.state, func.count()).group_by(TaskJob.state)
    ).all():
        counts[_state_value(state)] = int(count)

    total = sum(counts.values())
    active_states = {
        TaskState.QUEUED.value,
        TaskState.LEASED.value,
        TaskState.RUNNING.value,
        TaskState.WAITING_EXTERNAL_VALIDATION.value,
    }
    active_leases = int(
        session.scalar(
            select(func.count(func.distinct(ResourceLease.task_job_id))).where(
                ResourceLease.expires_at > current_time
            )
        )
        or 0
    )
    expiring_soon = int(
        session.scalar(
            select(func.count(func.distinct(ResourceLease.task_job_id))).where(
                ResourceLease.expires_at > current_time,
                ResourceLease.expires_at <= current_time + timedelta(seconds=30),
            )
        )
        or 0
    )
    return RuntimeMetricsResponse(
        generated_at=current_time,
        queues=RuntimeQueueMetrics(
            ready=int(redis_client.llen(ready_key)),
            processing=int(redis_client.llen(processing_key)),
        ),
        tasks=RuntimeTaskMetrics(
            total=total,
            active=sum(counts.get(state, 0) for state in active_states),
            counts=counts,
            last_finished_at=session.scalar(select(func.max(TaskJob.finished_at))),
        ),
        leases=RuntimeLeaseMetrics(
            active=active_leases,
            expiring_soon=expiring_soon,
        ),
        workers=_worker_heartbeat_metrics(
            redis_client,
            now=current_time,
            heartbeat_key=heartbeat_key,
            heartbeat_ttl_seconds=heartbeat_ttl_seconds,
        ),
    )
