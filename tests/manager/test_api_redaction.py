from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import SecretStr
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.api.dependencies import get_db
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.schemas.accounts import AccountImportRequest
from manager_api.schemas.vault import VaultInitializeRequest, VaultPasswordRequest
from manager_api.services.vault import VaultRuntime, VaultService


def test_import_api_never_echoes_tsv_secrets_or_ciphertext() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        vault = VaultService(session)
        vault.initialize("manager-password-fixture")
        session.commit()

    def override_get_db():
        with Session(engine) as session:
            yield session

    from manager_api.config import ManagerSettings

    settings = ManagerSettings(
        database_url="sqlite+pysqlite:///:memory:",
        redis_url="redis://localhost/0",
        session_secret="test-session-secret-123",
    )
    app = create_app(settings)
    app.dependency_overrides[get_db] = override_get_db
    assert "/api/imports/accounts/preview" in app.openapi()["paths"]
    assert "/api/imports/accounts/commit" in app.openapi()["paths"]

    raw = "\t".join(
        (
            "ApiFixture",
            "password-fixture",
            "TOTP-FIXTURE",
            "sample@example.test",
            "mail-fixture",
            "token-fixture",
            "cookie-fixture",
        )
    )
    from manager_api.api.routers.imports import preview_accounts

    with next(override_get_db()) as request_session:
        response = preview_accounts(
            AccountImportRequest(content=SecretStr(raw), source_name="fixture.tsv"),
            session=request_session,
        )

    body = response.model_dump(mode="json")
    rendered = response.model_dump_json()
    assert "password-fixture" not in rendered
    assert "TOTP-FIXTURE" not in rendered
    assert "token-fixture" not in rendered
    assert "cookie-fixture" not in rendered
    assert body["rows"][0]["handle_masked"] == "Ap***e"
    assert body["rows"][0]["status"] == "valid"
    assert "content" not in body


def test_account_list_contract_has_no_secret_fields() -> None:
    from manager_api.schemas.accounts import AccountListItem

    fields = set(AccountListItem.model_fields)
    assert fields == {
        "id",
        "handle",
        "email_masked",
        "state",
        "health",
        "has_secret",
    }
    assert UUID


def test_vault_http_lifecycle_and_account_commit_share_runtime() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    runtime = VaultRuntime(cache_ttl_seconds=60)

    from manager_api.api.routers.imports import commit_accounts
    from manager_api.api.routers.vault import initialize_vault, lock_vault, unlock_vault_with_password

    with Session(engine) as session:
        initialized = initialize_vault(
            VaultInitializeRequest(password=SecretStr("manager-password-fixture")),
            session=session,
            runtime=runtime,
        )
        session.commit()

    assert initialized.initialized is True
    assert initialized.recovery_key

    with Session(engine) as session:
        status = unlock_vault_with_password(
            VaultPasswordRequest(password=SecretStr("manager-password-fixture")),
            session=session,
            runtime=runtime,
        )
        raw = "\t".join(
            (
                "HttpFixture",
                "password-fixture",
                "TOTP-FIXTURE",
                "sample@example.test",
                "mail-fixture",
                "token-fixture",
                "cookie-fixture",
            )
        )
        response = commit_accounts(
            AccountImportRequest(content=SecretStr(raw), source_name="http-fixture.tsv"),
            session=session,
            vault=VaultService(session, runtime=runtime),
        )
        session.commit()

    assert status.unlocked is True
    assert response.summary.committed_rows == 1

    with Session(engine) as session:
        locked = lock_vault(session=session, runtime=runtime)
        assert locked.unlocked is False
        locked_raw = raw.replace("HttpFixture", "LockedHttpFixture")
        with pytest.raises(HTTPException) as locked_error:
            commit_accounts(
                AccountImportRequest(content=SecretStr(locked_raw), source_name="locked.tsv"),
                session=session,
                vault=VaultService(session, runtime=runtime),
            )
        assert locked_error.value.status_code == 423
        assert locked_error.value.detail == "vault is locked"


