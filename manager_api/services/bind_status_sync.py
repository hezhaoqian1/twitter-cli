"""Read-only sync helpers for pending Kredo/X bindings."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..models.accounts import AccountHealth, AccountSecret, LifecycleState, SocialAccount
from ..models.bindings import AccountWalletBinding, BindingState
from ..models.tasks import ResourceLease, TaskBatch, TaskJob, TaskKind, TaskState
from ..models.wallets import Wallet, WalletSecret
from .tasks import TaskService

KREDO_BIND_STATUS_TARGET = "kredo:bind-status"


@dataclass(frozen=True)
class BindStatusSyncSummary:
    """UI 和脚本共用的脱敏汇总，不包含账号、地址或密钥。"""

    apply: bool
    name: str
    limit: int
    pending_bindings: int
    selected: int
    created_jobs: int
    reused_jobs: int
    paused_action_jobs: int
    skipped_existing_status_job: int
    skipped_active_lease: int
    skipped_missing_secret: int


@dataclass(frozen=True)
class BindStatusCandidateContext:
    """批量预加载候选绑定的非密钥状态，避免远端数据库逐行查询。"""

    existing_status_binding_ids: set[UUID]
    active_lease_keys: set[str]
    account_secret_ids: set[UUID]
    wallet_secret_ids: set[UUID]


def _lease_key_for_account(account_id: object) -> str:
    """生成账号租约 key，避免状态读取和正在执行的任务抢同一账号。"""
    return f"account:{account_id}"


def _lease_key_for_wallet(wallet_id: object) -> str:
    """生成钱包租约 key，避免状态读取和正在执行的任务抢同一钱包。"""
    return f"wallet:{wallet_id}"


def _pause_unsafe_action_jobs(session: Session, binding: AccountWalletBinding) -> int:
    """暂停同一 binding 上还没执行的首绑任务，避免同步时再次触发 OAuth。"""
    task_service = TaskService(session)
    jobs = session.scalars(
        select(TaskJob)
        .where(
            TaskJob.kind == TaskKind.BIND,
            TaskJob.binding_id == binding.id,
            TaskJob.state == TaskState.QUEUED,
            TaskJob.external_operation_ref.is_(None),
            TaskJob.external_target != KREDO_BIND_STATUS_TARGET,
        )
        .order_by(TaskJob.created_at, TaskJob.id)
    ).all()
    paused = 0
    for job in jobs:
        task_service.pause(job.id)
        paused += 1
    return paused


def _candidate_bindings(session: Session) -> list[AccountWalletBinding]:
    """读取所有 pending binding，并过滤已归档账号和钱包。"""
    return list(
        session.scalars(
            select(AccountWalletBinding)
            .join(SocialAccount, SocialAccount.id == AccountWalletBinding.social_account_id)
            .join(Wallet, Wallet.id == AccountWalletBinding.wallet_id)
            .where(
                AccountWalletBinding.state == BindingState.PENDING,
                AccountWalletBinding.archived_at.is_(None),
                SocialAccount.state == LifecycleState.ACTIVE,
                SocialAccount.archived_at.is_(None),
                SocialAccount.health.in_(
                    (AccountHealth.HEALTHY, AccountHealth.UNKNOWN, AccountHealth.DEGRADED)
                ),
                Wallet.state == "active",
                Wallet.archived_at.is_(None),
            )
            .order_by(AccountWalletBinding.created_at, AccountWalletBinding.id)
        ).all()
    )


def _candidate_context(
    session: Session,
    bindings: list[AccountWalletBinding],
) -> BindStatusCandidateContext:
    """一次性读取候选 binding 的状态同步前置条件。"""
    if not bindings:
        return BindStatusCandidateContext(set(), set(), set(), set())

    binding_ids = [binding.id for binding in bindings]
    account_ids = [binding.social_account_id for binding in bindings]
    wallet_ids = [binding.wallet_id for binding in bindings]
    lease_keys = {
        *(_lease_key_for_account(account_id) for account_id in account_ids),
        *(_lease_key_for_wallet(wallet_id) for wallet_id in wallet_ids),
    }
    existing_status_binding_ids = {
        binding_id
        for binding_id in session.scalars(
            select(TaskJob.binding_id).where(
                TaskJob.kind == TaskKind.BIND,
                TaskJob.binding_id.in_(binding_ids),
                TaskJob.state.in_(
                    (
                        TaskState.QUEUED,
                        TaskState.LEASED,
                        TaskState.RUNNING,
                        TaskState.WAITING_EXTERNAL_VALIDATION,
                    )
                ),
                (
                    (TaskJob.external_operation_ref.is_not(None))
                    | (TaskJob.external_target == KREDO_BIND_STATUS_TARGET)
                ),
            )
        ).all()
        if binding_id is not None
    }
    active_lease_keys = set(
        session.scalars(
            select(ResourceLease.lease_key).where(
                ResourceLease.lease_key.in_(lease_keys),
                ResourceLease.expires_at > utc_now(),
            )
        ).all()
    )
    account_secret_ids = set(
        session.scalars(
            select(AccountSecret.social_account_id).where(
                AccountSecret.social_account_id.in_(account_ids),
                AccountSecret.is_current.is_(True),
            )
        ).all()
    )
    wallet_secret_ids = set(
        session.scalars(
            select(WalletSecret.wallet_id).where(
                WalletSecret.wallet_id.in_(wallet_ids),
                WalletSecret.is_current.is_(True),
            )
        ).all()
    )
    return BindStatusCandidateContext(
        existing_status_binding_ids=existing_status_binding_ids,
        active_lease_keys=active_lease_keys,
        account_secret_ids=account_secret_ids,
        wallet_secret_ids=wallet_secret_ids,
    )


def queue_bind_status_sync(
    session: Session,
    *,
    name: str,
    limit: int,
    dispatch_limit: int,
    apply: bool,
) -> BindStatusSyncSummary:
    """为 pending binding 创建只读状态同步任务，不重新点击绑定。"""
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("batch name must not be empty")
    if limit < 1:
        raise ValueError("limit must be positive")
    if dispatch_limit < 1:
        raise ValueError("dispatch limit must be positive")

    pending_total = int(
        session.scalar(
            select(func.count())
            .select_from(AccountWalletBinding)
            .where(
                AccountWalletBinding.state == BindingState.PENDING,
                AccountWalletBinding.archived_at.is_(None),
            )
        )
        or 0
    )

    selected: list[AccountWalletBinding] = []
    skipped_existing_status_job = 0
    skipped_active_lease = 0
    skipped_missing_secret = 0
    candidates = _candidate_bindings(session)
    candidate_context = _candidate_context(session, candidates)
    for binding in candidates:
        if len(selected) >= limit:
            break
        if binding.id in candidate_context.existing_status_binding_ids:
            skipped_existing_status_job += 1
            continue
        account_lease_key = _lease_key_for_account(binding.social_account_id)
        wallet_lease_key = _lease_key_for_wallet(binding.wallet_id)
        if (
            account_lease_key in candidate_context.active_lease_keys
            or wallet_lease_key in candidate_context.active_lease_keys
        ):
            skipped_active_lease += 1
            continue
        if (
            binding.social_account_id not in candidate_context.account_secret_ids
            or binding.wallet_id not in candidate_context.wallet_secret_ids
        ):
            skipped_missing_secret += 1
            continue
        selected.append(binding)

    created_jobs = 0
    reused_jobs = 0
    paused_action_jobs = 0
    if apply and selected:
        batch = TaskBatch(
            name=normalized_name,
            kind=TaskKind.BIND,
            workflow_type="stage:bind-status",
            dispatch_limit=dispatch_limit,
            state="active",
        )
        session.add(batch)
        session.flush()
        task_service = TaskService(session)
        for binding in selected:
            paused_action_jobs += _pause_unsafe_action_jobs(session, binding)
            result = task_service.create(
                TaskKind.BIND,
                binding_id=binding.id,
                external_target=KREDO_BIND_STATUS_TARGET,
                external_operation_ref=f"kredo:bind-status:{binding.id}",
                task_batch_id=batch.id,
                priority=10,
            )
            if result.reused:
                reused_jobs += 1
            else:
                created_jobs += 1

    return BindStatusSyncSummary(
        apply=apply,
        name=normalized_name,
        limit=limit,
        pending_bindings=pending_total,
        selected=len(selected),
        created_jobs=created_jobs,
        reused_jobs=reused_jobs,
        paused_action_jobs=paused_action_jobs,
        skipped_existing_status_job=skipped_existing_status_job,
        skipped_active_lease=skipped_active_lease,
        skipped_missing_secret=skipped_missing_secret,
    )
