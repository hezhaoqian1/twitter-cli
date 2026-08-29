from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.adapters.protocol import (
    AccountHealthResult,
    AdapterEvidence,
    ExternalObservation,
    ExternalOperation,
    ExternalStatus,
    OperationMaterial,
)
from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import WalletSourceType
from manager_api.scheduler import Scheduler
from manager_api.services.execution import ExecutionConfig, TaskExecutionService
from manager_api.services.imports import AccountImportService
from manager_api.services.vault import VaultService
from manager_api.services.wallets import WalletService
from manager_api.services.workflows import (
    WorkflowService,
    WorkflowStage,
    WorkflowStageBatchItem,
)
from manager_api.worker import TaskWorker


@dataclass
class FakeXAdapter:
    """合成 X Provider，记录调用但不连接外部服务。"""

    repost_status: ExternalStatus = ExternalStatus.SUCCEEDED
    verify_calls: int = 0
    repost_calls: int = 0

    def verify_account(self, account):
        self.verify_calls += 1
        return AccountHealthResult(
            health=AccountHealth.HEALTHY,
            handle=account.handle,
            user_id="synthetic-user",
            evidence=AdapterEvidence("account_verified", "synthetic account verified"),
        )

    def repost(self, account, operation: OperationMaterial):
        self.repost_calls += 1
        return ExternalOperation(
            operation_ref="synthetic-tweet",
            status=self.repost_status,
            evidence=AdapterEvidence("reposted", "synthetic repost complete"),
        )


@dataclass
class FakeKredoAdapter:
    """合成 Kredo Provider，验证绑定与延迟状态映射。"""

    bind_status: ExternalStatus = ExternalStatus.SUCCEEDED
    status_value: ExternalStatus = ExternalStatus.SUCCEEDED
    bind_calls: int = 0
    claim_calls: int = 0
    status_calls: int = 0

    def bind(self, account, wallet, operation):
        self.bind_calls += 1
        return ExternalOperation(
            operation_ref="synthetic-binding",
            status=self.bind_status,
            evidence=AdapterEvidence("bound", "synthetic binding complete"),
        )

    def claim(self, account, wallet, operation):
        self.claim_calls += 1
        return ExternalOperation(
            operation_ref="synthetic-claim",
            status=ExternalStatus.SUCCEEDED,
            evidence=AdapterEvidence("claimed", "synthetic claim complete"),
        )

    def status(self, operation, account=None, wallet=None):
        del account, wallet
        self.status_calls += 1
        return ExternalObservation(
            operation_ref="synthetic-repost",
            status=self.status_value,
            evidence=AdapterEvidence("status", "synthetic Kredo status"),
        )


def _setup_pair(session: Session):
    """创建一个带加密账号和钱包秘密的合成工作流输入。"""
    vault = VaultService(session)
    vault.initialize("fixture-vault-password")
    account_content = (
        "fixture-account\tfixture-password\tJBSWY3DPEHPK3PXP\t"
        "fixture@example.test\tfixture-mail-password\tfixture-token\t"
        "auth_token=fixture-token; ct0=fixture-csrf"
    )
    AccountImportService(session, vault).commit(account_content)
    # 从已提交账号中取公开身份，避免测试依赖 secret 字段查询。
    from manager_api.models.accounts import SocialAccount

    account = session.query(SocialAccount).filter_by(handle="fixture-account").one()
    _, wallet_preview = WalletService(session, vault).commit(
        WalletSourceType.PRIVATE_KEY,
        "11" * 32,
        label="synthetic-wallet",
    )
    wallet_id = next(
        decision.wallet_id
        for decision in wallet_preview.decisions
        if decision.wallet_id is not None
    )
    from manager_api.models.wallets import Wallet

    wallet = session.get(Wallet, wallet_id)
    assert wallet is not None
    return vault, account, wallet


