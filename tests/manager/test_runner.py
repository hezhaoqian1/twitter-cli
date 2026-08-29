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
from manager_api.services.workflows import (
    WorkflowService,
    WorkflowStage,
    WorkflowStageBatchItem,
)
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


def test_runner_drains_independent_stage_batches() -> None:
    """Runner drains only the stage batches explicitly created by the operator."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pairs = [_pair(session, index) for index in range(1, 4)]
        workflows = WorkflowService(session)
        verify = workflows.create_stage_batch(
            name="Runner verify batch",
            stage=WorkflowStage.VERIFY,
            dispatch_limit=2,
            items=[
                WorkflowStageBatchItem(social_account_id=account.id)
                for account, _wallet in pairs
            ],
        )
        bind = workflows.create_stage_batch(
            name="Runner bind batch",
            stage=WorkflowStage.BIND,
            dispatch_limit=2,
            items=[
                WorkflowStageBatchItem(social_account_id=account.id, wallet_id=wallet.id)
                for account, wallet in pairs
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
        first_drain = runner.run_until_idle(
            dispatch_limit=2,
            max_jobs_per_cycle=2,
            max_cycles=20,
        )
        bindings = session.query(AccountWalletBinding).order_by(AccountWalletBinding.id).all()
        assert all(binding.state is BindingState.BOUND for binding in bindings)

        repost = workflows.create_stage_batch(
            name="Runner repost batch",
            stage=WorkflowStage.REPOST,
            dispatch_limit=2,
            items=[
                WorkflowStageBatchItem(binding_id=binding.id, external_target=f"tweet-{index}")
                for index, binding in enumerate(bindings, start=1)
            ],
        )
        second_drain = runner.run_until_idle(
            dispatch_limit=2,
            max_jobs_per_cycle=2,
            max_cycles=20,
        )
        claim = workflows.create_stage_batch(
            name="Runner claim batch",
            stage=WorkflowStage.CLAIM,
            dispatch_limit=2,
            items=[WorkflowStageBatchItem(binding_id=binding.id) for binding in bindings],
        )
        third_drain = runner.run_until_idle(
            dispatch_limit=2,
            max_jobs_per_cycle=2,
            max_cycles=20,
        )

        jobs = [*verify.jobs, *bind.jobs, *repost.jobs, *claim.jobs]
        assert first_drain.dispatched == 6
        assert first_drain.completed == 6
        assert second_drain.dispatched == 3
        assert second_drain.completed == 3
        assert third_drain.dispatched == 3
        assert third_drain.completed == 3
        assert all(job.state is TaskState.SUCCEEDED for job in jobs)
        assert not queue.ready
        assert not queue.processing
        assert bindings
        assert all(binding.state is BindingState.BOUND for binding in bindings)
