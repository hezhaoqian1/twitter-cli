from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.adapters.protocol import AdapterEvidence, KredoBalanceResult
from manager_api.db.base import Base
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.bindings import BindingState
from manager_api.models.tasks import TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.scheduler import Scheduler
from manager_api.services.balances import BalanceService
from manager_api.services.execution import TaskExecutionService
from manager_api.services.imports import AccountImportService
from manager_api.services.tasks import TaskService
from manager_api.services.vault import VaultService
from manager_api.services.wallets import WalletService
from manager_api.models.wallets import WalletSourceType
from manager_api.worker import TaskWorker


class FakeBalanceAdapter:
    """合成余额 Provider，确保同步路径只调用账户摘要。"""

    def __init__(self) -> None:
        self.summary_calls = 0

    def account_summary(self, account, wallet, operation):
        self.summary_calls += 1
        return KredoBalanceResult(
            points=Decimal("1200"),
            cash_hsk_available=Decimal("18.25"),
            positions_value_hsk=Decimal("2.75"),
            evidence=AdapterEvidence("balance_synced", "synthetic balance synced"),
        )


def test_balance_service_preserves_last_good_values_on_error() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        from manager_api.models.bindings import AccountWalletBinding

        account = SocialAccount(
            handle="balance-account",
            normalized_handle="balance-account",
            state=LifecycleState.ACTIVE,
            health=AccountHealth.UNKNOWN,
        )
        wallet = Wallet(
            address="0x" + "1" * 40,
            normalized_address="0x" + "1" * 40,
            state="active",
        )
        session.add_all([account, wallet])
        session.flush()
        binding = AccountWalletBinding(
            social_account_id=account.id,
            wallet_id=wallet.id,
            binding_key="fixture",
            state=BindingState.BOUND,
        )
        session.add(binding)
        session.flush()

        result = KredoBalanceResult(
            points=Decimal("100"),
            cash_hsk_available=Decimal("1.5"),
            positions_value_hsk=Decimal("2"),
            evidence=AdapterEvidence("balance_synced", "fixture"),
        )
        BalanceService(session).sync_success(binding.id, result)
        BalanceService(session).sync_error(binding.id, "rate_limited")

        snapshot = BalanceService(session).get(binding.id)
        assert snapshot is not None
        assert snapshot.snapshot.sync_status == "error"
        assert snapshot.snapshot.error_code == "rate_limited"
        assert snapshot.snapshot.points == Decimal("100.00000000")
        assert snapshot.snapshot.cash_hsk_available == Decimal("1.50000000")


def test_balance_sync_task_persists_snapshot_without_mutating_binding() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        from manager_api.models.bindings import AccountWalletBinding

        vault = VaultService(session)
        vault.initialize("fixture-vault-password")
        AccountImportService(session, vault).commit(
            "balance-execution-account\tfixture-password\tJBSWY3DPEHPK3PXP\t"
            "balance@example.test\tfixture-mail-password\tfixture-token\t"
            "auth_token=fixture-token; ct0=fixture-csrf"
        )
        account = (
            session.query(SocialAccount)
            .filter_by(handle="balance-execution-account")
            .one()
        )
        _, preview = WalletService(session, vault).commit(
            WalletSourceType.PRIVATE_KEY,
            "22" * 32,
            label="balance-wallet",
        )
        wallet_id = next(
            decision.wallet_id
            for decision in preview.decisions
            if decision.wallet_id is not None
        )
        wallet = session.get(Wallet, wallet_id)
        assert wallet is not None
        binding = AccountWalletBinding(
            social_account_id=account.id,
            wallet_id=wallet.id,
            binding_key="fixture",
            state=BindingState.BOUND,
        )
        session.add(binding)
        session.flush()

        TaskService(session).create(
            TaskKind.BALANCE_SYNC,
            binding_id=binding.id,
            external_target="kredo:account-summary:fixture",
        ).job
        scheduler = Scheduler(session, worker_concurrency=1, browser_concurrency=1)
        worker = TaskWorker(session, scheduler=scheduler)
        adapter = FakeBalanceAdapter()
        execution = TaskExecutionService(
            session,
            vault=vault,
            x_adapter=object(),
            kredo_adapter=adapter,
        )

        result = worker.run_one(scheduler.dispatch_once(limit=1)[0], execution.handle)
        assert result.state is TaskState.SUCCEEDED
        assert adapter.summary_calls == 1
        assert binding.state is BindingState.BOUND
        snapshot = BalanceService(session).get(binding.id)
        assert snapshot is not None
        assert snapshot.snapshot.points == Decimal("1200.00000000")
        assert snapshot.snapshot.cash_hsk_available == Decimal("18.25000000")
        assert snapshot.snapshot.positions_value_hsk == Decimal("2.75000000")
