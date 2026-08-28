from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding
from manager_api.models.tasks import TaskEvent
from manager_api.models.wallets import Wallet


def _engine():
    return create_engine("sqlite+pysqlite:///:memory:")


def test_metadata_contains_all_manager_tables() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)

    tables = set(inspect(engine).get_table_names())

    assert {
        "social_accounts",
        "account_secrets",
        "wallet_sources",
        "wallets",
        "wallet_secrets",
        "account_wallet_bindings",
        "import_batches",
        "import_rows",
        "task_batches",
        "task_jobs",
        "task_events",
        "resource_leases",
        "audit_logs",
        "vault_metadata",
    } <= tables


def test_active_binding_is_unique_per_account_and_wallet() -> None:
    engine = _engine()
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        account = SocialAccount(
            handle="account-a",
            normalized_handle="account-a",
            state=LifecycleState.ACTIVE,
            health=AccountHealth.UNKNOWN,
        )
        wallet_a = Wallet(address="0x" + "1" * 40, normalized_address="0x" + "1" * 40)
        wallet_b = Wallet(address="0x" + "2" * 40, normalized_address="0x" + "2" * 40)
        session.add_all([account, wallet_a, wallet_b])
        session.flush()
        session.add(
            AccountWalletBinding(
                social_account_id=account.id,
                wallet_id=wallet_a.id,
                binding_key="pair-a",
            )
        )
        session.commit()

        session.add(
            AccountWalletBinding(
                social_account_id=account.id,
                wallet_id=wallet_b.id,
                binding_key="pair-b",
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("active account binding uniqueness was not enforced")


def test_utc_now_is_timezone_aware() -> None:
    timestamp = utc_now()

    assert timestamp.tzinfo is not None
    assert timestamp.utcoffset() == datetime.now(timezone.utc).utcoffset()


def test_event_metadata_uses_metadata_column_without_reserved_attribute() -> None:
    column = TaskEvent.__table__.c.metadata

    assert column.name == "metadata"
    assert TaskEvent.event_metadata.property.columns[0] is column
