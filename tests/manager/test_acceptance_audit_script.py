from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, AccountSecret, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet, WalletSecret
from manager_api.services.acceptance import collect_acceptance_audit
from scripts.manager_acceptance_audit import _print_table


def _session() -> Session:
    """创建隔离内存库测试验收审计输出。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _account(session: Session, index: int) -> SocialAccount:
    """创建带当前密钥标记的账号夹具。"""
    handle = f"audit-account-{index}"
    account = SocialAccount(
        handle=handle,
        normalized_handle=handle,
        state=LifecycleState.ACTIVE,
        health=AccountHealth.HEALTHY,
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
            secret_fingerprint=f"account-{index}",
            redacted_metadata="{}",
        )
    )
    return account


def _wallet(session: Session, index: int) -> Wallet:
    """创建带当前密钥标记的钱包夹具。"""
    address = f"0x{index:02x}" + "f" * 38
    wallet = Wallet(address=address, normalized_address=address, state="active")
    session.add(wallet)
    session.flush()
    session.add(
        WalletSecret(
            wallet_id=wallet.id,
            version=1,
            is_current=True,
            envelope=b"fixture",
            envelope_version=1,
            secret_fingerprint=f"wallet-{index}",
            redacted_metadata="{}",
        )
    )
    return wallet


def _binding(session: Session, index: int) -> AccountWalletBinding:
    """创建 pending 绑定夹具。"""
    account = _account(session, index)
    wallet = _wallet(session, index)
    binding = AccountWalletBinding(
        social_account_id=account.id,
        wallet_id=wallet.id,
        binding_key=f"audit-binding-{index}",
        state=BindingState.PENDING,
    )
    session.add(binding)
    session.flush()
    return binding


def test_acceptance_audit_lists_poll_sync_and_retry_actions(capsys) -> None:
    """验收审计把混合 pending 绑定拆成可执行动作清单。"""
    session = _session()
    try:
        pollable = _binding(session, 1)
        syncable = _binding(session, 2)
        failed = _binding(session, 3)
        session.add_all(
            [
                TaskJob(
                    kind=TaskKind.BIND,
                    state=TaskState.WAITING_EXTERNAL_VALIDATION,
                    attempt=1,
                    priority=0,
                    binding_id=pollable.id,
                    social_account_id=pollable.social_account_id,
                    wallet_id=pollable.wallet_id,
                    external_target="hidden-bind-target",
                    external_operation_ref="external-ref",
                    idempotency_key="audit-pollable",
                    lease_keys=[],
                    scheduled_at=utc_now(),
                ),
                TaskJob(
                    kind=TaskKind.BIND,
                    state=TaskState.FAILED,
                    attempt=1,
                    priority=0,
                    binding_id=failed.id,
                    social_account_id=failed.social_account_id,
                    wallet_id=failed.wallet_id,
                    external_target="hidden-failure-target",
                    idempotency_key="audit-failed",
                    lease_keys=[],
                    scheduled_at=utc_now(),
                    failure_code="fixture",
                ),
            ]
        )
        session.flush()

        audit = collect_acceptance_audit(session, limit=10)
        _print_table(audit)
        rendered = capsys.readouterr().out
        decoded = json.loads(audit.to_json())

        assert audit.next_action.action == "poll"
        assert any(action.action == "poll" and action.stage == "bind" for action in audit.actions)
        assert any(action.action == "sync_bind_status" and action.stage == "bind" for action in audit.actions)
        assert any(action.action == "retry" and action.stage == "bind" for action in audit.actions)
        assert decoded["stages"][1]["status_syncable"] == 2
        assert "manager_requeue_stage_polls.py bind" in rendered
        assert "manager_sync_bind_status.py" in rendered
        assert "manager_retry_stage_failures.py bind" in rendered
        assert "hidden-bind-target" not in rendered
        assert "hidden-failure-target" not in audit.to_json()
        assert syncable.account.handle not in audit.to_json()
    finally:
        session.close()