def test_execution_service_runs_each_encrypted_stage_when_explicitly_created() -> None:
    """每个阶段都要显式建批次，执行层只处理当前租约任务。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        vault, account, wallet = _setup_pair(session)
        workflows = WorkflowService(session)
        x_adapter = FakeXAdapter()
        kredo_adapter = FakeKredoAdapter()
        execution = TaskExecutionService(
            session,
            vault=vault,
            x_adapter=x_adapter,
            kredo_adapter=kredo_adapter,
            config=ExecutionConfig(poll_delay_seconds=1),
        )
        scheduler = Scheduler(session, worker_concurrency=1, browser_concurrency=1)
        worker = TaskWorker(session, scheduler=scheduler)

        workflows.create_stage_batch(
            name="synthetic verify",
            stage=WorkflowStage.VERIFY,
            items=[WorkflowStageBatchItem(social_account_id=account.id)],
        )
        verify_grant = scheduler.dispatch_once(limit=1)[0]
        verify = worker.run_one(verify_grant, execution.handle)
        assert verify.state is TaskState.SUCCEEDED
        assert account.health is AccountHealth.HEALTHY

        bind_batch = workflows.create_stage_batch(
            name="synthetic bind",
            stage=WorkflowStage.BIND,
            items=[
                WorkflowStageBatchItem(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                )
            ],
        )
        bind_grant = scheduler.dispatch_once(limit=1)[0]
        bind = worker.run_one(bind_grant, execution.handle)
        assert bind.state is TaskState.SUCCEEDED
        binding = session.get(AccountWalletBinding, bind_batch.jobs[0].binding_id)
        assert binding is not None
        assert binding.state is BindingState.BOUND

        workflows.create_stage_batch(
            name="synthetic repost",
            stage=WorkflowStage.REPOST,
            items=[
                WorkflowStageBatchItem(
                    binding_id=binding.id,
                    external_target="https://x.test/status/123",
                )
            ],
        )
        repost_grant = scheduler.dispatch_once(limit=1)[0]
        repost = worker.run_one(repost_grant, execution.handle)
        assert repost.state is TaskState.SUCCEEDED

        workflows.create_stage_batch(
            name="synthetic claim",
            stage=WorkflowStage.CLAIM,
            items=[WorkflowStageBatchItem(binding_id=binding.id)],
        )
        claim_grant = scheduler.dispatch_once(limit=1)[0]
        claim = worker.run_one(claim_grant, execution.handle)
        assert claim.state is TaskState.SUCCEEDED
        assert x_adapter.verify_calls == 1
        assert x_adapter.repost_calls == 1
        assert kredo_adapter.bind_calls == 1
        assert kredo_adapter.claim_calls == 1
        assert kredo_adapter.status_calls == 1


def test_execution_service_never_creates_later_stage_jobs() -> None:
    """执行单个任务只更新当前任务结果，不自动插入后续阶段。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        vault, account, wallet = _setup_pair(session)
        workflows = WorkflowService(session)
        x_adapter = FakeXAdapter()
        kredo_adapter = FakeKredoAdapter()
        execution = TaskExecutionService(
            session,
            vault=vault,
            x_adapter=x_adapter,
            kredo_adapter=kredo_adapter,
        )
        scheduler = Scheduler(session, worker_concurrency=1, browser_concurrency=1)
        worker = TaskWorker(session, scheduler=scheduler)

        workflows.create_stage_batch(
            name="verify only",
            stage=WorkflowStage.VERIFY,
            items=[WorkflowStageBatchItem(social_account_id=account.id)],
        )
        verify = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)
        assert verify.state is TaskState.SUCCEEDED
        assert session.query(AccountWalletBinding).count() == 0
        assert session.query(TaskJob).count() == 1

        bind_batch = workflows.create_stage_batch(
            name="bind only",
            stage=WorkflowStage.BIND,
            items=[WorkflowStageBatchItem(social_account_id=account.id, wallet_id=wallet.id)],
        )
        bind = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)
        assert bind.state is TaskState.SUCCEEDED
        assert bind_batch.jobs[0].kind is TaskKind.BIND
        assert session.query(AccountWalletBinding).count() == 1
        assert session.query(TaskJob).filter(TaskJob.kind == TaskKind.REPOST).count() == 0
        assert session.query(TaskJob).filter(TaskJob.kind == TaskKind.CLAIM).count() == 0