def test_wallet_http_flow_uses_shared_vault_and_redacts_secret_material() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    from manager_api.api.routers.vault import initialize_vault, lock_vault
    from manager_api.api.routers.wallets import (
        commit_private_key_batch,
        commit_wallet_source,
        derive_wallet_source,
        list_wallets,
        preview_private_key_batch,
    )
    from manager_api.config import ManagerSettings
    from manager_api.schemas.vault import VaultInitializeRequest
    from manager_api.schemas.wallets import (
        WalletDeriveRequest,
        WalletImportRequest,
        WalletPrivateKeysRequest,
    )

    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    runtime = app.state.vault_runtime
    assert "/api/wallet-sources" in app.openapi()["paths"]
    assert "/api/wallet-sources/private-keys" in app.openapi()["paths"]
    assert "/api/wallets" in app.openapi()["paths"]

    with Session(engine) as session:
        initialized = initialize_vault(
            VaultInitializeRequest(password=SecretStr("manager-password-fixture")),
            session=session,
            runtime=runtime,
        )
        session.commit()
    assert initialized.initialized is True
    assert initialized.recovery_key

    mnemonic = "test test test test test test test test test test test junk"
    with Session(engine) as session:
        vault = VaultService(session, runtime=runtime)
        commit = commit_wallet_source(
            WalletImportRequest(
                source_type="mnemonic",
                secret=mnemonic,
                label="api-fixture",
                start_index=0,
                count=2,
            ),
            session=session,
            vault=vault,
        )
        session.commit()
        assert commit.summary.committed == 2
        assert commit.wallets[0].address == "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
        assert mnemonic not in commit.model_dump_json()
        source_id = commit.source_id
    assert source_id is not None

    private_key_a = "59c6995e998f97a5a0044966f094538b292d4874067f2f3f2d8f3f3e8f5f7f8f"
    private_key_b = "5de4111afa1a4b6ea2ba7c5d3c587a1efde46fb7f8d8eb21d6cd7b3e7f09a1b1"
    private_key_content = f"{private_key_a}\n0x{private_key_b.upper()}\n"
    with Session(engine) as session:
        vault = VaultService(session, runtime=runtime)
        preview_private = preview_private_key_batch(
            WalletPrivateKeysRequest(
                content=SecretStr(private_key_content),
                label_prefix="api-private",
            ),
            session=session,
        )
        committed_private = commit_private_key_batch(
            WalletPrivateKeysRequest(
                content=SecretStr(private_key_content),
                label_prefix="api-private",
            ),
            session=session,
            vault=vault,
        )
        session.commit()
        assert preview_private.summary.valid == 2
        assert committed_private.summary.committed == 2
        assert committed_private.source_id is None
        assert private_key_a not in committed_private.model_dump_json()
        assert private_key_b not in committed_private.model_dump_json()

    with Session(engine) as session:
        vault = VaultService(session, runtime=runtime)
        derived = derive_wallet_source(
            source_id,
            WalletDeriveRequest(start_index=2, count=1),
            session=session,
            vault=vault,
        )
        session.commit()
        assert derived.summary.committed == 1

        listed = list_wallets(offset=0, limit=50, session=session)
        assert listed.total == 5
        assert len(listed.items) == 5
    assert {
        "id",
        "address",
        "source_type",
        "derivation_path",
        "derivation_index",
        "state",
        "has_secret",
        "is_bound",
    } == set(listed.items[0].model_dump())
    assert mnemonic not in listed.model_dump_json()

    with Session(engine) as session:
        locked = lock_vault(session=session, runtime=runtime)
        assert locked.unlocked is False
        with pytest.raises(HTTPException) as rejected:
            commit_wallet_source(
                WalletImportRequest(source_type="private_key", secret="00" * 31 + "01"),
                session=session,
                vault=VaultService(session, runtime=runtime),
            )
        assert rejected.value.status_code == 423
        assert rejected.value.detail == "vault is locked"
