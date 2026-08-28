from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.bindings import AccountWalletBinding, BindingState
from manager_api.models.tasks import ResourceLease, TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.services.bindings import BindingConflictError, BindingService


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _resources(session: Session) -> tuple[SocialAccount, Wallet, Wallet]:
    account = SocialAccount(
        handle="binding-account",
        normalized_handle="binding-account",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet_a = Wallet(address="0x" + "1" * 40, normalized_address="0x" + "1" * 40, state="active")
    wallet_b = Wallet(address="0x" + "2" * 40, normalized_address="0x" + "2" * 40, state="active")
    session.add_all([account, wallet_a, wallet_b])
    session.flush()
    return account, wallet_a, wallet_b


def test_binding_confirm_is_immutable_and_archive_preserves_history(session: Session) -> None:
    account, wallet, _ = _resources(session)
    service = BindingService(session)

    pending = service.create_pending(account.id, wallet.id, binding_key="fixture-binding")
    assert pending.binding.state is BindingState.PENDING

    confirmed = service.confirm(pending.binding.id, "external-fixture-1")
    assert confirmed.binding.state is BindingState.BOUND
    assert confirmed.binding.bound_at is not None
    assert service.confirm(pending.binding.id, "external-fixture-1").binding.id == pending.binding.id

    with pytest.raises(BindingConflictError, match="confirmed binding cannot be changed"):
        service.confirm(pending.binding.id, "external-fixture-2")

    archived = service.archive(pending.binding.id)
    assert archived.binding.state is BindingState.ARCHIVED
    assert archived.binding.archived_at is not None
    assert session.get(AccountWalletBinding, pending.binding.id) is not None


def test_active_binding_and_bound_history_block_reassignment(session: Session) -> None:
    account, wallet_a, wallet_b = _resources(session)
    service = BindingService(session)
    first = service.create_pending(account.id, wallet_a.id)

    with pytest.raises(BindingConflictError) as pending_error:
        service.create_pending(account.id, wallet_b.id)
    assert pending_error.value.code == "binding_in_progress"

    service.confirm(first.binding.id, "external-fixture")
    service.archive(first.binding.id)
    with pytest.raises(BindingConflictError) as history_error:
        service.create_pending(account.id, wallet_b.id)
    assert history_error.value.code == "already_bound"


def test_archived_resource_and_active_lease_have_distinct_conflicts(session: Session) -> None:
    account, wallet_a, wallet_b = _resources(session)
    account.archived_at = utc_now()
    account.state = LifecycleState.ARCHIVED
    with pytest.raises(BindingConflictError) as archived_error:
        BindingService(session).create_pending(account.id, wallet_a.id)
    assert archived_error.value.code == "archived_account"

    account.archived_at = None
    account.state = LifecycleState.ACTIVE
    job = TaskJob(
        kind=TaskKind.BIND,
        state=TaskState.RUNNING,
        attempt=1,
        priority=0,
        idempotency_key="bind:lease-fixture",
        lease_keys=[],
        scheduled_at=utc_now(),
        external_target="kredo:bind",
    )
    session.add(job)
    session.flush()
    session.add(
        ResourceLease(
            lease_key=f"account:{account.id}",
            task_job_id=job.id,
            owner_token="owner-fixture",
            acquired_at=utc_now(),
            expires_at=utc_now() + timedelta(minutes=5),
        )
    )
    session.flush()
    with pytest.raises(BindingConflictError) as lease_error:
        BindingService(session).create_pending(account.id, wallet_b.id)
    assert lease_error.value.code == "resource_leased"


def test_binding_api_is_registered_and_list_is_redacted(session: Session) -> None:
    from manager_api.api.routers.bindings import create_binding, list_bindings
    from manager_api.main import create_app
    from manager_api.schemas.bindings import BindingCreateRequest
    from manager_api.config import ManagerSettings

    account, wallet, _ = _resources(session)
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/bindings" in app.openapi()["paths"]
    assert "/api/balances" in app.openapi()["paths"]
    created = create_binding(
        BindingCreateRequest(social_account_id=account.id, wallet_id=wallet.id),
        session=session,
    )
    listed = list_bindings(offset=0, limit=50, session=session)

    assert created.state == "pending"
    assert listed.total == 1
    assert listed.items[0].account_handle == "binding-account"
    assert "private_key" not in listed.model_dump_json()
