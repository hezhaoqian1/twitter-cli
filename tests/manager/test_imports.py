from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.models.accounts import AccountSecret, SocialAccount
from manager_api.models.imports import ImportRowStatus
from manager_api.services.imports import AccountImportService
from manager_api.services.vault import VaultService


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _row(
    handle: str = "SampleUser",
    password: str = "password-fixture",
    totp: str = "TOTP-FIXTURE",
    email: str = "sample@example.test",
    email_password: str = "mail-fixture",
    token: str = "token-fixture",
    cookie: str = "cookie-fixture",
) -> str:
    return "\t".join((handle, password, totp, email, email_password, token, cookie))


def test_preview_uses_seven_columns_and_preserves_line_numbers(session: Session) -> None:
    service = AccountImportService(session)
    preview = service.preview(f"{_row()}\nmalformed\trow\n")

    assert preview.total_rows == 2
    assert [decision.line_number for decision in preview.decisions] == [1, 2]
    assert preview.decisions[0].status is ImportRowStatus.VALID
    assert preview.decisions[1].status is ImportRowStatus.MALFORMED
    assert preview.decisions[1].diagnostic_code == "invalid_column_count_or_empty_field"
    assert preview.decisions[0].handle_masked == "Sa***r"
    assert preview.decisions[0].email_masked == "s***@example.test"


def test_preview_classifies_duplicate_existing_and_conflicting_rows(session: Session) -> None:
    vault = VaultService(session)
    vault.initialize("manager-password-fixture")
    service = AccountImportService(session, vault)
    first = _row()
    session.commit()
    preview = service.preview(f"{first}\n{first}\n")
    assert [decision.status for decision in preview.decisions] == [
        ImportRowStatus.VALID,
        ImportRowStatus.DUPLICATE_IN_FILE,
    ]

    batch, _ = service.commit(first)
    session.commit()
    assert batch.committed_rows == 1

    same_preview = service.preview(first)
    assert same_preview.decisions[0].status is ImportRowStatus.EXISTING_ACCOUNT

    conflicting = _row(password="different-fixture")
    conflict_preview = service.preview(conflicting)
    assert conflict_preview.decisions[0].status is ImportRowStatus.CONFLICTING_SESSION


def test_commit_encrypts_all_sensitive_fields_and_records_each_row(session: Session) -> None:
    vault = VaultService(session)
    vault.initialize("manager-password-fixture")
    service = AccountImportService(session, vault)
    raw = _row()

    batch, preview = service.commit(raw, source_name="fixture.tsv")
    session.commit()

    assert batch.total_rows == 1
    assert batch.committed_rows == 1
    assert batch.skipped_rows == 0
    assert preview.decisions[0].status is ImportRowStatus.VALID

    account = session.query(SocialAccount).one()
    secret = session.query(AccountSecret).one()
    assert raw.encode("utf-8") not in secret.envelope
    envelope = json.loads(secret.envelope.decode("utf-8"))
    assert set(envelope) == {"cookie", "email_password", "password", "token", "totp"}
    decrypted = {
        field: vault.decrypt_field(
            "account_secrets",
            secret.id,
            field,
            __import__("base64").urlsafe_b64decode(value),
        ).decode("utf-8")
        for field, value in envelope.items()
    }
    assert decrypted == {
        "cookie": "cookie-fixture",
        "email_password": "mail-fixture",
        "password": "password-fixture",
        "token": "token-fixture",
        "totp": "TOTP-FIXTURE",
    }
    assert account.email_masked == "s***@example.test"
