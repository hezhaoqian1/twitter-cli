from __future__ import annotations

import json
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.models.vault import VaultMetadata
from manager_api.schemas.vault import VaultPasswordRequest
from manager_api.services.vault import (
    VaultAlreadyInitializedError,
    VaultError,
    VaultService,
    VaultUnlockError,
)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def test_initialize_encrypts_and_unlocks_with_password_and_recovery_key(
    session: Session,
) -> None:
    service = VaultService(session)
    initialized = service.initialize("manager-password")
    record_id = uuid4()
    envelope = service.encrypt_field("account_secrets", record_id, "password", "value")

    assert len(initialized.recovery_key) >= 40
    assert service.decrypt_field("account_secrets", record_id, "password", envelope) == b"value"
    assert b"value" not in envelope
    assert session.scalar(session.query(VaultMetadata).statement) is not None

    service.lock()
    service.unlock_with_recovery_key(initialized.recovery_key)
    assert service.decrypt_field("account_secrets", record_id, "password", envelope) == b"value"

    service.lock()
    service.unlock_with_password("manager-password")
    assert service.is_unlocked is True


def test_wrong_password_and_missing_vault_share_external_error(session: Session) -> None:
    service = VaultService(session)

    with pytest.raises(VaultUnlockError, match="vault unlock failed") as missing:
        service.unlock_with_password("wrong")

    service.initialize("manager-password")
    service.lock()
    with pytest.raises(VaultUnlockError, match="vault unlock failed") as wrong:
        service.unlock_with_password("wrong")

    assert str(missing.value) == str(wrong.value)


def test_aad_prevents_cross_field_and_cross_record_decryption(session: Session) -> None:
    service = VaultService(session)
    service.initialize("manager-password")
    record_id = uuid4()
    envelope = service.encrypt_field("account_secrets", record_id, "token", "secret")

    with pytest.raises(VaultError, match="invalid vault envelope"):
        service.decrypt_field("account_secrets", record_id, "password", envelope)
    with pytest.raises(VaultError, match="invalid vault envelope"):
        service.decrypt_field("wallet_secrets", record_id, "token", envelope)
    with pytest.raises(VaultError, match="invalid vault envelope"):
        service.decrypt_field("account_secrets", uuid4(), "token", envelope)


def test_ttl_locks_key_and_reinitialization_is_rejected(session: Session) -> None:
    now = [100.0]
    service = VaultService(session, cache_ttl_seconds=5.0, clock=lambda: now[0])
    service.initialize("manager-password")
    record_id = uuid4()
    envelope = service.encrypt_field("account_secrets", record_id, "token", "secret")

    now[0] = 105.0
    with pytest.raises(VaultUnlockError, match="vault is locked"):
        service.decrypt_field("account_secrets", record_id, "token", envelope)

    with pytest.raises(VaultAlreadyInitializedError):
        service.initialize("another-password")


def test_schema_secret_str_does_not_expose_value_in_repr_or_dump() -> None:
    request = VaultPasswordRequest(password="manager-password")

    assert isinstance(request.password, SecretStr)
    assert "manager-password" not in repr(request)
    assert "manager-password" not in json.dumps(request.model_dump(mode="json"))
