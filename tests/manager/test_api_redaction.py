from __future__ import annotations

from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.api.dependencies import get_db
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.schemas.accounts import AccountImportRequest
from manager_api.services.vault import VaultService


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
            AccountImportRequest(content=raw, source_name="fixture.tsv"),
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
