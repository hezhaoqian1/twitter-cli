from __future__ import annotations

import base64
import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.models.accounts import AccountSecret, SocialAccount
from manager_api.services.backup import (
    BackupError,
    BackupFormatError,
    create_backup,
    restore_backup,
    verify_backup,
)
from manager_api.services.vault import VaultService


PASSWORD = "backup-manager-password"
def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _source_fixture() -> tuple[object, str, bytes]:
    engine = _engine()
    with Session(engine) as session:
        vault = VaultService(session)
        initialized = vault.initialize(PASSWORD)
        account = SocialAccount(
            handle="BackupFixture",
            normalized_handle="backupfixture",
            email_masked="b***@example.test",
        )
        session.add(account)
        session.flush()
        envelope = vault.encrypt_field("account_secrets", account.id, "password", "secret-value")
        session.add(
            AccountSecret(
                social_account_id=account.id,
                version=1,
                is_current=True,
                envelope=envelope,
                envelope_version=1,
                secret_fingerprint=vault.fingerprint("secret-value"),
            )
        )
        session.commit()
        package = create_backup(session, vault, initialized.recovery_key)
    return engine, initialized.recovery_key, package


def test_backup_round_trip_restores_and_decrypts_secret() -> None:
    source_engine, recovery_key, package = _source_fixture()
    target_engine = _engine()
    try:
        with Session(source_engine) as source:
            result = verify_backup(source, VaultService(source), package, recovery_key)
            assert result.table_count == len(Base.metadata.tables)
            assert result.row_count >= 3
            assert result.checksums_valid is True

        with Session(target_engine) as target:
            target_vault = VaultService(target)
            restored = restore_backup(target, target_vault, package, recovery_key)
            assert restored.vault_recovery_key_valid is True
            restored_account = target.scalar(
                select(SocialAccount).where(SocialAccount.normalized_handle == "backupfixture")
            )
            assert restored_account is not None
            secret = target.scalar(
                select(AccountSecret).where(AccountSecret.social_account_id == restored_account.id)
            )
            assert secret is not None
            target_vault.unlock_with_recovery_key(recovery_key)
            assert (
                target_vault.decrypt_field(
                    "account_secrets",
                    restored_account.id,
                    "password",
                    secret.envelope,
                )
                == b"secret-value"
            )
    finally:
        source_engine.dispose()
        target_engine.dispose()


def test_wrong_recovery_key_and_ciphertext_tampering_are_rejected() -> None:
    engine, recovery_key, package = _source_fixture()
    try:
        with Session(engine) as session:
            with pytest.raises(BackupFormatError, match="decryption failed"):
                verify_backup(session, VaultService(session), package, "wrong-key")

        outer = json.loads(package)
        envelope = outer["envelope"]
        ciphertext = bytearray(base64.urlsafe_b64decode(envelope["ciphertext"]))
        ciphertext[-1] ^= 1
        envelope["ciphertext"] = base64.urlsafe_b64encode(ciphertext).decode()
        tampered = json.dumps(outer, separators=(",", ":"), sort_keys=True).encode()
        with Session(engine) as session:
            with pytest.raises(BackupFormatError, match="checksum mismatch"):
                verify_backup(session, VaultService(session), tampered, recovery_key)
    finally:
        engine.dispose()


def test_manifest_tampering_and_non_empty_restore_are_rejected() -> None:
    engine, recovery_key, package = _source_fixture()
    target_engine = _engine()
    try:
        with Session(engine) as session:
            vault = VaultService(session)
            outer = json.loads(package)
            raw = vault.decrypt_backup_payload(outer["envelope"], recovery_key)
            payload = json.loads(raw)
            payload["manifest"]["social_accounts"]["row_count"] += 1
            replacement = vault.encrypt_backup_payload(
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(),
                recovery_key,
            )
            outer["envelope"] = replacement
            ciphertext = base64.urlsafe_b64decode(replacement["ciphertext"])
            outer["ciphertext_sha256"] = hashlib.sha256(ciphertext).hexdigest()
            tampered = json.dumps(outer, separators=(",", ":"), sort_keys=True).encode()
            with pytest.raises(BackupFormatError, match="manifest mismatch"):
                verify_backup(session, vault, tampered, recovery_key)

        with Session(target_engine) as target:
            target.add(
                SocialAccount(
                    handle="Existing",
                    normalized_handle="existing",
                    email_masked=None,
                )
            )
            target.flush()
            with pytest.raises(BackupError, match="target must be empty"):
                restore_backup(target, VaultService(target), package, recovery_key)
    finally:
        engine.dispose()
        target_engine.dispose()
