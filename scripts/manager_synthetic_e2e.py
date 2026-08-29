#!/usr/bin/env python3
"""Run local four-pair stage batches through the production runner and vault."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from manager_api.adapters.protocol import (
    AccountHealthResult,
    AdapterEvidence,
    ExternalObservation,
    ExternalOperation,
    ExternalStatus,
)
from manager_api.db.base import Base
from manager_api.models.accounts import AccountHealth, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet, WalletSourceType
from manager_api.queue import TaskMessage
from manager_api.runner import TaskRunner
from manager_api.scheduler import Scheduler
from manager_api.services.imports import AccountImportService
from manager_api.services.tasks import TaskService
from manager_api.services.vault import VaultService
from manager_api.services.wallets import WalletService
from manager_api.services.execution import ExecutionConfig, TaskExecutionService
from manager_api.worker import TaskWorker
from manager_api.services.workflows import (
    WorkflowService,
    WorkflowStage,
    WorkflowStageBatchItem,
)


class MemoryQueue:
    """Use the same reliable-list contract without requiring Redis."""

    def __init__(self) -> None:
        self.ready: deque[TaskMessage] = deque()
        self.processing: deque[TaskMessage] = deque()

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


@dataclass
class SyntheticXAdapter:
    """Record account calls while simulating successful X session and repost."""

    verify_calls: Counter[str] = field(default_factory=Counter)
    repost_calls: Counter[str] = field(default_factory=Counter)

    def verify_account(self, account):
        self.verify_calls[account.handle] += 1
        return AccountHealthResult(
            health=AccountHealth.HEALTHY,
            handle=account.handle,
            user_id=f"synthetic-user:{account.handle}",
            evidence=AdapterEvidence("account_verified", "synthetic account verified"),
        )

    def repost(self, account, operation):
        del operation
        self.repost_calls[account.handle] += 1
        return ExternalOperation(
            operation_ref=f"synthetic-repost:{account.handle}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("repost_submitted", "synthetic repost submitted"),
        )


@dataclass
class SyntheticKredoAdapter:
    """Make binding immediate and make the first repost check pending."""

    bind_calls: Counter[str] = field(default_factory=Counter)
    status_calls: Counter[str] = field(default_factory=Counter)
    claim_calls: Counter[str] = field(default_factory=Counter)

    def bind(self, account, wallet, operation):
        del operation
        self.bind_calls[account.handle] += 1
        return ExternalOperation(
            operation_ref=f"synthetic-binding:{wallet.address}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("bound", "synthetic binding complete"),
        )

    def status(self, operation, account=None, wallet=None):
        del account, wallet
        binding_id = str(operation.metadata["binding_id"])
        self.status_calls[binding_id] += 1
        status = (
            ExternalStatus.PENDING
            if self.status_calls[binding_id] == 1
            else ExternalStatus.SUCCEEDED
        )
        return ExternalObservation(
            operation_ref=f"synthetic-repost:{binding_id}",
            status=status,
            evidence=AdapterEvidence(
                "repost_pending" if status is ExternalStatus.PENDING else "repost_verified",
                "synthetic repost status",
            ),
        )

    def claim(self, account, wallet, operation):
        del operation
        self.claim_calls[account.handle] += 1
        return ExternalOperation(
            operation_ref=f"synthetic-claim:{wallet.address}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("claimed", "synthetic claim complete"),
        )


def _seed(session: Session) -> tuple[VaultService, list[tuple[SocialAccount, Wallet]]]:
    """Create four encrypted account records and four independent wallets."""
    vault = VaultService(session)
    vault.initialize("synthetic-vault-password")
    rows = "\n".join(
        "\t".join(
            [
                f"synthetic-account-{index}",
                f"synthetic-password-{index}",
                "JBSWY3DPEHPK3PXP",
                f"account-{index}@example.test",
                f"synthetic-email-password-{index}",
                f"synthetic-token-{index}",
                f"auth_token=synthetic-token-{index}; ct0=synthetic-csrf-{index}",
            ]
        )
        for index in range(1, 5)
    )
    AccountImportService(session, vault).commit(rows, source_name="synthetic-e2e.tsv")

    accounts = session.scalars(
        select(SocialAccount).order_by(SocialAccount.handle)
    ).all()
    pairs: list[tuple[SocialAccount, Wallet]] = []
    for index, account in enumerate(accounts, start=1):
        _, preview = WalletService(session, vault).commit(
            WalletSourceType.PRIVATE_KEY,
            f"{index:064x}",
            label=f"synthetic-wallet-{index}",
        )
        wallet_id = next(
            decision.wallet_id
            for decision in preview.decisions
            if decision.wallet_id is not None
        )
        wallet = session.get(Wallet, wallet_id)
        if wallet is None:
            raise RuntimeError("synthetic wallet was not created")
        pairs.append((account, wallet))
    session.commit()
    return vault, pairs


def _run() -> dict[str, object]:
    """Drive the durable queue until delayed repost checks and claims finish."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        vault, pairs = _seed(session)
        workflows = WorkflowService(session)
        verify = workflows.create_stage_batch(
            name="Synthetic verify stage",
            stage=WorkflowStage.VERIFY,
            items=[
                WorkflowStageBatchItem(social_account_id=account.id, external_target="x:verify")
                for account, _ in pairs
            ],
            dispatch_limit=4,
        )
        bind = workflows.create_stage_batch(
            name="Synthetic bind stage",
            stage=WorkflowStage.BIND,
            items=[
                WorkflowStageBatchItem(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                    external_target="kredo:bind",
                )
                for account, wallet in pairs
            ],
            dispatch_limit=4,
        )
        session.commit()
        x_adapter = SyntheticXAdapter()
        kredo_adapter = SyntheticKredoAdapter()
        execution = TaskExecutionService(
            session,
            vault=vault,
            x_adapter=x_adapter,
            kredo_adapter=kredo_adapter,
            # 合成验收使用立即到期的轮询窗口，避免测试依赖真实睡眠。
            config=ExecutionConfig(poll_delay_seconds=0),
        )
        scheduler = Scheduler(
            session,
            lease_ttl_seconds=60,
            worker_concurrency=4,
            browser_concurrency=4,
        )
        runner = TaskRunner(
            scheduler=scheduler,
            worker=TaskWorker(session, scheduler=scheduler),
            queue=MemoryQueue(),
            handler=execution.handle,
        )

        waiting_reposts: set[str] = set()
        stage_created = {"verify": verify.batch.id, "bind": bind.batch.id}
        repost_created = False
        claim_created = False
        cycles = 0
        while True:
            cycles += 1
            runner.dispatch(limit=4)
            while runner.run_one(timeout=0) is not None:
                pass
            session.commit()

            waiting = session.scalars(
                select(TaskJob).where(
                    TaskJob.state == TaskState.WAITING_EXTERNAL_VALIDATION
                )
            ).all()
            for job in waiting:
                waiting_reposts.add(str(job.id))
            TaskService(session).requeue_due_polls()
            session.commit()

            bound = session.scalars(
                select(AccountWalletBinding).where(AccountWalletBinding.state == BindingState.BOUND)
            ).all()
            if len(bound) == len(pairs) and not repost_created:
                repost = workflows.create_stage_batch(
                    name="Synthetic repost stage",
                    stage=WorkflowStage.REPOST,
                    items=[
                        WorkflowStageBatchItem(
                            binding_id=binding.id,
                            external_target=f"https://x.test/status/synthetic-{index}",
                        )
                        for index, binding in enumerate(bound, start=1)
                    ],
                    dispatch_limit=4,
                )
                stage_created["repost"] = repost.batch.id
                repost_created = True
                session.commit()

            repost_jobs = session.scalars(
                select(TaskJob).where(TaskJob.kind == TaskKind.REPOST)
            ).all()
            if (
                repost_created
                and not claim_created
                and len(repost_jobs) == len(pairs)
                and all(job.state is TaskState.SUCCEEDED for job in repost_jobs)
            ):
                bindings = session.scalars(
                    select(AccountWalletBinding).where(AccountWalletBinding.state == BindingState.BOUND)
                ).all()
                claim = workflows.create_stage_batch(
                    name="Synthetic claim stage",
                    stage=WorkflowStage.CLAIM,
                    items=[
                        WorkflowStageBatchItem(binding_id=binding.id, external_target="kredo:claim")
                        for binding in bindings
                    ],
                    dispatch_limit=4,
                )
                stage_created["claim"] = claim.batch.id
                claim_created = True
                session.commit()

            jobs = session.scalars(select(TaskJob)).all()
            if claim_created and all(job.state is TaskState.SUCCEEDED for job in jobs):
                break
            if cycles > 40:
                raise RuntimeError("synthetic stage workflow did not become idle")

        jobs = session.scalars(select(TaskJob)).all()
        bindings = session.scalars(select(AccountWalletBinding)).all()
        accounts = session.scalars(select(SocialAccount)).all()
        queue = runner.queue
        assert isinstance(queue, MemoryQueue)
        assert len(jobs) == 16
        assert Counter(job.kind for job in jobs) == Counter(
            {
                TaskKind.VERIFY_ACCOUNT: 4,
                TaskKind.BIND: 4,
                TaskKind.REPOST: 4,
                TaskKind.CLAIM: 4,
            }
        )
        assert all(job.state is TaskState.SUCCEEDED for job in jobs)
        assert all(binding.state is BindingState.BOUND for binding in bindings)
        assert all(account.health is AccountHealth.HEALTHY for account in accounts)
        assert all(job.depends_on_task_id is None for job in jobs)
        assert len(waiting_reposts) == 4
        assert not queue.ready and not queue.processing
        assert all(value == 1 for value in x_adapter.repost_calls.values())
        assert all(value == 1 for value in kredo_adapter.claim_calls.values())
        assert all(value == 1 for value in kredo_adapter.bind_calls.values())
        assert all(value == 1 for value in x_adapter.verify_calls.values())
        assert all(value == 2 for value in kredo_adapter.status_calls.values())
        return {
            "cycles": cycles,
            "stage_batches": {name: str(batch_id) for name, batch_id in stage_created.items()},
            "jobs": len(jobs),
            "bindings_bound": len(bindings),
            "delayed_reposts_polled": len(waiting_reposts),
            "repost_calls": sum(x_adapter.repost_calls.values()),
            "claim_calls": sum(kredo_adapter.claim_calls.values()),
            "queue_ready": len(queue.ready),
            "queue_processing": len(queue.processing),
        }


if __name__ == "__main__":
    print(_run())
