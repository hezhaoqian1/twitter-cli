"""One-job worker execution and crash-safe lease cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from .models.tasks import TaskJob, TaskState
from .queue import TaskMessage, TaskQueue
from .repositories.leases import LeaseRepository
from .scheduler import DispatchGrant, Scheduler
from .services.tasks import TaskConflictError, TaskService


@dataclass(frozen=True)
class WorkerOutcome:
    """Redacted handler result mapped to the durable task state machine."""

    state: TaskState
    summary: str | None = None
    external_operation_ref: str | None = None
    failure_code: str | None = None
    next_poll_at: datetime | None = None


WorkerHandler = Callable[[TaskJob], WorkerOutcome]


class TaskWorker:
    """Run each leased task in isolation behind a replaceable handler."""

    def __init__(self, session: Session, *, scheduler: Scheduler | None = None) -> None:
        self.session = session
        self.scheduler = scheduler or Scheduler(session)

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
            return tasks.transition(
                job.id,
                outcome.state,
                summary=outcome.summary,
                external_operation_ref=outcome.external_operation_ref,
                failure_code=outcome.failure_code,
                next_poll_at=outcome.next_poll_at,
            )
        except TaskConflictError:
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
            tasks.transition(
                job.id,
                TaskState.FAILED,
                summary="worker execution failed",
                failure_code="worker_exception",
            )
            return tasks.get(job.id)
        finally:
            leases.release(
                grant.owner_token,
                task_job_id=grant.task_job_id,
                lease_keys=grant.lease_keys,
            )

    def recover_expired(self) -> list[UUID]:
        """Run the durable recovery sweep for crashed or stopped workers."""
        return self.scheduler.recover_expired()

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
        except Exception:
            queue.requeue(message)
            raise
        queue.acknowledge(message)
        return result

    def shutdown(self, owner_token: str) -> int:
        """Release every lease owned by this worker before graceful shutdown."""
        return LeaseRepository(self.session).release(owner_token)
