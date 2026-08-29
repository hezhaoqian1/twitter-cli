"""Read-only runtime metrics for the manager control plane."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..models.accounts import AccountHealth, LifecycleState, SocialAccount
from ..models.bindings import AccountWalletBinding, BindingState
from ..models.tasks import ResourceLease, TaskJob, TaskKind, TaskState
from ..models.wallets import Wallet
from ..schemas.runtime import (
    OperationResourceCounts,
    OperationsSummaryResponse,
    OperationStageSummary,
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


def collect_operations_summary(
    session: Session,
    *,
    now: datetime | None = None,
) -> OperationsSummaryResponse:
    """聚合分阶段运营视图，只读取公开资源状态和任务状态。"""
    current_time = now or utc_now()
    active_account_ids = set(
        session.scalars(
            select(SocialAccount.id).where(SocialAccount.state == LifecycleState.ACTIVE)
        ).all()
    )
    healthy_accounts = int(
        session.scalar(
            select(func.count()).select_from(SocialAccount).where(
                SocialAccount.state == LifecycleState.ACTIVE,
                SocialAccount.health == AccountHealth.HEALTHY,
            )
        )
        or 0
    )
    active_wallet_ids = set(
        session.scalars(
            select(Wallet.id).where(Wallet.state == "active", Wallet.archived_at.is_(None))
        ).all()
    )
    active_bindings = session.scalars(
        select(AccountWalletBinding).where(AccountWalletBinding.archived_at.is_(None))
    ).all()
    occupied_account_ids = {binding.social_account_id for binding in active_bindings}
    occupied_wallet_ids = {binding.wallet_id for binding in active_bindings}
    bound_binding_ids = {
        binding.id for binding in active_bindings if binding.state is BindingState.BOUND
    }
    pending_bindings = sum(1 for binding in active_bindings if binding.state is BindingState.PENDING)
    waiting_tasks = _task_counts_by_kind(session, TaskState.WAITING_EXTERNAL_VALIDATION)
    failed_tasks = _task_counts_by_kind(session, TaskState.FAILED)
    succeeded_repost_bindings = set(
        session.scalars(
            select(TaskJob.binding_id).where(
                TaskJob.kind == TaskKind.REPOST,
                TaskJob.state == TaskState.SUCCEEDED,
                TaskJob.binding_id.is_not(None),
            )
        ).all()
    )
    succeeded_claim_bindings = set(
        session.scalars(
            select(TaskJob.binding_id).where(
                TaskJob.kind == TaskKind.CLAIM,
                TaskJob.state == TaskState.SUCCEEDED,
                TaskJob.binding_id.is_not(None),
            )
        ).all()
    )
    claim_ready = len((bound_binding_ids & succeeded_repost_bindings) - succeeded_claim_bindings)
    available_accounts = len(active_account_ids - occupied_account_ids)
    available_wallets = len(active_wallet_ids - occupied_wallet_ids)
    bind_ready = min(available_accounts, available_wallets)
    resources = OperationResourceCounts(
        accounts_total=int(session.scalar(select(func.count()).select_from(SocialAccount)) or 0),
        accounts_active=len(active_account_ids),
        accounts_healthy=healthy_accounts,
        accounts_available_for_binding=available_accounts,
        wallets_total=int(session.scalar(select(func.count()).select_from(Wallet)) or 0),
        wallets_active=len(active_wallet_ids),
        wallets_available_for_binding=available_wallets,
        bindings_total=len(active_bindings),
        bindings_pending=pending_bindings,
        bindings_bound=len(bound_binding_ids),
    )
    return OperationsSummaryResponse(
        generated_at=current_time,
        resources=resources,
        stages=[
            OperationStageSummary(
                key="verify",
                label="登录校验",
                ready=resources.accounts_active,
                waiting=waiting_tasks.get("verify_account", 0),
                failed=failed_tasks.get("verify_account", 0),
                detail="可对全部活跃 X 账号重新校验会话",
            ),
            OperationStageSummary(
                key="bind",
                label="绑定地址",
                ready=bind_ready,
                waiting=pending_bindings + waiting_tasks.get("bind", 0),
                failed=failed_tasks.get("bind", 0),
                detail="按未占用账号和未占用地址自动配对",
            ),
            OperationStageSummary(
                key="repost",
                label="转发推文",
                ready=resources.bindings_bound,
                waiting=waiting_tasks.get("repost", 0),
                failed=failed_tasks.get("repost", 0),
                detail="仅对已确认绑定的账号地址对创建任务",
            ),
            OperationStageSummary(
                key="claim",
                label="领取奖励",
                ready=claim_ready,
                waiting=waiting_tasks.get("claim", 0),
                failed=failed_tasks.get("claim", 0),
                detail="转发校验成功后进入领取候选池",
            ),
        ],
    )


def _task_counts_by_kind(session: Session, state: TaskState) -> dict[str, int]:
    """按任务类型统计某个状态，给运营台显示瓶颈位置。"""
    return {kind.value: int(count) for kind, count in session.execute(
        select(TaskJob.kind, func.count()).where(TaskJob.state == state).group_by(TaskJob.kind)
    ).all()}
