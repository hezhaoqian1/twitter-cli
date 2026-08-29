"""Exact account/private-key row pairing for bind stage creation."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..db.base import utc_now
from ..models.accounts import (
    AccountHealth,
    AccountSecret,
    LifecycleState,
    SocialAccount,
)
from ..models.bindings import AccountWalletBinding, BindingState
from ..models.tasks import ResourceLease
from ..models.wallets import Wallet, WalletSecret
from .imports import AccountImportService, ImportDecision
from .wallets import WalletDecision, WalletService
from .workflows import WorkflowService, WorkflowStage, WorkflowStageBatchItem


@dataclass(frozen=True)
class PairingDecision:
    """One redacted row-pair decision for tests and aggregate summaries."""

    row_number: int
    status: str
    social_account_id: UUID | None = None
    wallet_id: UUID | None = None


@dataclass(frozen=True)
class PairingSummary:
    """Redacted API/CLI summary without handles, addresses, cookies, or keys."""

    apply: bool
    name: str
    limit: int
    dispatch_limit: int
    total_pairs: int
    selected_pairs: int
    created_jobs: int
    counts: dict[str, int]

    def to_json(self) -> str:
        """Render stable JSON for server logs and tests."""
        return json.dumps(
            {
                "apply": self.apply,
                "name": self.name,
                "limit": self.limit,
                "dispatch_limit": self.dispatch_limit,
                "total_pairs": self.total_pairs,
                "selected_pairs": self.selected_pairs,
                "created_jobs": self.created_jobs,
                "counts": self.counts,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def _account_decisions(session: Session, content: str) -> list[ImportDecision]:
    """复用账号导入解析器，保留输入行序。"""
    return AccountImportService(session).preview(content).decisions


def _wallet_decisions(session: Session, content: str) -> list[WalletDecision]:
    """复用私钥解析器，只保留公开地址和分类状态。"""
    return WalletService(session).preview_private_keys(content).decisions


def _account_by_decision(session: Session, decision: ImportDecision) -> SocialAccount | None:
    """根据账号输入行定位已导入且有当前密钥的账号。"""
    if decision.parsed is None:
        return None
    normalized_handle = AccountImportService._normalize_handle(decision.parsed.handle)
    return session.scalar(
        select(SocialAccount)
        .join(AccountSecret, AccountSecret.social_account_id == SocialAccount.id)
        .where(
            SocialAccount.normalized_handle == normalized_handle,
            SocialAccount.archived_at.is_(None),
            AccountSecret.is_current.is_(True),
        )
        .limit(1)
    )


def _wallet_by_decision(session: Session, decision: WalletDecision) -> Wallet | None:
    """根据私钥推导出的公开地址定位已导入且有当前密钥的钱包。"""
    return session.scalar(
        select(Wallet)
        .join(WalletSecret, WalletSecret.wallet_id == Wallet.id)
        .where(
            Wallet.normalized_address == decision.candidate.normalized_address,
            Wallet.archived_at.is_(None),
            WalletSecret.is_current.is_(True),
        )
        .limit(1)
    )


def _resource_conflict(session: Session, account_id: UUID, wallet_id: UUID) -> str | None:
    """提前检查绑定不变式，支持 dry-run 看到真实筛选结果。"""
    bindings = session.scalars(
        select(AccountWalletBinding).where(
            or_(
                AccountWalletBinding.social_account_id == account_id,
                AccountWalletBinding.wallet_id == wallet_id,
            )
        )
    ).all()
    for binding in bindings:
        if binding.state is BindingState.PENDING and binding.archived_at is None:
            return "binding_in_progress"
        if binding.state is BindingState.BOUND or binding.bound_at is not None:
            return "already_bound"

    lease = session.scalar(
        select(ResourceLease.id).where(
            ResourceLease.lease_key.in_((f"account:{account_id}", f"wallet:{wallet_id}")),
            ResourceLease.expires_at > utc_now(),
        )
    )
    return "resource_leased" if lease is not None else None


def select_bound_pairs(
    session: Session,
    *,
    accounts_content: str,
    private_keys_content: str,
    limit: int,
    include_unverified: bool = False,
) -> list[PairingDecision]:
    """按两个输入的同一行配对，返回可创建绑定任务的脱敏决策。"""
    if limit < 1:
        raise ValueError("limit must be positive")
    accounts = _account_decisions(session, accounts_content)
    wallets = _wallet_decisions(session, private_keys_content)
    total_pairs = max(len(accounts), len(wallets))
    decisions: list[PairingDecision] = []
    selected_count = 0

    for index in range(total_pairs):
        row_number = index + 1
        account_decision = accounts[index] if index < len(accounts) else None
        wallet_decision = wallets[index] if index < len(wallets) else None

        if account_decision is None:
            decisions.append(PairingDecision(row_number, "missing_account_row"))
            continue
        if wallet_decision is None:
            decisions.append(PairingDecision(row_number, "missing_wallet_row"))
            continue
        if account_decision.parsed is None:
            decisions.append(PairingDecision(row_number, "malformed_account_row"))
            continue
        if account_decision.diagnostic_code == "duplicate_handle_in_file":
            decisions.append(PairingDecision(row_number, "duplicate_account_in_file"))
            continue
        if account_decision.diagnostic_code == "active_session_conflict":
            decisions.append(PairingDecision(row_number, "account_session_conflict"))
            continue
        if wallet_decision.diagnostic_code == "duplicate_address_in_file":
            decisions.append(PairingDecision(row_number, "duplicate_wallet_in_file"))
            continue

        account = _account_by_decision(session, account_decision)
        if account is None:
            decisions.append(PairingDecision(row_number, "account_not_imported"))
            continue
        wallet = _wallet_by_decision(session, wallet_decision)
        if wallet is None:
            decisions.append(PairingDecision(row_number, "wallet_not_imported"))
            continue
        if account.state is not LifecycleState.ACTIVE:
            decisions.append(PairingDecision(row_number, "account_not_active"))
            continue
        if wallet.state != "active":
            decisions.append(PairingDecision(row_number, "wallet_not_active"))
            continue
        if not include_unverified and account.health is not AccountHealth.HEALTHY:
            decisions.append(PairingDecision(row_number, "account_not_healthy"))
            continue

        conflict = _resource_conflict(session, account.id, wallet.id)
        if conflict is not None:
            decisions.append(PairingDecision(row_number, conflict))
            continue
        if selected_count >= limit:
            decisions.append(PairingDecision(row_number, "over_limit", account.id, wallet.id))
            continue

        selected_count += 1
        decisions.append(PairingDecision(row_number, "selected", account.id, wallet.id))

    return decisions


def create_bound_pair_batch(
    session: Session,
    *,
    accounts_content: str,
    private_keys_content: str,
    name: str,
    limit: int,
    dispatch_limit: int,
    include_unverified: bool = False,
    apply: bool = False,
) -> PairingSummary:
    """创建按行精确配对的 bind 阶段批次，不串联转发或领取。"""
    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("name must not be empty")
    if dispatch_limit < 1:
        raise ValueError("dispatch limit must be positive")

    decisions = select_bound_pairs(
        session,
        accounts_content=accounts_content,
        private_keys_content=private_keys_content,
        limit=limit,
        include_unverified=include_unverified,
    )
    selected = [decision for decision in decisions if decision.status == "selected"]
    created_jobs = 0
    if selected and apply:
        result = WorkflowService(session).create_stage_batch(
            name=normalized_name,
            stage=WorkflowStage.BIND,
            dispatch_limit=dispatch_limit,
            items=[
                WorkflowStageBatchItem(
                    social_account_id=decision.social_account_id,
                    wallet_id=decision.wallet_id,
                    external_target="kredo:bind",
                )
                for decision in selected
            ],
        )
        created_jobs = len(result.jobs)

    return PairingSummary(
        apply=apply,
        name=normalized_name,
        limit=limit,
        dispatch_limit=dispatch_limit,
        total_pairs=len(decisions),
        selected_pairs=len(selected),
        created_jobs=created_jobs,
        counts=dict(sorted(Counter(decision.status for decision in decisions).items())),
    )
