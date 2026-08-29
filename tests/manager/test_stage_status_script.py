from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, AccountSecret, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet, WalletSecret
from manager_api.services.stage_status import collect_stage_status


def _session() -> Session:
    """创建隔离内存库测试阶段状态快照。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _account(session: Session, handle: str, *, health: AccountHealth) -> SocialAccount:
    """创建带当前密钥标记的账号夹具，不保存真实凭据。"""
    account = SocialAccount(
        handle=handle,
        normalized_handle=handle.casefold(),
        state=LifecycleState.ACTIVE,
        health=health,
    )
    session.add(account)
    session.flush()
    session.add(
        AccountSecret(
            social_account_id=account.id,
            version=1,
            is_current=True,
            envelope=b"fixture",
            envelope_version=1,
            secret_fingerprint=f"fingerprint:{handle}",
            redacted_metadata="{}",
        )
    )
    session.flush()
    return account


def _wallet(session: Session, suffix: str) -> Wallet:
    """创建带当前密钥标记的钱包夹具，不保存真实私钥。"""
    address = "0x" + suffix * 40
    wallet = Wallet(
        address=address,
        normalized_address=address,
        state="active",
    )
    session.add(wallet)
    session.flush()
    session.add(
        WalletSecret(
            wallet_id=wallet.id,
            version=1,
            is_current=True,
            envelope=b"fixture",
            envelope_version=1,
            secret_fingerprint=f"wallet:{suffix}",
            redacted_metadata="{}",
        )
    )
    session.flush()
    return wallet


def _binding(
    session: Session,
    account: SocialAccount,
    wallet: Wallet,
    *,
    state: BindingState,
) -> AccountWalletBinding:
    """创建绑定夹具，用于汇总阶段状态。"""
    binding = AccountWalletBinding(
        social_account_id=account.id,
        wallet_id=wallet.id,
        binding_key=f"{account.id}:{wallet.id}",
        state=state,
        bound_at=utc_now() if state is BindingState.BOUND else None,
    )
    session.add(binding)
    session.flush()
    return binding


def test_collect_stage_status_reports_ready_pollable_and_retryable_counts() -> None:
    """阶段状态快照聚合 ready、可轮询和可重试任务。"""
    session = _session()
    try:
        free_account = _account(session, "free-account", health=AccountHealth.HEALTHY)
        bound_account = _account(session, "bound-account", health=AccountHealth.HEALTHY)
        waiting_account = _account(session, "waiting-account", health=AccountHealth.HEALTHY)
        free_wallet = _wallet(session, "1")
        bound_wallet = _wallet(session, "2")
        waiting_wallet = _wallet(session, "3")
        bound = _binding(session, bound_account, bound_wallet, state=BindingState.BOUND)
        waiting = _binding(session, waiting_account, waiting_wallet, state=BindingState.PENDING)
        assert free_account.id
        assert free_wallet.id
        session.add_all(
            [
                TaskJob(
                    kind=TaskKind.BIND,
                    state=TaskState.WAITING_EXTERNAL_VALIDATION,
                    attempt=1,
                    priority=0,
                    social_account_id=waiting.social_account_id,
                    wallet_id=waiting.wallet_id,
                    binding_id=waiting.id,
                    external_target="hidden-bind-target",
                    external_operation_ref="bind-ref",
                    idempotency_key="bind:waiting",
                    lease_keys=[],
                    scheduled_at=utc_now(),
                ),
                TaskJob(
                    kind=TaskKind.REPOST,
                    state=TaskState.FAILED,
                    attempt=1,
                    priority=0,
                    social_account_id=bound.social_account_id,
                    wallet_id=bound.wallet_id,
                    binding_id=bound.id,
                    external_target="hidden-repost-target",
                    idempotency_key="repost:failed",
                    lease_keys=[],
                    scheduled_at=utc_now(),
                    failure_code="fixture_failure",
                ),
            ]
        )
        session.flush()

        summary = collect_stage_status(session, limit=10)

        rows = {row.stage: row for row in summary.stages}
        assert summary.resources["accounts_active"] == 3
        assert summary.resources["bindings_pending"] == 1
        assert summary.resources["bindings_bound"] == 1
        assert rows["bind"].ready == 1
        assert rows["bind"].pollable == 1
        assert rows["bind"].status_syncable == 0
        assert rows["repost"].retryable == 1
    finally:
        session.close()


def test_stage_status_json_is_redacted() -> None:
    """JSON 输出只包含聚合数字，不包含任务目标原文。"""
    session = _session()
    try:
        account = _account(session, "json-account", health=AccountHealth.HEALTHY)
        wallet = _wallet(session, "4")
        binding = _binding(session, account, wallet, state=BindingState.BOUND)
        session.add(
            TaskJob(
                kind=TaskKind.CLAIM,
                state=TaskState.FAILED,
                attempt=1,
                priority=0,
                social_account_id=binding.social_account_id,
                wallet_id=binding.wallet_id,
                binding_id=binding.id,
                external_target="hidden-claim-target",
                idempotency_key="claim:failed",
                lease_keys=[],
                scheduled_at=utc_now(),
            )
        )
        session.flush()

        payload = collect_stage_status(session, limit=10).to_json()

        decoded = json.loads(payload)
        assert decoded["stages"][3]["stage"] == "claim"
        assert "hidden-claim-target" not in payload
        assert "json-account" not in payload
    finally:
        session.close()
