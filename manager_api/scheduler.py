"""Fair durable task dispatch and expired-lease recovery."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .db.base import utc_now
from .models.tasks import ResourceLease, TaskJob, TaskState
from .repositories.leases import LeaseGrant, LeaseRepository
from .services.tasks import TaskService


@dataclass(frozen=True)
class DispatchGrant:
    """A leased task ready for a worker-owned running transition."""

    task_job_id: UUID
    owner_token: str
    lease_keys: tuple[str, ...]
    expires_at: datetime


class Scheduler:
    """Select eligible jobs fairly and convert them to leased state."""

    def __init__(
        self,
        session: Session,
        *,
        lease_ttl_seconds: float = 120.0,
        worker_concurrency: int = 3,
        browser_concurrency: int = 2,
    ) -> None:
        self.session = session
        self.lease_ttl_seconds = lease_ttl_seconds
        self.worker_concurrency = worker_concurrency
        self.browser_concurrency = browser_concurrency

    def dispatch_once(
        self,
        *,
        limit: int | None = None,
        now: datetime | None = None,
    ) -> list[DispatchGrant]:
        """Lease up to limit jobs while skipping blocked resources."""
        requested_limit = limit if limit is not None else self.worker_concurrency
        if requested_limit < 1:
            return []
        current_time = now or utc_now()
        active_leases = self.session.scalar(
            select(func.count(func.distinct(ResourceLease.task_job_id))).where(
                ResourceLease.expires_at > current_time
            )
        ) or 0
        capacity = min(
            requested_limit,
            self.worker_concurrency,
            self.browser_concurrency,
        ) - int(active_leases)
        if capacity < 1:
            return []

        candidates = self._fair_candidates(limit=capacity, now=current_time)
        grants: list[DispatchGrant] = []
        leases = LeaseRepository(self.session)
        tasks = TaskService(self.session)

        for candidate in candidates:
            try:
                with self.session.begin_nested():
                    job = self.session.get(TaskJob, candidate.id)
                    if job is None or job.state is not TaskState.QUEUED:
                        continue
                    grant = leases.acquire(
                        job,
                        ttl_seconds=self.lease_ttl_seconds,
                        now=current_time,
                    )
                    if grant is None:
                        continue
                    tasks.transition(
                        job.id,
                        TaskState.LEASED,
                        summary="lease acquired",
                    )
            except Exception:
                # The savepoint keeps one malformed/stale candidate from
                # rolling back successful dispatches selected earlier.
                continue

            grants.append(self._dispatch_grant(candidate.id, grant))

        return grants

    def dispatch_to_queue(self, queue: object, *, limit: int | None = None) -> list[DispatchGrant]:
        """Persist leases first, then enqueue their internal dispatch messages."""
        from .queue import TaskQueue

        if not isinstance(queue, TaskQueue):
            raise TypeError("queue must implement TaskQueue")
        grants = self.dispatch_once(limit=limit)
        for grant in grants:
            queue.enqueue(grant)
        return grants

    def recover_expired(self, *, now: datetime | None = None) -> list[UUID]:
        """Requeue leased/running jobs whose worker lease has expired."""
        current_time = now or utc_now()
        jobs = self.session.scalars(
            select(TaskJob)
            .join(ResourceLease, ResourceLease.task_job_id == TaskJob.id)
            .where(
                ResourceLease.expires_at <= current_time,
                TaskJob.state.in_((TaskState.LEASED, TaskState.RUNNING)),
            )
            .order_by(TaskJob.created_at, TaskJob.id)
        ).unique().all()

        recovered: list[UUID] = []
        tasks = TaskService(self.session)
        for job in jobs:
            try:
                with self.session.begin_nested():
                    # Recovery discards all leases for the job so a sibling
                    # key cannot remain locked after a partial worker crash.
                    self.session.execute(
                        delete(ResourceLease).where(ResourceLease.task_job_id == job.id)
                    )
                    tasks.recover_expired_lease(job.id)
            except Exception:
                continue
            recovered.append(job.id)
        return recovered

    def _fair_candidates(self, *, limit: int, now: datetime) -> list[TaskJob]:
        """Round-robin the oldest eligible job from each batch."""
        jobs = self.session.scalars(
            select(TaskJob)
            .where(
                TaskJob.state == TaskState.QUEUED,
                TaskJob.scheduled_at <= now,
                or_(TaskJob.next_poll_at.is_(None), TaskJob.next_poll_at <= now),
            )
            .order_by(TaskJob.scheduled_at, TaskJob.created_at, TaskJob.id)
        ).all()

        groups: dict[str, deque[TaskJob]] = {}
        order: list[str] = []
        for job in jobs:
            group_key = (
                f"batch:{job.task_batch_id}"
                if job.task_batch_id is not None
                else f"job:{job.id}"
            )
            if group_key not in groups:
                groups[group_key] = deque()
                order.append(group_key)
            groups[group_key].append(job)

        selected: list[TaskJob] = []
        while order and len(selected) < limit:
            next_order: list[str] = []
            for group_key in order:
                queue = groups[group_key]
                if queue:
                    selected.append(queue.popleft())
                if queue:
                    next_order.append(group_key)
                if len(selected) >= limit:
                    break
            order = next_order
        return selected

    @staticmethod
    def _dispatch_grant(task_job_id: UUID, grant: LeaseGrant) -> DispatchGrant:
        """Convert repository output to the worker-facing dispatch contract."""
        return DispatchGrant(
            task_job_id=task_job_id,
            owner_token=grant.owner_token,
            lease_keys=grant.lease_keys,
            expires_at=grant.expires_at,
        )
