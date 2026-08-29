"""One-job worker execution and crash-safe lease cleanup."""

from __future__ import annotations

from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from .models.tasks import TaskJob, TaskState
from .queue import TaskMessage, TaskQueue
from .repositories.leases import LeaseRepository
from .scheduler import DispatchGrant, Scheduler
from .services.tasks import TaskConflictError, TaskService
from .task_outcomes import WorkerOutcome


WorkerHandler = Callable[[TaskJob], WorkerOutcome]


class TaskWorker:
    """Run each leased task in isolation behind a replaceable handler."""

    def __init__(self, session: Session, *, scheduler: Scheduler | None = None) -> None:
        self.session = session
        self.scheduler = scheduler or Scheduler(session)
        self._active_owner_tokens: set[str] = set()

    def run_one(self, grant: DispatchGrant, handler: WorkerHandler) -> TaskJob:
        """Run one job and always release the matching resource leases."""
        tasks = TaskService(self.session)
        leases = LeaseRepository(self.session)
        job = tasks.get(grant.task_job_id)
        if job.state is not TaskState.LEASED:
            raise TaskConflictError(
                "invalid_worker_state",
                "worker can only start a leased task",
            )

        self._active_owner_tokens.add(grant.owner_token)
        tasks.transition(
            job.id,
            TaskState.RUNNING,
            summary="worker started",
            owner_token=grant.owner_token,
        )
        try:
            outcome = handler(job)
            if outcome.state not in {
                TaskState.WAITING_EXTERNAL_VALIDATION,
                TaskState.SUCCEEDED,
                TaskState.FAILED,
            }:
                raise TaskConflictError(
                    "invalid_worker_outcome",
                    "worker handler returned an unsupported terminal state",
                )
            if self._cancel_requested(job):
                return tasks.transition(
                    job.id,
                    TaskState.CANCELLED,
                    summary="worker stopped after cancellation request",
                )
            return tasks.transition(
                job.id,
                outcome.state,
                summary=outcome.summary,
                external_operation_ref=outcome.external_operation_ref,
                failure_code=outcome.failure_code,
                next_poll_at=outcome.next_poll_at,
            )
        except TaskConflictError:
            if self._cancel_requested(job):
                return tasks.transition(
                    job.id,
                    TaskState.CANCELLED,
                    summary="worker stopped after cancellation request",
                )
            tasks.transition(
                job.id,
                TaskState.FAILED,
                summary="worker returned an invalid outcome",
                failure_code="worker_invalid_outcome",
            )
            return tasks.get(job.id)
        except Exception:
            # Error details stay out of task events; adapters can return a
            # typed failure code through WorkerOutcome when it is safe to show.
            if self._cancel_requested(job):
                return tasks.transition(
                    job.id,
                    TaskState.CANCELLED,
                    summary="worker stopped after cancellation request",
                )
            tasks.transition(
                job.id,
                TaskState.FAILED,
                summary="worker execution failed",
                failure_code="worker_exception",
            )
            return tasks.get(job.id)
        finally:
            try:
                leases.release(
                    grant.owner_token,
                    task_job_id=grant.task_job_id,
                    lease_keys=grant.lease_keys,
                )
            finally:
                self._active_owner_tokens.discard(grant.owner_token)

    def recover_expired(self) -> list[UUID]:
        """Run the durable recovery sweep for crashed or stopped workers."""
        return self.scheduler.recover_expired()

    def _cancel_requested(self, job: TaskJob) -> bool:
        """Reload only the cancellation marker without disturbing datetime values."""
        self.session.expire(job, ["cancel_requested_at"])
        return job.cancel_requested_at is not None

    def run_message(
        self,
        message: TaskMessage,
        queue: TaskQueue,
        handler: WorkerHandler,
    ) -> TaskJob:
        """Run a reliable-list message and acknowledge it after durable completion."""
        grant = DispatchGrant(
            task_job_id=message.task_job_id,
            owner_token=message.owner_token,
            lease_keys=message.lease_keys,
            expires_at=message.expires_at,
        )
        try:
            result = self.run_one(grant, handler)
        except TaskConflictError as error:
            if error.code in {"invalid_worker_state", "lease_required", "lease_not_owned"}:
                # Redis 可能保留了旧 worker 崩溃前的消息；数据库状态才是准绳。
                queue.acknowledge(message)
                return TaskService(self.session).get(message.task_job_id)
            queue.requeue(message)
            raise
        except Exception:
            queue.requeue(message)
            raise
        queue.acknowledge(message)
        return result

    def shutdown(self, owner_token: str | None = None) -> int:
        """Release active leases before graceful shutdown."""
        tokens = (
            {owner_token}
            if owner_token is not None
            else set(self._active_owner_tokens)
        )
        leases = LeaseRepository(self.session)
        return sum(leases.release(token) for token in tokens)
