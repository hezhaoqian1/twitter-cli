from __future__ import annotations

import json

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.models.accounts import AccountSecret, SocialAccount
from manager_api.models.wallets import Wallet, WalletSecret
from manager_api.services.vault import VaultService, VaultUnlockError
from scripts.manager_import_operator_data import _password_from_args, run_import


ACCOUNT_ROW = "\t".join(
    (
        "ImportFixture",
        "password-fixture",
        "JBSWY3DPEHPK3PXP",
        "import@example.test",
        "email-password-fixture",
        "token-fixture",
        "auth_token=token-fixture; ct0=csrf-fixture",
    )
)
PRIVATE_KEY = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"


@pytest.fixture
def session() -> Session:
    """创建隔离数据库会话。"""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_operator_import_initializes_vault_and_commits_files(
    session: Session,
    tmp_path,
) -> None:
    """导入脚本复用 Vault 加密账号和私钥材料。"""
    accounts_file = tmp_path / "accounts.tsv"
    private_keys_file = tmp_path / "private-keys.txt"
    recovery_file = tmp_path / "recovery.key"
    accounts_file.write_text(ACCOUNT_ROW, encoding="utf-8")
    private_keys_file.write_text(f"{PRIVATE_KEY}\n", encoding="utf-8")

    summary = run_import(
        session,
        vault_password="manager-password-fixture",
        accounts_file=accounts_file,
        private_keys_file=private_keys_file,
        initialize_vault=True,
        recovery_key_output=recovery_file,
    )
    session.commit()

    assert summary.vault_initialized is True
    assert summary.recovery_key_written is True
    assert summary.accounts["committed_rows"] == 1
    assert summary.wallets["committed"] == 1
    assert session.query(SocialAccount).count() == 1
    assert session.query(AccountSecret).count() == 1
    assert session.query(Wallet).count() == 1
    assert session.query(WalletSecret).count() == 1
    assert recovery_file.exists()
    assert recovery_file.stat().st_mode & 0o777 == 0o600

    output = summary.to_json()
    assert "password-fixture" not in output
    assert "token-fixture" not in output
    assert PRIVATE_KEY not in output
    assert json.loads(output)["wallets"]["committed"] == 1


def test_operator_import_dry_run_keeps_database_empty(session: Session, tmp_path) -> None:
    """dry-run 只做解析和预览，不提交账号或钱包记录。"""
    VaultService(session).initialize("manager-password-fixture")
    accounts_file = tmp_path / "accounts.tsv"
    private_keys_file = tmp_path / "private-keys.txt"
    accounts_file.write_text(ACCOUNT_ROW, encoding="utf-8")
    private_keys_file.write_text(f"{PRIVATE_KEY}\n", encoding="utf-8")

    summary = run_import(
        session,
        vault_password="manager-password-fixture",
        accounts_file=accounts_file,
        private_keys_file=private_keys_file,
        dry_run=True,
    )

    assert summary.vault_initialized is False
    assert summary.accounts["valid_rows"] == 1
    assert summary.accounts["committed_rows"] == 0
    assert summary.wallets["valid"] == 1
    assert summary.wallets["committed"] == 0
    assert session.query(SocialAccount).count() == 0
    assert session.query(Wallet).count() == 0


def test_operator_import_requires_existing_vault_or_init_flag(
    session: Session,
    tmp_path,
) -> None:
    """未初始化 Vault 时必须显式传入初始化开关。"""
    accounts_file = tmp_path / "accounts.tsv"
    accounts_file.write_text(ACCOUNT_ROW, encoding="utf-8")

    with pytest.raises(VaultUnlockError):
        run_import(
            session,
            vault_password="manager-password-fixture",
            accounts_file=accounts_file,
        )


def test_operator_import_counts_duplicate_private_keys_once(session: Session, tmp_path) -> None:
    """同一个私钥在输入文件中重复时只提交一次。"""
    VaultService(session).initialize("manager-password-fixture")
    private_keys_file = tmp_path / "private-keys.txt"
    private_keys_file.write_text(f"{PRIVATE_KEY}\n0x{PRIVATE_KEY.upper()}\n", encoding="utf-8")

    summary = run_import(
        session,
        vault_password="manager-password-fixture",
        private_keys_file=private_keys_file,
    )

    assert summary.wallets["total"] == 2
    assert summary.wallets["committed"] == 1
    assert summary.wallets["duplicate_in_file"] == 1
    assert session.query(Wallet).count() == 1


def test_password_helper_uses_settings_worker_password_when_env_is_not_exported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """命令行导入可以直接复用 .env.manager 加载到 settings 的 Worker 密码。"""
    monkeypatch.delenv("WORKER_VAULT_PASSWORD", raising=False)

    assert _password_from_args(
        None,
        "WORKER_VAULT_PASSWORD",
        SecretStr("settings-password-fixture"),
    ) == "settings-password-fixture"
