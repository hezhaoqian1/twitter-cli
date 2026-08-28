from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.tasks import ResourceLease, TaskEvent, TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.services.tasks import TaskConflictError, TaskService


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _resources(session: Session) -> tuple[SocialAccount, Wallet]:
    account = SocialAccount(
        handle="task-account",
        normalized_handle="task-account",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet = Wallet(address="0x" + "3" * 40, normalized_address="0x" + "3" * 40, state="active")
    session.add_all([account, wallet])
    session.flush()
    return account, wallet


def test_create_is_idempotent_and_event_is_append_only(session: Session) -> None:
    account, wallet = _resources(session)
    service = TaskService(session)

    first = service.create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="target-fixture",
    )
    second = service.create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="target-fixture",
    )

    assert first.reused is False
    assert second.reused is True
    assert first.job.id == second.job.id
    assert len(first.job.events) == 1
    assert first.job.events[0].to_state == TaskState.QUEUED.value


def test_state_transitions_write_events_and_retry_same_job(session: Session) -> None:
    account, wallet = _resources(session)
    service = TaskService(session)
    created = service.create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="transition-target",
    )
    job = created.job

    service.transition(job.id, TaskState.LEASED, summary="lease acquired")
    owner_token = "owner-fixture"
    session.add_all(
        [
            ResourceLease(
                lease_key=f"account:{account.id}",
                task_job_id=job.id,
                owner_token=owner_token,
                acquired_at=utc_now(),
                expires_at=utc_now() + timedelta(minutes=5),
            ),
            ResourceLease(
                lease_key=f"wallet:{wallet.id}",
                task_job_id=job.id,
                owner_token=owner_token,
                acquired_at=utc_now(),
                expires_at=utc_now() + timedelta(minutes=5),
            ),
        ]
    )
    session.flush()
    service.transition(job.id, TaskState.RUNNING, owner_token=owner_token)
    service.transition(job.id, TaskState.FAILED, summary="provider rejected", failure_code="provider_rejected")
    retried = service.retry(job.id)

    assert retried.state is TaskState.QUEUED
    assert retried.attempt == 1
    assert retried.failure_code is None
    events = session.query(TaskEvent).filter(TaskEvent.task_job_id == job.id).order_by(TaskEvent.sequence).all()
    assert [event.to_state for event in events] == [
        TaskState.QUEUED.value,
        TaskState.LEASED.value,
        TaskState.RUNNING.value,
        TaskState.FAILED.value,
        TaskState.QUEUED.value,
    ]


def test_state_specific_commands_reject_invalid_states(session: Session) -> None:
    account, wallet = _resources(session)
    service = TaskService(session)
    created = service.create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="command-target",
    )
    paused = service.pause(created.job.id)
    assert paused.state is TaskState.PAUSED
    cancelled = service.cancel(paused.id)
    assert cancelled.state is TaskState.CANCELLED

    with pytest.raises(TaskConflictError) as pause_error:
        service.pause(cancelled.id)
    assert pause_error.value.code == "invalid_transition"


def test_running_requires_all_active_owned_leases(session: Session) -> None:
    account, wallet = _resources(session)
    service = TaskService(session)
    created = service.create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="lease-target",
    )
    service.transition(created.job.id, TaskState.LEASED)

    with pytest.raises(TaskConflictError) as missing_error:
        service.transition(created.job.id, TaskState.RUNNING, owner_token="owner-fixture")
    assert missing_error.value.code == "lease_not_owned"

    with pytest.raises(TaskConflictError) as token_error:
        service.transition(created.job.id, TaskState.RUNNING)
    assert token_error.value.code == "lease_required"


def test_waiting_poll_requeues_and_duplicate_target_is_hashed(session: Session) -> None:
    account, wallet = _resources(session)
    service = TaskService(session)
    created = service.create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="https://provider.example/secret-target",
    )
    assert "provider.example" not in created.job.idempotency_key
    service.transition(created.job.id, TaskState.LEASED)
    owner_token = "owner-fixture"
    session.add_all(
        [
            ResourceLease(
                lease_key=f"account:{account.id}",
                task_job_id=created.job.id,
                owner_token=owner_token,
                acquired_at=utc_now(),
                expires_at=utc_now() + timedelta(minutes=5),
            ),
            ResourceLease(
                lease_key=f"wallet:{wallet.id}",
                task_job_id=created.job.id,
                owner_token=owner_token,
                acquired_at=utc_now(),
                expires_at=utc_now() + timedelta(minutes=5),
            ),
        ]
    )
    session.flush()
    service.transition(
        created.job.id,
        TaskState.RUNNING,
        owner_token=owner_token,
    )
    service.transition(created.job.id, TaskState.WAITING_EXTERNAL_VALIDATION, summary="awaiting provider")
    requeued = service.poll(created.job.id)
    assert requeued.state is TaskState.QUEUED
