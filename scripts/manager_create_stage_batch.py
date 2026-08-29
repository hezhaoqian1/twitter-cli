#!/usr/bin/env python3
"""Create one manager stage batch from current database readiness."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.models.accounts import (
    AccountHealth,
    AccountSecret,
    LifecycleState,
    SocialAccount,
)
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet, WalletSecret
from manager_api.services.tasks import TaskError, TaskService
from manager_api.services.workflows import (
    WorkflowService,
    WorkflowStage,
    WorkflowStageBatchItem,
)

REPOST_BLOCKING_STATES = set(TaskState)
CLAIM_BLOCKING_STATES = set(TaskState)


@dataclass(frozen=True)
class StageBatchSelection:
    """Redacted stage selection result for logs and tests."""

    stage: WorkflowStage
    name: str
    dispatch_limit: int
    items: list[WorkflowStageBatchItem]


def _task_exists(
    session: Session,
    *,
    binding_id: UUID,
    kind: TaskKind,
    states: set[TaskState],
    external_target: str | None = None,
) -> bool:
    """检查当前绑定是否已有同类任务，避免重复创建活跃任务。"""
    query = select(TaskJob.id).where(
        TaskJob.binding_id == binding_id,
        TaskJob.kind == kind,
        TaskJob.state.in_(states),
    )
    if external_target is not None:
        digest = TaskService._target_digest(external_target)
        query = query.where(
            TaskJob.idempotency_key == f"{kind.value}:binding:{binding_id}:{digest}"
        )
    return session.scalar(query.limit(1)) is not None


def _bound_binding_ids(session: Session) -> set[UUID]:
    """返回仍然占用账号和地址的绑定记录。"""
    return set(
        session.scalars(
            select(AccountWalletBinding.id).where(
                AccountWalletBinding.archived_at.is_(None),
                AccountWalletBinding.state.in_((BindingState.PENDING, BindingState.BOUND)),
            )
        ).all()
    )


def _occupied_account_ids(session: Session) -> set[UUID]:
    """返回已有未归档绑定意图的账号 ID。"""
    return set(
        session.scalars(
            select(AccountWalletBinding.social_account_id).where(
                AccountWalletBinding.archived_at.is_(None),
                AccountWalletBinding.state.in_((BindingState.PENDING, BindingState.BOUND)),
            )
        ).all()
    )


def _occupied_wallet_ids(session: Session) -> set[UUID]:
    """返回已有未归档绑定意图的钱包 ID。"""
    return set(
        session.scalars(
            select(AccountWalletBinding.wallet_id).where(
                AccountWalletBinding.archived_at.is_(None),
                AccountWalletBinding.state.in_((BindingState.PENDING, BindingState.BOUND)),
            )
        ).all()
    )


def _select_verify(session: Session, *, limit: int) -> list[WorkflowStageBatchItem]:
    """选择活跃且存在当前密钥的账号做登录校验。"""
    rows = session.scalars(
        select(SocialAccount)
        .join(AccountSecret, AccountSecret.social_account_id == SocialAccount.id)
        .where(
            SocialAccount.state == LifecycleState.ACTIVE,
            SocialAccount.archived_at.is_(None),
            AccountSecret.is_current.is_(True),
        )
        .order_by(SocialAccount.created_at, SocialAccount.id)
        .limit(limit)
    ).all()
    return [
        WorkflowStageBatchItem(
            social_account_id=account.id,
            external_target="x:verify",
        )
        for account in rows
    ]


def _select_bind(
    session: Session,
    *,
    limit: int,
    include_unverified: bool,
) -> list[WorkflowStageBatchItem]:
    """按创建顺序把可用账号和可用地址一一配对。"""
    occupied_accounts = _occupied_account_ids(session)
    occupied_wallets = _occupied_wallet_ids(session)
    account_query = (
        select(SocialAccount)
        .join(AccountSecret, AccountSecret.social_account_id == SocialAccount.id)
        .where(
            SocialAccount.state == LifecycleState.ACTIVE,
            SocialAccount.archived_at.is_(None),
            AccountSecret.is_current.is_(True),
            SocialAccount.id.not_in(occupied_accounts or {UUID(int=0)}),
        )
        .order_by(SocialAccount.created_at, SocialAccount.id)
        .limit(limit)
    )
    if not include_unverified:
        account_query = account_query.where(SocialAccount.health == AccountHealth.HEALTHY)
    accounts = session.scalars(account_query).all()
    wallets = session.scalars(
        select(Wallet)
        .join(WalletSecret, WalletSecret.wallet_id == Wallet.id)
        .where(
            Wallet.state == "active",
            Wallet.archived_at.is_(None),
            WalletSecret.is_current.is_(True),
            Wallet.id.not_in(occupied_wallets or {UUID(int=0)}),
        )
        .order_by(Wallet.created_at, Wallet.id)
        .limit(limit)
    ).all()
    return [
        WorkflowStageBatchItem(
            social_account_id=account.id,
            wallet_id=wallet.id,
            external_target="kredo:bind",
        )
        for account, wallet in zip(accounts, wallets, strict=False)
    ][:limit]


def _select_repost(
    session: Session,
    *,
    limit: int,
    external_target: str,
) -> list[WorkflowStageBatchItem]:
    """选择已绑定且当前目标未提交或未完成转发的记录。"""
    bindings = session.scalars(
        select(AccountWalletBinding)
        .where(
            AccountWalletBinding.state == BindingState.BOUND,
            AccountWalletBinding.archived_at.is_(None),
        )
        .order_by(AccountWalletBinding.bound_at, AccountWalletBinding.id)
    ).all()
    items: list[WorkflowStageBatchItem] = []
    for binding in bindings:
        if _task_exists(
            session,
            binding_id=binding.id,
            kind=TaskKind.REPOST,
            states=REPOST_BLOCKING_STATES,
            external_target=external_target,
        ):
            continue
        items.append(
            WorkflowStageBatchItem(
                binding_id=binding.id,
                external_target=external_target,
            )
        )
        if len(items) >= limit:
            break
    return items


def _select_claim(
    session: Session,
    *,
    limit: int,
    external_target: str,
) -> list[WorkflowStageBatchItem]:
    """只选择已转发成功且没有领取中或已领取任务的绑定。"""
    bindings = session.scalars(
        select(AccountWalletBinding)
        .where(
            AccountWalletBinding.state == BindingState.BOUND,
            AccountWalletBinding.archived_at.is_(None),
        )
        .order_by(AccountWalletBinding.bound_at, AccountWalletBinding.id)
    ).all()
    items: list[WorkflowStageBatchItem] = []
    for binding in bindings:
        has_repost = _task_exists(
            session,
            binding_id=binding.id,
            kind=TaskKind.REPOST,
            states={TaskState.SUCCEEDED},
        )
        has_claim = _task_exists(
            session,
            binding_id=binding.id,
            kind=TaskKind.CLAIM,
            states=CLAIM_BLOCKING_STATES,
        )
        if not has_repost or has_claim:
            continue
        items.append(
            WorkflowStageBatchItem(
                binding_id=binding.id,
                external_target=external_target,
            )
        )
        if len(items) >= limit:
            break
    return items


def select_stage_items(
    session: Session,
    *,
    stage: WorkflowStage,
    limit: int,
    external_target: str,
    include_unverified: bool = False,
) -> list[WorkflowStageBatchItem]:
    """根据阶段路由到对应选择器。"""
    if limit < 1:
        raise ValueError("limit must be positive")
    if stage is WorkflowStage.VERIFY:
        return _select_verify(session, limit=limit)
    if stage is WorkflowStage.BIND:
        return _select_bind(
            session,
            limit=limit,
            include_unverified=include_unverified,
        )
    if stage is WorkflowStage.REPOST:
        return _select_repost(session, limit=limit, external_target=external_target)
    if stage is WorkflowStage.CLAIM:
        return _select_claim(session, limit=limit, external_target=external_target)
    raise ValueError(f"unsupported stage: {stage.value}")


def create_stage_batch_from_selection(
    session: Session,
    *,
    name: str,
    stage: WorkflowStage,
    limit: int,
    dispatch_limit: int,
    external_target: str,
    include_unverified: bool = False,
) -> StageBatchSelection:
    """选择候选行并创建单阶段批次。"""
    items = select_stage_items(
        session,
        stage=stage,
        limit=limit,
        external_target=external_target,
        include_unverified=include_unverified,
    )
    selection = StageBatchSelection(
        stage=stage,
        name=name,
        dispatch_limit=dispatch_limit,
        items=items,
    )
    if not items:
        return selection
    WorkflowService(session).create_stage_batch(
        name=name,
        stage=stage,
        dispatch_limit=dispatch_limit,
        items=items,
    )
    return selection


def _default_target(stage: WorkflowStage, target: str | None) -> str:
    """给非转发阶段提供稳定默认目标。"""
    if target and target.strip():
        return target.strip()
    if stage is WorkflowStage.REPOST:
        raise ValueError("--target is required for repost stage")
    return {
        WorkflowStage.VERIFY: "x:verify",
        WorkflowStage.BIND: "kredo:bind",
        WorkflowStage.CLAIM: "kredo:claim",
    }[stage]


def _default_name(stage: WorkflowStage, name: str | None) -> str:
    """生成便于日志识别的批次名称。"""
    if name and name.strip():
        return name.strip()
    return f"{stage.value} stage batch"


def _print_selection(selection: StageBatchSelection, *, dry_run: bool) -> None:
    """输出不含账号、Cookie、私钥和链接原文的创建摘要。"""
    action = "预览" if dry_run else "已创建"
    print(f"{action} stage={selection.stage.value}")
    print(f"name={selection.name}")
    print(f"dispatch_limit={selection.dispatch_limit}")
    print(f"items={len(selection.items)}")


def main() -> int:
    """从环境配置连接数据库并创建一个阶段批次。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=[stage.value for stage in WorkflowStage])
    parser.add_argument("--name")
    parser.add_argument("--target", help="repost URL/id or provider claim target")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--dispatch-limit", type=int, default=10)
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help="allow unknown/degraded accounts in bind selection",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only count selected rows without creating tasks",
    )
    args = parser.parse_args()

    try:
        stage = WorkflowStage(args.stage)
        target = _default_target(stage, args.target)
        name = _default_name(stage, args.name)
        engine = build_engine(get_settings())
        with session_scope(engine) as session:
            items = select_stage_items(
                session,
                stage=stage,
                limit=args.limit,
                external_target=target,
                include_unverified=args.include_unverified,
            )
            selection = StageBatchSelection(
                stage=stage,
                name=name,
                dispatch_limit=args.dispatch_limit,
                items=items,
            )
            if not args.dry_run and items:
                WorkflowService(session).create_stage_batch(
                    name=name,
                    stage=stage,
                    dispatch_limit=args.dispatch_limit,
                    items=items,
                )
            _print_selection(selection, dry_run=args.dry_run)
        engine.dispose()
    except (TaskError, ValueError) as error:
        print(f"创建阶段批次失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
