from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.adapters.protocol import (
    AccountHealthResult,
    AdapterEvidence,
    ExternalObservation,
    ExternalOperation,
    ExternalStatus,
)
from manager_api.db.base import Base
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskBatch, TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.scheduler import Scheduler
from manager_api.services.bindings import BindingService
from manager_api.services.execution import TaskExecutionService
from manager_api.services.imports import AccountImportService
from manager_api.services.tasks import TaskConflictError
from manager_api.services.vault import VaultService
from manager_api.services.wallets import WalletService, WalletSourceType
from manager_api.services.workflows import (
    WorkflowService,
    WorkflowStage,
    WorkflowStageBatchItem,
)
from manager_api.worker import TaskWorker, WorkerOutcome


def _pair(session: Session, suffix: int) -> tuple[SocialAccount, Wallet]:
    """Create one synthetic account-wallet pair without secret material."""
    account = SocialAccount(
        handle=f"fixture-account-{suffix}",
        normalized_handle=f"fixture-account-{suffix}",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet = Wallet(
        address=f"0x{suffix:02x}" + "a" * 38,
        normalized_address=f"0x{suffix:02x}" + "a" * 38,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
    return account, wallet


class SyntheticWorkflowXAdapter:
    """本地合成 X 适配器，只记录公开账号和调用次数。"""

    def __init__(self) -> None:
        self.verify_calls = 0
        self.repost_calls = 0

    def verify_account(self, account):
        self.verify_calls += 1
        return AccountHealthResult(
            health=AccountHealth.HEALTHY,
            handle=account.handle,
            user_id=f"synthetic-user-{self.verify_calls}",
            evidence=AdapterEvidence("account_verified", "synthetic account verified"),
        )

    def repost(self, account, operation):
        self.repost_calls += 1
        return ExternalOperation(
            operation_ref=f"synthetic-repost-{self.repost_calls}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("reposted", "synthetic repost complete"),
        )


class SyntheticWorkflowKredoAdapter:
    """本地合成 Kredo 适配器，验证每次调用拿到的是当前地址私钥。"""

    def __init__(self) -> None:
        self.bind_calls = 0
        self.status_calls = 0
        self.claim_calls = 0
        self.wallet_keys: list[str] = []

    def bind(self, account, wallet, operation):
        self.bind_calls += 1
        self.wallet_keys.append(wallet.private_key)
        return ExternalOperation(
            operation_ref=f"synthetic-bind-{self.bind_calls}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("bound", "synthetic binding complete"),
        )

    def status(self, operation, account=None, wallet=None):
        del account, wallet
        self.status_calls += 1
        return ExternalObservation(
            operation_ref=f"synthetic-status-{self.status_calls}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("repost_verified", "synthetic repost verified"),
        )

    def claim(self, account, wallet, operation):
        self.claim_calls += 1
        self.wallet_keys.append(wallet.private_key)
        return ExternalOperation(
            operation_ref=f"synthetic-claim-{self.claim_calls}",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("claimed", "synthetic claim complete"),
        )

    def account_summary(self, account, wallet, operation):
        """满足执行服务契约；本测试的主链不触发余额同步。"""
        return None


def test_stage_batches_are_homogeneous_and_never_add_cross_stage_dependencies() -> None:
    """Each operator stage stays independently schedulable and observable."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pairs = [_pair(session, index) for index in range(11, 14)]
        service = WorkflowService(session)

        verify = service.create_stage_batch(
            name="Verify three accounts",
            stage=WorkflowStage.VERIFY,
            dispatch_limit=2,
            items=[
                WorkflowStageBatchItem(social_account_id=account.id, external_target="x:verify")
                for account, _ in pairs
            ],
        )
        assert verify.batch.workflow_type == "stage:verify"
        assert {job.kind for job in verify.jobs} == {TaskKind.VERIFY_ACCOUNT}
        assert all(job.depends_on_task_id is None for job in verify.jobs)

        bind = service.create_stage_batch(
            name="Bind three pairs",
            stage=WorkflowStage.BIND,
            dispatch_limit=2,
            items=[
                WorkflowStageBatchItem(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                    external_target="kredo:bind",
                )
                for account, wallet in pairs
            ],
        )
        assert bind.batch.workflow_type == "stage:bind"
        assert {job.kind for job in bind.jobs} == {TaskKind.BIND}
        assert all(job.depends_on_task_id is None for job in bind.jobs)
        assert all(job.binding_id is not None for job in bind.jobs)

        for job in bind.jobs:
            assert job.binding_id is not None
            BindingService(session).confirm(job.binding_id, f"fixture:{job.binding_id}")

        repost = service.create_stage_batch(
            name="Repost verified pairs",
            stage=WorkflowStage.REPOST,
            dispatch_limit=2,
            items=[
                WorkflowStageBatchItem(
                    binding_id=job.binding_id,
                    external_target=f"tweet-{index}",
                )
                for index, job in enumerate(bind.jobs, start=1)
            ],
        )
        for job in repost.jobs:
            job.state = TaskState.SUCCEEDED
        session.flush()

        claim = service.create_stage_batch(
            name="Claim verified pairs",
            stage=WorkflowStage.CLAIM,
            dispatch_limit=2,
            items=[
                WorkflowStageBatchItem(binding_id=job.binding_id, external_target="kredo:claim")
                for job in bind.jobs
            ],
        )

        assert repost.batch.workflow_type == "stage:repost"
        assert claim.batch.workflow_type == "stage:claim"
        assert all(job.depends_on_task_id is None for job in (*repost.jobs, *claim.jobs))
        assert all(job.state is TaskState.QUEUED for job in (*verify.jobs, *bind.jobs, *claim.jobs))
        assert all(job.state is TaskState.SUCCEEDED for job in repost.jobs)


def test_claim_stage_requires_successful_repost_validation() -> None:
    """领取批次必须等转发任务成功，避免慢回写时提前领取。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        account, wallet = _pair(session, 31)
        binding = BindingService(session).confirm(
            BindingService(session).create_pending(account.id, wallet.id).binding.id,
            "fixture:bound",
        ).binding
        service = WorkflowService(session)

        with pytest.raises(TaskConflictError) as not_ready:
            service.create_stage_batch(
                name="early claim",
                stage=WorkflowStage.CLAIM,
                items=[WorkflowStageBatchItem(binding_id=binding.id, external_target="kredo:claim")],
            )
        assert not_ready.value.code == "claim_not_ready"
        assert session.query(TaskBatch).count() == 0

        repost = service.create_stage_batch(
            name="repost first",
            stage=WorkflowStage.REPOST,
            items=[WorkflowStageBatchItem(binding_id=binding.id, external_target="tweet-31")],
        )
        repost.jobs[0].state = TaskState.WAITING_EXTERNAL_VALIDATION
        session.flush()
        with pytest.raises(TaskConflictError) as still_waiting:
            service.create_stage_batch(
                name="waiting claim",
                stage=WorkflowStage.CLAIM,
                items=[WorkflowStageBatchItem(binding_id=binding.id, external_target="kredo:claim")],
            )
        assert still_waiting.value.code == "claim_not_ready"

        repost.jobs[0].state = TaskState.SUCCEEDED
        session.flush()
        claim = service.create_stage_batch(
            name="ready claim",
            stage=WorkflowStage.CLAIM,
            items=[WorkflowStageBatchItem(binding_id=binding.id, external_target="kredo:claim")],
        )
        assert len(claim.jobs) == 1
        with pytest.raises(TaskConflictError) as duplicate:
            service.create_stage_batch(
                name="duplicate claim",
                stage=WorkflowStage.CLAIM,
                items=[WorkflowStageBatchItem(binding_id=binding.id, external_target="kredo:claim")],
            )
        assert duplicate.value.code == "claim_already_exists"


def test_failed_or_waiting_stage_jobs_do_not_block_independent_batches() -> None:
    """A delayed provider response only holds its own resources and batch slot."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        accounts = [_pair(session, index)[0] for index in range(21, 24)]
        service = WorkflowService(session)
        first = service.create_stage_batch(
            name="First verification stage",
            stage=WorkflowStage.VERIFY,
            dispatch_limit=1,
            items=[WorkflowStageBatchItem(social_account_id=account.id) for account in accounts[:2]],
        )
        second = service.create_stage_batch(
            name="Second verification stage",
            stage=WorkflowStage.VERIFY,
            dispatch_limit=1,
            items=[WorkflowStageBatchItem(social_account_id=accounts[2].id)],
        )
        scheduler = Scheduler(
            session,
            worker_concurrency=2,
            browser_concurrency=2,
            lease_ttl_seconds=60,
        )
        worker = TaskWorker(session, scheduler=scheduler)

        grants = scheduler.dispatch_once(limit=2)
        assert {grant.task_job_id for grant in grants} == {first.jobs[0].id, second.jobs[0].id}
        for grant in grants:
            worker.run_one(
                grant,
                lambda job: WorkerOutcome(
                    state=TaskState.FAILED if job.id == first.jobs[0].id else TaskState.SUCCEEDED,
                    summary="synthetic stage outcome",
                    failure_code="synthetic_invalid" if job.id == first.jobs[0].id else None,
                ),
            )

        next_grant = scheduler.dispatch_once(limit=1)
        assert [grant.task_job_id for grant in next_grant] == [first.jobs[1].id]
        assert second.jobs[0].state is TaskState.SUCCEEDED


def test_generated_wallets_run_explicit_stage_batches_through_the_vault() -> None:
    """生成多个本地地址，并验证每个阶段都由显式批次独立推进。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        vault = VaultService(session)
        vault.initialize("synthetic-workflow-vault-password")

        account_rows = "\n".join(
            (
                f"workflow-account-{index}\tfixture-password-{index}\t"
                f"fixture-totp-{index}\tworkflow-{index}@example.test\t"
                f"fixture-email-password-{index}\tfixture-token-{index}\t"
                f"auth_token=fixture-token-{index}; ct0=fixture-csrf-{index}"
            )
            for index in range(1, 4)
        )
        AccountImportService(session, vault).commit(account_rows)
        accounts = session.query(SocialAccount).order_by(SocialAccount.handle).all()

        wallets = []
        private_keys = []
        for index in range(1, 4):
            private_key = f"{index + 16:02x}" * 32
            private_keys.append(private_key)
            _, preview = WalletService(session, vault).commit(
                WalletSourceType.PRIVATE_KEY,
                private_key,
                label=f"synthetic-wallet-{index}",
            )
            wallet_id = next(
                decision.wallet_id
                for decision in preview.decisions
                if decision.wallet_id is not None
            )
            wallet = session.get(Wallet, wallet_id)
            assert wallet is not None
            wallets.append(wallet)

        workflows = WorkflowService(session)
        verify = workflows.create_stage_batch(
            name="Generated verify stage",
            stage=WorkflowStage.VERIFY,
            dispatch_limit=3,
            items=[
                WorkflowStageBatchItem(social_account_id=account.id)
                for account in accounts
            ],
        )
        scheduler = Scheduler(
            session,
            worker_concurrency=3,
            browser_concurrency=3,
            lease_ttl_seconds=60,
        )
        worker = TaskWorker(session, scheduler=scheduler)
        x_adapter = SyntheticWorkflowXAdapter()
        kredo_adapter = SyntheticWorkflowKredoAdapter()
        execution = TaskExecutionService(
            session,
            vault=vault,
            x_adapter=x_adapter,
            kredo_adapter=kredo_adapter,
        )

        # 验证阶段独立完成后，并不会自动创建绑定任务。
        for _ in range(3):
            grants = scheduler.dispatch_once(limit=3)
            if not grants:
                break
            for grant in grants:
                worker.run_one(grant, execution.handle)
            if all(job.state is TaskState.SUCCEEDED for job in verify.jobs):
                break
        assert all(job.state is TaskState.SUCCEEDED for job in verify.jobs)
        assert session.query(AccountWalletBinding).count() == 0

        bind = workflows.create_stage_batch(
            name="Generated bind stage",
            stage=WorkflowStage.BIND,
            dispatch_limit=3,
            items=[
                WorkflowStageBatchItem(social_account_id=account.id, wallet_id=wallet.id)
                for account, wallet in zip(accounts, wallets, strict=False)
            ],
        )
        for _ in range(3):
            grants = scheduler.dispatch_once(limit=3)
            if not grants:
                break
            for grant in grants:
                worker.run_one(grant, execution.handle)
            if all(job.state is TaskState.SUCCEEDED for job in bind.jobs):
                break
        bindings = session.query(AccountWalletBinding).order_by(AccountWalletBinding.id).all()
        assert len(bindings) == 3
        assert all(binding.state is BindingState.BOUND for binding in bindings)

        repost = workflows.create_stage_batch(
            name="Generated repost stage",
            stage=WorkflowStage.REPOST,
            dispatch_limit=3,
            items=[
                WorkflowStageBatchItem(
                    binding_id=binding.id,
                    external_target=f"https://x.test/status/{index}",
                )
                for index, binding in enumerate(bindings, start=1)
            ],
        )
        for _ in range(3):
            grants = scheduler.dispatch_once(limit=3)
            if not grants:
                break
            for grant in grants:
                worker.run_one(grant, execution.handle)
            if all(job.state is TaskState.SUCCEEDED for job in repost.jobs):
                break

        claim = workflows.create_stage_batch(
            name="Generated claim stage",
            stage=WorkflowStage.CLAIM,
            dispatch_limit=3,
            items=[WorkflowStageBatchItem(binding_id=binding.id) for binding in bindings],
        )
        for _ in range(3):
            grants = scheduler.dispatch_once(limit=3)
            if not grants:
                break
            for grant in grants:
                worker.run_one(grant, execution.handle)
            if all(job.state is TaskState.SUCCEEDED for job in claim.jobs):
                break

        all_jobs = [*verify.jobs, *bind.jobs, *repost.jobs, *claim.jobs]
        assert all(job.state is TaskState.SUCCEEDED for job in all_jobs)
        assert all(job.depends_on_task_id is None for job in all_jobs)
        assert x_adapter.verify_calls == 3
        assert x_adapter.repost_calls == 3
        assert kredo_adapter.bind_calls == 3
        assert kredo_adapter.status_calls == 3
        assert kredo_adapter.claim_calls == 3
        assert sorted(kredo_adapter.wallet_keys) == sorted(private_keys + private_keys)

        assert all(binding.state is BindingState.BOUND for binding in bindings)
