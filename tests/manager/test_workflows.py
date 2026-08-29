from __future__ import annotations

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
from manager_api.models.tasks import TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.scheduler import Scheduler
from manager_api.services.bindings import BindingService
from manager_api.services.execution import TaskExecutionService
from manager_api.services.imports import AccountImportService
from manager_api.services.vault import VaultService
from manager_api.services.wallets import WalletService, WalletSourceType
from manager_api.services.workflows import (
    WorkflowBatchItem,
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


def test_workflow_builds_four_ordered_jobs_per_pair() -> None:
    """Each pair gets an isolated verify-bind-repost-claim dependency chain."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pairs = [_pair(session, index) for index in range(1, 4)]
        result = WorkflowService(session).create_batch(
            name="Synthetic HSK run",
            dispatch_limit=2,
            items=[
                WorkflowBatchItem(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                    repost_target=f"https://x.test/status/{index}",
                )
                for index, (account, wallet) in enumerate(pairs, start=1)
            ],
        )

        assert result.batch.workflow_type == "account_wallet"
        assert len(result.jobs) == 3
        for verify, bind, repost, claim in result.jobs:
            assert verify.kind is TaskKind.VERIFY_ACCOUNT
            assert bind.depends_on_task_id == verify.id
            assert repost.depends_on_task_id == bind.id
            assert claim.kind is TaskKind.CLAIM
            assert claim.depends_on_task_id == repost.id
            assert {verify.social_account_id, bind.social_account_id} == {repost.social_account_id}
            assert claim.social_account_id == repost.social_account_id
            assert verify.wallet_id is None
            assert bind.wallet_id == repost.wallet_id
            assert claim.wallet_id == repost.wallet_id


def test_workflow_dispatches_in_stages_and_keeps_pairs_independent() -> None:
    """One failed pair does not block unrelated pairs from reaching later stages."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pairs = [_pair(session, index) for index in range(1, 4)]
        result = WorkflowService(session).create_batch(
            name="Independent synthetic run",
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
        chains = result.jobs

        # Only the first stage is eligible initially, and two pairs can run together.
        grants = scheduler.dispatch_once(limit=2)
        assert {grant.task_job_id for grant in grants} == {
            chains[0][0].id,
            chains[1][0].id,
        }
        for grant in grants:
            worker.run_one(
                grant,
                lambda job: WorkerOutcome(
                    state=TaskState.FAILED if job.social_account_id == chains[1][0].social_account_id else TaskState.SUCCEEDED,
                    summary="synthetic verification",
                    failure_code="synthetic_invalid" if job.social_account_id == chains[1][0].social_account_id else None,
                ),
            )

        # The failed pair is explicitly blocked; the first and third pairs can advance independently.
        grants = scheduler.dispatch_once(limit=2)
        assert {grant.task_job_id for grant in grants} == {
            chains[0][1].id,
            chains[2][0].id,
        }
        for grant in grants:
            if grant.task_job_id == chains[0][1].id:
                worker.run_one(grant, lambda job: _complete_bind(session, job))
            else:
                worker.run_one(
                    grant,
                    lambda _: WorkerOutcome(
                        state=TaskState.SUCCEEDED,
                        summary="synthetic verification",
                    ),
                )

        # The first pair advances to repost while the third pair waits at bind.
        grant = scheduler.dispatch_once(limit=1)[0]
        assert grant.task_job_id == chains[0][2].id
        worker.run_one(
            grant,
            lambda _: WorkerOutcome(state=TaskState.SUCCEEDED, summary="synthetic repost"),
        )

        assert chains[0][2].state is TaskState.SUCCEEDED
        assert chains[0][3].state is TaskState.QUEUED
        assert chains[1][1].state is TaskState.BLOCKED
        assert chains[2][1].state is TaskState.QUEUED


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
        assert all(job.state is TaskState.QUEUED for job in (*verify.jobs, *bind.jobs, *repost.jobs, *claim.jobs))


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


def _complete_bind(session: Session, job) -> WorkerOutcome:
    """Confirm the synthetic binding when its bind task completes."""
    binding = session.get(AccountWalletBinding, job.binding_id)
    if binding is not None:
        BindingService(session).confirm(binding.id, f"synthetic:{binding.id}")
        assert binding.state is BindingState.BOUND
    return WorkerOutcome(state=TaskState.SUCCEEDED, summary="synthetic bind")


def test_generated_wallets_run_the_complete_workflow_through_the_vault() -> None:
    """生成多个本地地址并验证每个配对独立完成四阶段任务链。"""
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

        result = WorkflowService(session).create_batch(
            name="Generated local workflow",
            dispatch_limit=3,
            items=[
                WorkflowBatchItem(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                    repost_target=f"https://x.test/status/{index}",
                )
                for index, (account, wallet) in enumerate(zip(accounts, wallets), start=1)
            ],
        )
        all_jobs = [job for chain in result.jobs for job in chain]
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

        for _ in range(8):
            grants = scheduler.dispatch_once(limit=3)
            if not grants:
                break
            for grant in grants:
                worker.run_one(grant, execution.handle)
            if all(job.state is TaskState.SUCCEEDED for job in all_jobs):
                break

        assert all(job.state is TaskState.SUCCEEDED for job in all_jobs)
        assert x_adapter.verify_calls == 3
        assert x_adapter.repost_calls == 3
        assert kredo_adapter.bind_calls == 3
        assert kredo_adapter.status_calls == 3
        assert kredo_adapter.claim_calls == 3
        assert sorted(kredo_adapter.wallet_keys) == sorted(private_keys + private_keys)

        bindings = session.query(AccountWalletBinding).all()
        assert len(bindings) == 3
        assert all(binding.state is BindingState.BOUND for binding in bindings)
