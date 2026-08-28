from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.queue import TaskMessage
from manager_api.runner import TaskRunner
from manager_api.scheduler import Scheduler
from manager_api.services.bindings import BindingService
from manager_api.services.workflows import WorkflowBatchItem, WorkflowService
from manager_api.task_outcomes import WorkerOutcome
from manager_api.worker import TaskWorker


@dataclass
class MemoryQueue:
    """Redis reliable-list double used by the local batch smoke test."""

    ready: deque[TaskMessage]
    processing: deque[TaskMessage]

    def __init__(self) -> None:
        self.ready = deque()
        self.processing = deque()

    def enqueue(self, grant) -> None:
        self.ready.append(TaskMessage.from_grant(grant))

    def receive(self, *, timeout: int = 0) -> TaskMessage | None:
        del timeout
        if not self.ready:
            return None
        message = self.ready.popleft()
        self.processing.append(message)
        return message

    def acknowledge(self, message: TaskMessage) -> None:
        self.processing.remove(message)

    def requeue(self, message: TaskMessage) -> None:
        self.processing.remove(message)
        self.ready.appendleft(message)


def _pair(session: Session, suffix: int) -> tuple[SocialAccount, Wallet]:
    """Create one public-only synthetic account and wallet pair."""
    account = SocialAccount(
        handle=f"runner-account-{suffix}",
        normalized_handle=f"runner-account-{suffix}",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet = Wallet(
        address=f"0x{suffix:02x}" + "b" * 38,
        normalized_address=f"0x{suffix:02x}" + "b" * 38,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
    return account, wallet


def test_runner_drains_independent_workflow_batch() -> None:
    """The queue runner advances all pairs while preserving each dependency chain."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pairs = [_pair(session, index) for index in range(1, 4)]
        result = WorkflowService(session).create_batch(
            name="Runner smoke batch",
            dispatch_limit=2,
            items=[
                WorkflowBatchItem(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                    repost_target=f"tweet-{index}",
                )
                for index, (account, wallet) in enumerate(pairs, start=1)
            ],
        )
        scheduler = Scheduler(
            session,
            worker_concurrency=2,
            browser_concurrency=2,
            lease_ttl_seconds=60,
        )
        worker = TaskWorker(session, scheduler=scheduler)
        queue = MemoryQueue()

        def handler(job):
            if job.kind is TaskKind.BIND:
                binding = session.get(AccountWalletBinding, job.binding_id)
                assert binding is not None
                BindingService(session).confirm(binding.id, f"runner:{binding.id}")
                assert binding.state is BindingState.BOUND
            return WorkerOutcome(
                state=TaskState.SUCCEEDED,
                summary=f"synthetic {job.kind.value} complete",
            )

        runner = TaskRunner(
            scheduler=scheduler,
            worker=worker,
            queue=queue,
            handler=handler,
        )
        drained = runner.run_until_idle(
            dispatch_limit=2,
            max_jobs_per_cycle=2,
            max_cycles=20,
        )

        jobs = [job for chain in result.jobs for job in chain]
        assert drained.dispatched == 12
        assert drained.completed == 12
        assert drained.cycles < 20
        assert all(job.state is TaskState.SUCCEEDED for job in jobs)
        assert not queue.ready
        assert not queue.processing
        bindings = session.query(AccountWalletBinding).all()
        assert bindings
        assert all(binding.state is BindingState.BOUND for binding in bindings)
