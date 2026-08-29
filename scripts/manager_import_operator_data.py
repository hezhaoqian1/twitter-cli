#!/usr/bin/env python3
"""Import operator account and wallet files into the encrypted manager vault."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy.orm import Session

from manager_api.config import get_settings
from manager_api.db.session import build_engine, session_scope
from manager_api.services.imports import AccountImportService, summarize_preview
from manager_api.services.vault import (
    VaultAlreadyInitializedError,
    VaultService,
    VaultUnlockError,
)
from manager_api.services.wallets import WalletInputError, WalletService


@dataclass(frozen=True)
class ImportRunSummary:
    """脱敏导入摘要，供命令行和测试断言使用。"""

    vault_initialized: bool
    accounts: dict[str, int]
    wallets: dict[str, int]
    recovery_key_written: bool = False

    def to_json(self) -> str:
        """输出稳定 JSON，不包含账号、Cookie、私钥或恢复密钥。"""
        return json.dumps(
            {
                "vault_initialized": self.vault_initialized,
                "accounts": self.accounts,
                "wallets": self.wallets,
                "recovery_key_written": self.recovery_key_written,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def _read_text(path: Path) -> str:
    """读取 UTF-8 输入文件，让格式错误尽早暴露。"""
    return path.read_text(encoding="utf-8")


def _password_from_args(
    value: str | None,
    env_name: str,
    settings_password: SecretStr | None = None,
) -> str:
    """从参数、进程环境或 settings env file 取得 Vault 密码。"""
    password = value or os.environ.get(env_name)
    if not password and env_name == "WORKER_VAULT_PASSWORD" and settings_password is not None:
        password = settings_password.get_secret_value()
    if not password:
        raise ValueError(f"vault password is required via --vault-password or {env_name}")
    return password


def _write_recovery_key(path: Path, recovery_key: str) -> None:
    """把新生成的恢复密钥写入 0600 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{recovery_key}\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _unlock_or_initialize(
    session: Session,
    vault: VaultService,
    *,
    password: str,
    initialize: bool,
    recovery_key_output: Path | None,
) -> tuple[bool, bool]:
    """初始化或解锁 Vault，返回是否初始化和是否写出恢复密钥。"""
    if vault.initialized:
        vault.unlock_with_password(password)
        return False, False
    if not initialize:
        raise VaultUnlockError("vault is not initialized")
    try:
        result = vault.initialize(password)
    except VaultAlreadyInitializedError:
        vault.unlock_with_password(password)
        return False, False
    wrote_recovery_key = False
    if recovery_key_output is not None:
        _write_recovery_key(recovery_key_output, result.recovery_key)
        wrote_recovery_key = True
    session.flush()
    return True, wrote_recovery_key


def _account_summary(
    session: Session,
    vault: VaultService,
    *,
    accounts_file: Path | None,
    dry_run: bool,
) -> dict[str, int]:
    """预览或写入账号 TSV，并只返回聚合计数。"""
    empty = {
        "total_rows": 0,
        "valid_rows": 0,
        "malformed_rows": 0,
        "duplicate_rows": 0,
        "existing_rows": 0,
        "conflicting_rows": 0,
        "committed_rows": 0,
        "skipped_rows": 0,
    }
    if accounts_file is None:
        return empty
    content = _read_text(accounts_file)
    service = AccountImportService(session, vault)
    if dry_run:
        return summarize_preview(service.preview(content))
    batch, preview = service.commit(content, source_name=accounts_file.name)
    counts = summarize_preview(preview)
    counts["committed_rows"] = batch.committed_rows
    counts["skipped_rows"] = batch.skipped_rows
    return counts


def _wallet_summary(
    session: Session,
    vault: VaultService,
    *,
    private_keys_file: Path | None,
    dry_run: bool,
    label_prefix: str,
) -> dict[str, int]:
    """预览或写入私钥列表，并只返回聚合计数。"""
    counts = {
        "total": 0,
        "valid": 0,
        "duplicate_in_file": 0,
        "duplicate_existing": 0,
        "committed": 0,
        "skipped": 0,
    }
    if private_keys_file is None:
        return counts
    service = WalletService(session, vault)
    content = _read_text(private_keys_file)
    preview = (
        service.preview_private_keys(content, label_prefix=label_prefix)
        if dry_run
        else service.commit_private_keys(content, label_prefix=label_prefix)[1]
    )
    committed = 0 if dry_run else sum(decision.wallet_id is not None for decision in preview.decisions)
    return preview.summary(committed=committed)


def run_import(
    session: Session,
    *,
    vault_password: str,
    accounts_file: Path | None = None,
    private_keys_file: Path | None = None,
    initialize_vault: bool = False,
    recovery_key_output: Path | None = None,
    dry_run: bool = False,
    wallet_label_prefix: str = "operator-wallet",
) -> ImportRunSummary:
    """执行一次导入，所有敏感字段都走现有 Vault service。"""
    vault = VaultService(session)
    initialized, recovery_written = _unlock_or_initialize(
        session,
        vault,
        password=vault_password,
        initialize=initialize_vault,
        recovery_key_output=recovery_key_output,
    )
    accounts = _account_summary(
        session,
        vault,
        accounts_file=accounts_file,
        dry_run=dry_run,
    )
    wallets = _wallet_summary(
        session,
        vault,
        private_keys_file=private_keys_file,
        dry_run=dry_run,
        label_prefix=wallet_label_prefix,
    )
    return ImportRunSummary(
        vault_initialized=initialized,
        accounts=accounts,
        wallets=wallets,
        recovery_key_written=recovery_written,
    )


def main() -> int:
    """命令行入口：连接配置数据库并导入本地文件。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accounts-file", type=Path)
    parser.add_argument("--private-keys-file", type=Path)
    parser.add_argument("--vault-password")
    parser.add_argument("--vault-password-env", default="WORKER_VAULT_PASSWORD")
    parser.add_argument("--init-vault", action="store_true")
    parser.add_argument("--recovery-key-output", type=Path)
    parser.add_argument("--wallet-label-prefix", default="operator-wallet")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.accounts_file is None and args.private_keys_file is None:
        print("导入失败: 至少提供一个输入文件", file=sys.stderr)
        return 1

    try:
        settings = get_settings()
        password = _password_from_args(
            args.vault_password,
            args.vault_password_env,
            settings.worker_vault_password,
        )
        engine = build_engine(settings)
        with session_scope(engine) as session:
            summary = run_import(
                session,
                vault_password=password,
                accounts_file=args.accounts_file,
                private_keys_file=args.private_keys_file,
                initialize_vault=args.init_vault,
                recovery_key_output=args.recovery_key_output,
                dry_run=args.dry_run,
                wallet_label_prefix=args.wallet_label_prefix,
            )
            print(summary.to_json())
        engine.dispose()
    except (OSError, ValueError, VaultUnlockError, WalletInputError) as error:
        print(f"导入失败: {type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
