"""Durable task creation, idempotency, and state transition rules."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from ..db.base import utc_now
from ..models.accounts import LifecycleState, SocialAccount
from ..models.bindings import AccountWalletBinding, BindingState
from ..models.tasks import ResourceLease, TaskBatch, TaskEvent, TaskJob, TaskKind, TaskState
from ..models.wallets import Wallet


class TaskError(ValueError):
    """Base error for task commands."""


class TaskNotFoundError(TaskError):
    """Raised when a task identifier does not exist."""


class TaskConflictError(TaskError):
    """Raised when an idempotency or state invariant blocks a command."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class TaskCreateResult:
    """Task creation result with duplicate reuse information."""

    job: TaskJob
    reused: bool


@dataclass(frozen=True)
class TaskBatchItem:
    """One independently validated task request inside a durable batch."""

    social_account_id: UUID | None = None
    wallet_id: UUID | None = None
    binding_id: UUID | None = None
    external_target: str = ""
    priority: int = 0


@dataclass(frozen=True)
class TaskBatchCreateResult:
    """Batch creation output with its independently idempotent job results."""

    batch: TaskBatch
    results: tuple[TaskCreateResult, ...]


class TaskService:
    """Own durable task state and append-only redacted event history."""

    _TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
        TaskState.DRAFT: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
        TaskState.QUEUED: frozenset(
            {TaskState.LEASED, TaskState.PAUSED, TaskState.BLOCKED, TaskState.CANCELLED}
        ),
        TaskState.LEASED: frozenset({TaskState.RUNNING, TaskState.QUEUED, TaskState.CANCELLED}),
        TaskState.RUNNING: frozenset(
            {
                TaskState.WAITING_EXTERNAL_VALIDATION,
                TaskState.SUCCEEDED,
                TaskState.FAILED,
                TaskState.CANCELLED,
            }
        ),
        TaskState.WAITING_EXTERNAL_VALIDATION: frozenset(
            {TaskState.QUEUED, TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
        ),
        TaskState.FAILED: frozenset({TaskState.QUEUED}),
        TaskState.BLOCKED: frozenset({TaskState.QUEUED}),
        TaskState.PAUSED: frozenset({TaskState.QUEUED, TaskState.CANCELLED}),
        TaskState.SUCCEEDED: frozenset(),
        TaskState.CANCELLED: frozenset(),
    }

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        kind: TaskKind,
        *,
        social_account_id: UUID | None = None,
        wallet_id: UUID | None = None,
        binding_id: UUID | None = None,
        external_target: str,
        priority: int = 0,
        scheduled_at: datetime | None = None,
        task_batch_id: UUID | None = None,
        depends_on_task_id: UUID | None = None,
        allow_pending_binding: bool = False,
    ) -> TaskCreateResult:
        """Create a queued task or return the existing job for its idempotency key."""
        target = external_target.strip()
        if not target:
            raise TaskError("external target must not be empty")
        resolved_account, resolved_wallet = self._resolve_resources(
            kind,
            social_account_id=social_account_id,
            wallet_id=wallet_id,
            binding_id=binding_id,
            allow_pending_binding=allow_pending_binding,
        )
        if depends_on_task_id is not None:
            dependency = self.session.get(TaskJob, depends_on_task_id)
            if dependency is None:
                raise TaskConflictError("dependency_not_found", "task dependency not found")
            if dependency.state in {
                TaskState.CANCELLED,
                TaskState.FAILED,
            }:
                raise TaskConflictError("dependency_terminal_failure", "task dependency already failed")
        scope = self._scope_key(
            binding_id=binding_id,
            social_account_id=resolved_account,
            wallet_id=resolved_wallet,
        )
        idempotency_key = f"{kind.value}:{scope}:{self._target_digest(target)}"
        existing = self.session.execute(
            select(TaskJob)
            .where(TaskJob.idempotency_key == idempotency_key)
            .options(joinedload(TaskJob.events))
        ).unique().scalar_one_or_none()
        if existing is not None:
            return TaskCreateResult(existing, reused=True)

        lease_keys = self._lease_keys(resolved_account, resolved_wallet)
        job = TaskJob(
            task_batch_id=task_batch_id,
            kind=kind,
            state=TaskState.QUEUED,
            attempt=0,
            priority=priority,
            social_account_id=resolved_account,
            wallet_id=resolved_wallet,
            binding_id=binding_id,
            depends_on_task_id=depends_on_task_id,
            external_target=target,
            idempotency_key=idempotency_key,
            lease_keys=lease_keys,
            scheduled_at=scheduled_at or utc_now(),
        )
        try:
            with self.session.begin_nested():
                self.session.add(job)
                self.session.flush()
                self._append_event(
                    job,
                    event_type="task_created",
                    from_state=None,
                    to_state=TaskState.QUEUED,
                    summary="task queued",
                )
        except IntegrityError:
            existing = self.session.execute(
                select(TaskJob)
                .where(TaskJob.idempotency_key == idempotency_key)
                .options(joinedload(TaskJob.events))
            ).unique().scalar_one_or_none()
            if existing is None:
                raise
            return TaskCreateResult(existing, reused=True)
        return TaskCreateResult(job, reused=False)

    def create_batch(
        self,
        *,
        name: str,
        kind: TaskKind,
        items: list[TaskBatchItem],
        dispatch_limit: int = 10,
    ) -> TaskBatchCreateResult:
        """Create a durable batch and independently idempotent child jobs."""
        normalized_name = name.strip()
        if not normalized_name:
            raise TaskError("batch name must not be empty")
        if not items:
            raise TaskError("batch must contain at least one task item")
        if dispatch_limit < 1:
            raise TaskError("dispatch limit must be positive")

        batch = TaskBatch(
            name=normalized_name,
            kind=kind,
            workflow_type=f"stage:{kind.value}",
            dispatch_limit=dispatch_limit,
            state="active",
        )
        self.session.add(batch)
        self.session.flush()

        results = tuple(
            self.create(
                kind,
                social_account_id=item.social_account_id,
                wallet_id=item.wallet_id,
                binding_id=item.binding_id,
                external_target=item.external_target,
                priority=item.priority,
                task_batch_id=batch.id,
            )
            for item in items
        )
        return TaskBatchCreateResult(batch=batch, results=results)

    def get(self, task_id: UUID) -> TaskJob:
        """Load one task and its append-only events."""
        job = self.session.execute(
            select(TaskJob)
            .where(TaskJob.id == task_id)
            .options(joinedload(TaskJob.events))
        ).unique().scalar_one_or_none()
        if job is None:
            raise TaskNotFoundError("task not found")
        return job

    def get_batch(self, batch_id: UUID) -> TaskBatch:
        """Load one batch with all of its redacted child task state."""
        batch = self.session.execute(
            select(TaskBatch)
            .where(TaskBatch.id == batch_id)
            .options(joinedload(TaskBatch.jobs).joinedload(TaskJob.events))
        ).unique().scalar_one_or_none()
        if batch is None:
            raise TaskNotFoundError("task batch not found")
        return batch

    def pause_batch(self, batch_id: UUID) -> TaskBatch:
        """Stop future dispatch for a batch and pause every still-queued child."""
        batch = self.get_batch(batch_id)
        if batch.state != "active":
            raise TaskConflictError("batch_not_active", "only active batches can be paused")
        batch.state = "paused"
        batch.paused_at = utc_now()
        for job in batch.jobs:
            if job.state is TaskState.QUEUED:
                self.transition(job.id, TaskState.PAUSED, summary="batch paused")
        self.session.flush()
        return batch

    def resume_batch(self, batch_id: UUID) -> TaskBatch:
        """Return a paused batch to dispatch and requeue its paused children."""
        batch = self.get_batch(batch_id)
        if batch.state != "paused":
            raise TaskConflictError("batch_not_paused", "only paused batches can be resumed")
        batch.state = "active"
        batch.paused_at = None
        for job in batch.jobs:
            if job.state is TaskState.PAUSED:
                self.transition(job.id, TaskState.QUEUED, summary="batch resumed")
        self.session.flush()
        return batch

    def cancel_batch(self, batch_id: UUID) -> TaskBatch:
        """Cancel pending work and request cancellation for active workers."""
        batch = self.get_batch(batch_id)
        if batch.state == "cancelled":
            return batch
        if batch.state not in {"active", "paused"}:
            raise TaskConflictError("batch_not_cancellable", "batch cannot be cancelled")
        batch.state = "cancelled"
        batch.paused_at = utc_now()
        for job in batch.jobs:
            self.request_cancel(job.id, summary="batch cancelled")
        self.session.flush()
        return batch

    def list_jobs(self, *, offset: int = 0, limit: int = 50) -> tuple[list[TaskJob], int]:
        """Return task rows in creation order with redacted event history."""
        jobs = self.session.scalars(
            select(TaskJob)
            .options(joinedload(TaskJob.events))
            .order_by(TaskJob.created_at, TaskJob.id)
            .offset(offset)
            .limit(limit)
        ).unique().all()
        total = self.session.scalar(select(func.count()).select_from(TaskJob)) or 0
        return list(jobs), total

    def transition(
        self,
        task_id: UUID,
        to_state: TaskState,
        *,
        summary: str | None = None,
        external_operation_ref: str | None = None,
        failure_code: str | None = None,
        next_poll_at: datetime | None = None,
        owner_token: str | None = None,
    ) -> TaskJob:
        """Apply one permitted transition and append its event atomically."""
        job = self.get(task_id)
        from_state = job.state
        if to_state not in self._TRANSITIONS.get(from_state, frozenset()):
            raise TaskConflictError(
                "invalid_transition",
                f"{from_state.value} cannot transition to {to_state.value}",
            )
        if to_state is TaskState.RUNNING:
            self._require_owned_leases(job, owner_token)

        job.state = to_state
        if external_operation_ref is not None:
            job.external_operation_ref = external_operation_ref.strip() or None
        if failure_code is not None:
            job.failure_code = failure_code.strip() or None
        if next_poll_at is not None:
            job.next_poll_at = next_poll_at
        if to_state is TaskState.RUNNING and job.started_at is None:
            job.started_at = utc_now()
        if to_state in {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}:
            job.finished_at = utc_now()
        job.result_summary = summary.strip() if summary and summary.strip() else job.result_summary
        self.session.flush()
        self._append_event(
            job,
            event_type="state_transition",
            from_state=from_state,
            to_state=to_state,
            summary=summary,
        )
        self.session.flush()
        return job

    def pause(self, task_id: UUID) -> TaskJob:
        """Pause a queued task before dispatch."""
        return self.transition(task_id, TaskState.PAUSED, summary="task paused")

    def cancel(self, task_id: UUID) -> TaskJob:
        """Cancel pending work or request cancellation for an active worker."""
        return self.request_cancel(task_id, summary="task cancelled")

    def request_cancel(self, task_id: UUID, *, summary: str) -> TaskJob:
        """Cancel pending work and mark running work for cooperative cancellation."""
        job = self.get(task_id)
        if job.state in {
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.BLOCKED,
            TaskState.CANCELLED,
        }:
            return job
        if job.state is TaskState.RUNNING:
            if job.cancel_requested_at is None:
                job.cancel_requested_at = utc_now()
                self.session.flush()
                self._append_event(
                    job,
                    event_type="cancel_requested",
                    from_state=job.state,
                    to_state=None,
                    summary=summary,
                )
                self.session.flush()
            return job
        if job.state is TaskState.LEASED:
            self.session.execute(delete(ResourceLease).where(ResourceLease.task_job_id == job.id))
            self.session.flush()
        return self.transition(job.id, TaskState.CANCELLED, summary=summary)

    def refresh_dependency_states(self) -> int:
        """Block queued dependents after failure and requeue them after recovery."""
        changed = 0
        while True:
            round_changed = 0
            jobs = self.session.scalars(
                select(TaskJob)
                .where(
                    TaskJob.state.in_(
                        (TaskState.QUEUED, TaskState.BLOCKED)
                    ),
                    TaskJob.depends_on_task_id.is_not(None),
                )
                .order_by(TaskJob.created_at, TaskJob.id)
            ).all()
            for job in jobs:
                dependency = self.session.get(TaskJob, job.depends_on_task_id)
                if dependency is None:
                    continue
                if dependency.state in {
                    TaskState.FAILED,
                    TaskState.BLOCKED,
                    TaskState.CANCELLED,
                } and job.state is TaskState.QUEUED:
                    self.transition(
                        job.id,
                        TaskState.BLOCKED,
                        summary="blocked by failed dependency",
                        failure_code="dependency_failed",
                    )
                    round_changed += 1
                elif dependency.state is TaskState.SUCCEEDED and job.state is TaskState.BLOCKED:
                    job.failure_code = None
                    job.finished_at = None
                    self.session.flush()
                    self.transition(
                        job.id,
                        TaskState.QUEUED,
                        summary="dependency recovered; task requeued",
                    )
                    round_changed += 1
            changed += round_changed
            if round_changed == 0:
                return changed

    def retry(self, task_id: UUID) -> TaskJob:
        """Requeue one failed task while preserving its idempotency boundary."""
        job = self.get(task_id)
        if job.state is not TaskState.FAILED:
            raise TaskConflictError("invalid_retry_state", "only failed tasks can be retried")
        job.attempt += 1
        job.failure_code = None
        job.finished_at = None
        job.next_poll_at = None
        self.session.flush()
        return self.transition(task_id, TaskState.QUEUED, summary="task retry queued")

    def poll(self, task_id: UUID) -> TaskJob:
        """Requeue a waiting external validation for a poll continuation."""
        job = self.get(task_id)
        if job.state is not TaskState.WAITING_EXTERNAL_VALIDATION:
            raise TaskConflictError(
                "invalid_poll_state",
                "only tasks waiting for external validation can be polled",
            )
        job.next_poll_at = None
        self.session.flush()
        return self.transition(task_id, TaskState.QUEUED, summary="external status poll queued")

    def requeue_due_polls(self, *, now: datetime | None = None) -> int:
        """将达到轮询时间的外部校验任务自动重新入队。"""
        current_time = now or utc_now()
        jobs = self.session.scalars(
            select(TaskJob)
            .where(
                TaskJob.state == TaskState.WAITING_EXTERNAL_VALIDATION,
                TaskJob.next_poll_at.is_not(None),
                TaskJob.next_poll_at <= current_time,
            )
            .order_by(TaskJob.next_poll_at, TaskJob.created_at, TaskJob.id)
        ).all()
        changed = 0
        for job in jobs:
            self.poll(job.id)
            changed += 1
        return changed

    def recover_expired_lease(self, task_id: UUID) -> TaskJob:
        """Requeue a leased or running task after its worker lease expires."""
        job = self.get(task_id)
        if job.state not in {TaskState.LEASED, TaskState.RUNNING}:
            raise TaskConflictError(
                "invalid_recovery_state",
                "only leased or running tasks can be recovered",
            )

        from_state = job.state
        job.state = TaskState.QUEUED
        job.started_at = None
        job.finished_at = None
        job.failure_code = "lease_expired"
        job.result_summary = "task requeued after worker lease expiry"
        job.next_poll_at = None
        self.session.flush()
        self._append_event(
            job,
            event_type="lease_expired_recovery",
            from_state=from_state,
            to_state=TaskState.QUEUED,
            summary="task requeued after worker lease expiry",
        )
        self.session.flush()
        return job

    def _resolve_resources(
        self,
        kind: TaskKind,
        *,
        social_account_id: UUID | None,
        wallet_id: UUID | None,
        binding_id: UUID | None,
        allow_pending_binding: bool = False,
    ) -> tuple[UUID | None, UUID | None]:
        """Normalize task resource IDs and validate binding ownership."""
        if kind in {TaskKind.REPOST, TaskKind.CLAIM, TaskKind.BALANCE_SYNC}:
            if binding_id is None:
                raise TaskError("repost, claim, and balance sync tasks require binding_id")
            binding = self.session.get(AccountWalletBinding, binding_id)
            if binding is None:
                raise TaskConflictError("binding_not_found", "binding not found")
            if kind is TaskKind.BALANCE_SYNC and binding.state is not BindingState.BOUND:
                raise TaskConflictError("binding_not_confirmed", "binding is not confirmed")
            if kind is not TaskKind.BALANCE_SYNC and binding.state is not BindingState.BOUND and not (
                allow_pending_binding and binding.state is BindingState.PENDING
            ):
                raise TaskConflictError("binding_not_confirmed", "binding is not confirmed")
            if (
                social_account_id is not None
                and social_account_id != binding.social_account_id
            ) or (wallet_id is not None and wallet_id != binding.wallet_id):
                raise TaskConflictError(
                    "binding_resource_mismatch",
                    "task resources do not match binding",
                )
            return binding.social_account_id, binding.wallet_id
        if kind is TaskKind.BIND:
            if binding_id is not None:
                if social_account_id is not None or wallet_id is not None:
                    raise TaskError("bind task binding scope must not include resource IDs")
                binding = self.session.get(AccountWalletBinding, binding_id)
                if binding is None:
                    raise TaskConflictError("binding_not_found", "binding not found")
                if binding.state is not BindingState.PENDING:
                    raise TaskConflictError("binding_not_pending", "binding is not pending")
                return binding.social_account_id, binding.wallet_id
            if social_account_id is None or wallet_id is None:
                raise TaskError("bind tasks require a pending binding or account and wallet IDs")
            account = self.session.get(SocialAccount, social_account_id)
            if account is None:
                raise TaskConflictError("account_not_found", "account not found")
            if account.state is not LifecycleState.ACTIVE:
                raise TaskConflictError("archived_account", "account is archived")
            wallet = self.session.get(Wallet, wallet_id)
            if wallet is None:
                raise TaskConflictError("wallet_not_found", "wallet not found")
            if wallet.state != "active" or wallet.archived_at is not None:
                raise TaskConflictError("archived_wallet", "wallet is archived")
            historical_binding = self.session.scalar(
                select(AccountWalletBinding)
                .where(
                    (AccountWalletBinding.social_account_id == account.id)
                    | (AccountWalletBinding.wallet_id == wallet.id),
                    AccountWalletBinding.state.in_(
                        (BindingState.PENDING, BindingState.BOUND)
                    ),
                )
                .order_by(AccountWalletBinding.created_at)
            )
            if historical_binding is not None:
                code = (
                    "binding_in_progress"
                    if historical_binding.state is BindingState.PENDING
                    else "already_bound"
                )
                raise TaskConflictError(code, "account or wallet already has a binding")
            return social_account_id, wallet_id
        if kind is TaskKind.VERIFY_ACCOUNT:
            if social_account_id is None or wallet_id is not None or binding_id is not None:
                raise TaskError("account verification tasks require social_account_id only")
            account = self.session.get(SocialAccount, social_account_id)
            if account is None:
                raise TaskConflictError("account_not_found", "account not found")
            if account.state is not LifecycleState.ACTIVE:
                raise TaskConflictError("archived_account", "account is archived")
            return social_account_id, None
        raise TaskError("unsupported task kind")

    @staticmethod
    def _scope_key(
        *,
        binding_id: UUID | None,
        social_account_id: UUID | None,
        wallet_id: UUID | None,
    ) -> str:
        """Build a stable readable scope without including target content."""
        if binding_id is not None:
            return f"binding:{binding_id}"
        if social_account_id is not None and wallet_id is not None:
            return f"account:{social_account_id}:wallet:{wallet_id}"
        if social_account_id is not None:
            return f"account:{social_account_id}"
        if wallet_id is not None:
            return f"wallet:{wallet_id}"
        return "global"

    @staticmethod
    def _target_digest(target: str) -> str:
        """Hash the provider target so raw URLs never enter task responses."""
        return hashlib.sha256(target.encode("utf-8")).hexdigest()

    @staticmethod
    def _lease_keys(
        social_account_id: UUID | None,
        wallet_id: UUID | None,
    ) -> list[str]:
        """Return canonical resource lease keys for the future scheduler."""
        keys: list[str] = []
        if social_account_id is not None:
            keys.append(f"account:{social_account_id}")
        if wallet_id is not None:
            keys.append(f"wallet:{wallet_id}")
        return keys

    def _require_owned_leases(self, job: TaskJob, owner_token: str | None) -> None:
        """Require one unexpired matching lease for each task resource."""
        if not owner_token:
            raise TaskConflictError("lease_required", "running transition requires an owner token")
        if not job.lease_keys:
            raise TaskConflictError("lease_required", "running transition requires resource leases")
        leases = self.session.scalars(
            select(ResourceLease).where(
                ResourceLease.task_job_id == job.id,
                ResourceLease.lease_key.in_(job.lease_keys),
                ResourceLease.owner_token == owner_token,
                ResourceLease.expires_at > utc_now(),
            )
        ).all()
        if {lease.lease_key for lease in leases} != set(job.lease_keys):
            raise TaskConflictError(
                "lease_not_owned",
                "owner token does not hold all active task leases",
            )

    def _append_event(
        self,
        job: TaskJob,
        *,
        event_type: str,
        from_state: TaskState | None,
        to_state: TaskState | None,
        summary: str | None,
    ) -> None:
        """Append one redacted event in the same transaction as the mutation."""
        last_sequence = self.session.scalar(
            select(func.max(TaskEvent.sequence)).where(TaskEvent.task_job_id == job.id)
        )
        event = TaskEvent(
            task_job_id=job.id,
            sequence=(last_sequence or 0) + 1,
            event_type=event_type,
            from_state=from_state.value if from_state is not None else None,
            to_state=to_state.value if to_state is not None else None,
            summary=summary,
            event_metadata=None,
            created_at=utc_now(),
        )
        job.events.append(event)
        self.session.flush()