def test_execution_service_preserves_delayed_external_state_for_polling() -> None:
    """外部延迟状态进入等待态，并生成未来轮询时间而不是重复动作。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        vault, account, wallet = _setup_pair(session)
        workflows = WorkflowService(session)
        bind_batch = workflows.create_stage_batch(
            name="synthetic bind first",
            stage=WorkflowStage.BIND,
            items=[WorkflowStageBatchItem(social_account_id=account.id, wallet_id=wallet.id)],
        )
        x_adapter = FakeXAdapter()
        kredo_adapter = FakeKredoAdapter(status_value=ExternalStatus.PENDING)
        execution = TaskExecutionService(
            session,
            vault=vault,
            x_adapter=x_adapter,
            kredo_adapter=kredo_adapter,
            config=ExecutionConfig(poll_delay_seconds=30),
        )
        scheduler = Scheduler(session, worker_concurrency=1, browser_concurrency=1)
        worker = TaskWorker(session, scheduler=scheduler)

        bind = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)
        assert bind.state is TaskState.SUCCEEDED
        binding_id = bind_batch.jobs[0].binding_id
        assert binding_id is not None
        workflows.create_stage_batch(
            name="synthetic delayed repost",
            stage=WorkflowStage.REPOST,
            items=[
                WorkflowStageBatchItem(
                    binding_id=binding_id,
                    external_target="https://x.test/status/456",
                )
            ],
        )
        repost = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)

        assert repost.state is TaskState.WAITING_EXTERNAL_VALIDATION
        assert repost.next_poll_at is not None
        assert repost.next_poll_at > repost.started_at
        assert x_adapter.repost_calls == 1
        assert kredo_adapter.status_calls == 1

        # 到达 next_poll_at 后由调度器自动回队，再验证只读路径。
        repost.next_poll_at = utc_now() - timedelta(seconds=1)
        session.flush()
        polled = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)
        assert polled.state is TaskState.WAITING_EXTERNAL_VALIDATION
        assert x_adapter.repost_calls == 1
        assert kredo_adapter.status_calls == 2


def test_execution_service_polls_delayed_bind_without_reclicking() -> None:
    """绑定进入等待态后，后续轮询只读 Kredo 状态，不重复触发 OAuth。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        vault, account, wallet = _setup_pair(session)
        result = WorkflowService(session).create_stage_batch(
            name="synthetic delayed bind",
            stage=WorkflowStage.BIND,
            items=[
                WorkflowStageBatchItem(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                )
            ],
        )
        x_adapter = FakeXAdapter()
        kredo_adapter = FakeKredoAdapter(
            bind_status=ExternalStatus.PENDING,
            status_value=ExternalStatus.SUCCEEDED,
        )
        execution = TaskExecutionService(
            session,
            vault=vault,
            x_adapter=x_adapter,
            kredo_adapter=kredo_adapter,
            config=ExecutionConfig(poll_delay_seconds=0),
        )
        scheduler = Scheduler(session, worker_concurrency=1, browser_concurrency=1)
        worker = TaskWorker(session, scheduler=scheduler)

        bind = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)
        assert bind.state is TaskState.WAITING_EXTERNAL_VALIDATION
        assert kredo_adapter.bind_calls == 1

        polled = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)
        binding = session.get(AccountWalletBinding, result.jobs[0].binding_id)
        assert polled.state is TaskState.SUCCEEDED
        assert binding is not None
        assert binding.state is BindingState.BOUND
        assert kredo_adapter.bind_calls == 1
        assert kredo_adapter.status_calls == 1
