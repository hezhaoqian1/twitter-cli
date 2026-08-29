from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.models.wallets import Wallet, WalletSecret, WalletSourceType
from manager_api.schemas.wallets import WalletImportRequest
from manager_api.services.vault import VaultService
from manager_api.services.wallets import (
    DEFAULT_DERIVATION_PATH,
    WalletImportStatus,
    WalletInputError,
    WalletService,
)


MNEMONIC_FIXTURE = "test test test test test test test test test test test junk"
PRIVATE_KEY_FIXTURE = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
SECOND_PRIVATE_KEY_FIXTURE = "59c6995e998f97a5a0044966f094538b292d4874067f2f3f2d8f3f3e8f5f7f8f"


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _unlocked_vault(session: Session) -> VaultService:
    vault = VaultService(session)
    vault.initialize("wallet-manager-password-fixture")
    return vault


def test_mnemonic_derivation_matches_metamask_path(session: Session) -> None:
    preview = WalletService(session).preview(
        WalletSourceType.MNEMONIC,
        MNEMONIC_FIXTURE,
        start_index=0,
        count=2,
    )

    assert [decision.status for decision in preview.decisions] == [
        WalletImportStatus.VALID,
        WalletImportStatus.VALID,
    ]
    assert preview.decisions[0].candidate.address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    assert preview.decisions[0].candidate.derivation_path == DEFAULT_DERIVATION_PATH.format(index=0)
    assert preview.decisions[1].candidate.address == "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"


def test_private_key_validation_and_secretstr_request_contract(session: Session) -> None:
    with pytest.raises(WalletInputError):
        WalletService(session).preview(WalletSourceType.PRIVATE_KEY, "not-a-key")

    request = WalletImportRequest(
        source_type=WalletSourceType.PRIVATE_KEY,
        secret="0x" + PRIVATE_KEY_FIXTURE.upper(),
    )
    preview = WalletService(session).preview(
        request.source_type,
        request.secret.get_secret_value(),
    )
    assert preview.decisions[0].candidate.address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    assert "secret" in request.model_fields
    assert request.secret.__class__.__name__ == "SecretStr"


def test_commit_encrypts_source_and_each_private_key(session: Session) -> None:
    vault = _unlocked_vault(session)
    source, preview = WalletService(session, vault).commit(
        WalletSourceType.MNEMONIC,
        MNEMONIC_FIXTURE,
        label="fixture mnemonic",
        start_index=0,
        count=2,
    )
    session.commit()

    assert source is not None
    assert [decision.status for decision in preview.decisions] == [
        WalletImportStatus.COMMITTED,
        WalletImportStatus.COMMITTED,
    ]
    assert session.query(Wallet).count() == 2
    assert session.query(WalletSecret).count() == 2
    assert MNEMONIC_FIXTURE.encode() not in source.encrypted_source_ref
    assert (
        vault.decrypt_field(
            "wallet_sources",
            source.id,
            "source_material",
            source.encrypted_source_ref,
        ).decode()
        == MNEMONIC_FIXTURE
    )
    for wallet_secret in session.query(WalletSecret).all():
        assert "private_key" not in json.loads(wallet_secret.redacted_metadata)
        assert wallet_secret.envelope != b"pending"


def test_duplicate_normalized_address_is_reported_and_skipped(session: Session) -> None:
    vault = _unlocked_vault(session)
    service = WalletService(session, vault)
    service.commit(WalletSourceType.PRIVATE_KEY, PRIVATE_KEY_FIXTURE)
    session.commit()

    preview = service.preview(
        WalletSourceType.PRIVATE_KEY,
        "0x" + PRIVATE_KEY_FIXTURE.upper(),
    )
    assert preview.decisions[0].status is WalletImportStatus.DUPLICATE_EXISTING
    source, committed_preview = service.commit(
        WalletSourceType.PRIVATE_KEY,
        "0x" + PRIVATE_KEY_FIXTURE.upper(),
    )
    assert source is None
    assert committed_preview.decisions[0].status is WalletImportStatus.DUPLICATE_EXISTING
    assert session.query(Wallet).count() == 1


def test_private_key_batch_preview_and_commit_encrypt_multiple_rows(session: Session) -> None:
    """批量私钥导入支持一行一个，并只提交有效去重地址。"""
    vault = _unlocked_vault(session)
    service = WalletService(session, vault)
    content = "\n".join(
        [
            "# operator note",
            PRIVATE_KEY_FIXTURE,
            "0x" + SECOND_PRIVATE_KEY_FIXTURE.upper(),
            "0x" + PRIVATE_KEY_FIXTURE.upper(),
        ]
    )

    preview = service.preview_private_keys(content, label_prefix="batch-wallet")
    _source, committed = service.commit_private_keys(content, label_prefix="batch-wallet")
    session.commit()

    assert [decision.status for decision in preview.decisions] == [
        WalletImportStatus.VALID,
        WalletImportStatus.VALID,
        WalletImportStatus.DUPLICATE_IN_FILE,
    ]
    assert [decision.status for decision in committed.decisions] == [
        WalletImportStatus.COMMITTED,
        WalletImportStatus.COMMITTED,
        WalletImportStatus.DUPLICATE_IN_FILE,
    ]
    assert committed.summary(committed=2)["committed"] == 2
    assert session.query(Wallet).count() == 2
    assert session.query(WalletSecret).count() == 2
    for secret in session.query(WalletSecret).all():
        assert PRIVATE_KEY_FIXTURE.encode() not in secret.envelope
        assert SECOND_PRIVATE_KEY_FIXTURE.encode() not in secret.envelope


def test_private_key_batch_reports_invalid_line_number(session: Session) -> None:
    """批量私钥格式错误时报告行号，避免操作者猜是哪一行。"""
    service = WalletService(session)

    with pytest.raises(WalletInputError) as error:
        service.preview_private_keys(f"{PRIVATE_KEY_FIXTURE}\nnot-a-key")

    assert "line 2" in str(error.value)


def test_invalid_mnemonic_and_derivation_range_are_rejected(session: Session) -> None:
    service = WalletService(session)
    with pytest.raises(WalletInputError):
        service.preview(WalletSourceType.MNEMONIC, "one two three")
    with pytest.raises(WalletInputError):
        service.preview(
            WalletSourceType.MNEMONIC,
            MNEMONIC_FIXTURE,
            start_index=2**31,
        )
