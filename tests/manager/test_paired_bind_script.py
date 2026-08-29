from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.models.accounts import AccountHealth, SocialAccount
from manager_api.models.bindings import AccountWalletBinding
from manager_api.models.tasks import TaskJob, TaskKind
from manager_api.models.wallets import Wallet
from manager_api.services.imports import AccountImportService
from manager_api.services.vault import VaultService
from manager_api.services.wallets import WalletService
from scripts.manager_create_bound_pairs_from_files import (
    create_bound_pair_batch_from_files,
    select_bound_pairs_from_files,
)

PRIVATE_KEYS = (
    "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "59c6995e998f97a5a0044966f0945382e9dae8adf733bcf4a936f586124f0e86",
    "5de4111a3164bbee802dbf778a4e5f08dae8600d4ddef368a0c7a596bfb58898",
)


@pytest.fixture
def session() -> Session:
    """创建隔离数据库会话。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _account_row(handle: str) -> str:
    """生成账号 TSV 夹具，不包含真实账号材料。"""
    return "\t".join(
        (
            handle,
            f"{handle}-password",
            "JBSWY3DPEHPK3PXP",
            f"{handle}@example.test",
            f"{handle}-mail-password",
            f"{handle}-token",
            f"auth_token={handle}-token; ct0={handle}-csrf",
        )
    )


def _write_fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    """写入存在重复账号行的配对输入，用于证明后续行不会错位。"""
    accounts_file = tmp_path / "accounts.tsv"
    private_keys_file = tmp_path / "private-keys.txt"
    accounts_file.write_text(
        "\n".join(
            (
                _account_row("paired-alpha"),
                _account_row("paired-alpha"),
                _account_row("paired-bravo"),
            )
        ),
        encoding="utf-8",
    )
    private_keys_file.write_text("\n".join(PRIVATE_KEYS), encoding="utf-8")
    return accounts_file, private_keys_file


def _import_fixtures(session: Session, accounts_file: Path, private_keys_file: Path) -> None:
    """通过正式导入服务写入账号和钱包，再把账号标记为可绑定。"""
    vault = VaultService(session)
    vault.initialize("manager-password-fixture")
    AccountImportService(session, vault).commit(accounts_file.read_text(encoding="utf-8"))
    WalletService(session, vault).commit_private_keys(private_keys_file.read_text(encoding="utf-8"))
    for account in session.query(SocialAccount).all():
        account.health = AccountHealth.HEALTHY
    session.flush()


def _wallet_by_private_key(session: Session, private_key: str) -> Wallet:
    """根据测试私钥推导公开地址并取回钱包记录。"""
    decision = WalletService(session).preview_private_keys(private_key).decisions[0]
    return session.query(Wallet).filter_by(
        normalized_address=decision.candidate.normalized_address
    ).one()


def test_pair_selection_uses_same_input_line_after_duplicate_account_skip(
    session: Session,
    tmp_path: Path,
) -> None:
    """重复账号行被跳过时，第三行账号仍配第三行私钥。"""
    accounts_file, private_keys_file = _write_fixture_files(tmp_path)
    _import_fixtures(session, accounts_file, private_keys_file)

    decisions = select_bound_pairs_from_files(
        session,
        accounts_file=accounts_file,
        private_keys_file=private_keys_file,
        limit=10,
    )

    alpha = session.query(SocialAccount).filter_by(normalized_handle="paired-alpha").one()
    bravo = session.query(SocialAccount).filter_by(normalized_handle="paired-bravo").one()
    wallet_1 = _wallet_by_private_key(session, PRIVATE_KEYS[0])
    wallet_3 = _wallet_by_private_key(session, PRIVATE_KEYS[2])
    assert [(decision.row_number, decision.status) for decision in decisions] == [
        (1, "selected"),
        (2, "duplicate_account_in_file"),
        (3, "selected"),
    ]
    assert decisions[0].social_account_id == alpha.id
    assert decisions[0].wallet_id == wallet_1.id
    assert decisions[2].social_account_id == bravo.id
    assert decisions[2].wallet_id == wallet_3.id


def test_pair_batch_dry_run_does_not_create_bindings_or_tasks(
    session: Session,
    tmp_path: Path,
) -> None:
    """dry-run 只返回聚合计数，不创建绑定意图或任务。"""
    accounts_file, private_keys_file = _write_fixture_files(tmp_path)
    _import_fixtures(session, accounts_file, private_keys_file)

    summary = create_bound_pair_batch_from_files(
        session,
        accounts_file=accounts_file,
        private_keys_file=private_keys_file,
        name="paired bind dry run",
        limit=10,
        dispatch_limit=2,
        dry_run=True,
    )

    assert summary.selected_pairs == 2
    assert summary.created_jobs == 0
    assert summary.counts == {"duplicate_account_in_file": 1, "selected": 2}
    assert session.query(AccountWalletBinding).count() == 0
    assert session.query(TaskJob).count() == 0


def test_pair_batch_apply_creates_only_bind_stage_jobs(
    session: Session,
    tmp_path: Path,
) -> None:
    """应用配对批次时只创建绑定阶段，不串联转发或领取。"""
    accounts_file, private_keys_file = _write_fixture_files(tmp_path)
    _import_fixtures(session, accounts_file, private_keys_file)

    summary = create_bound_pair_batch_from_files(
        session,
        accounts_file=accounts_file,
        private_keys_file=private_keys_file,
        name="paired bind apply",
        limit=10,
        dispatch_limit=2,
    )

    jobs = session.query(TaskJob).order_by(TaskJob.created_at, TaskJob.id).all()
    assert summary.selected_pairs == 2
    assert summary.created_jobs == 2
    assert session.query(AccountWalletBinding).count() == 2
    assert [job.kind for job in jobs] == [TaskKind.BIND, TaskKind.BIND]
    assert session.query(TaskJob).filter(TaskJob.kind.in_((TaskKind.REPOST, TaskKind.CLAIM))).count() == 0

