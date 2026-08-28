from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typing import Generator

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.tasks import ResourceLease, TaskEvent, TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.scheduler import Scheduler
from manager_api.services.tasks import TaskService
from manager_api.worker import TaskWorker, WorkerOutcome


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _queued(session: Session, suffix: str = "7"):
    account = SocialAccount(
        handle=f"worker-account-{suffix}",
        normalized_handle=f"worker-account-{suffix}",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet = Wallet(
        address="0x" + suffix * 40,
        normalized_address="0x" + suffix * 40,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
    return TaskService(session).create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target=f"worker-target-{suffix}",
    ).job


def test_worker_success_releases_leases(session: Session) -> None:
    _queued(session)
    scheduler = Scheduler(session, lease_ttl_seconds=60)
    grants = scheduler.dispatch_once(limit=1)
    assert len(grants) == 1

    result = TaskWorker(session, scheduler=scheduler).run_one(
        grants[0],
        lambda _: WorkerOutcome(state=TaskState.SUCCEEDED, summary="fixture complete"),
    )
    assert result.state is TaskState.SUCCEEDED
    assert session.query(ResourceLease).count() == 0
    assert [event.to_state for event in result.events] == [
        TaskState.QUEUED.value,
        TaskState.LEASED.value,
        TaskState.RUNNING.value,
        TaskState.SUCCEEDED.value,
    ]


def test_worker_exception_is_redacted_and_marks_failure(session: Session) -> None:
    _queued(session, "8")
    scheduler = Scheduler(session, lease_ttl_seconds=60)
    grant = scheduler.dispatch_once(limit=1)[0]

    def failing_handler(_):
        raise RuntimeError("secret provider response")

    result = TaskWorker(session, scheduler=scheduler).run_one(grant, failing_handler)
    assert result.state is TaskState.FAILED
    assert result.failure_code == "worker_exception"
    assert "secret provider response" not in (result.events[-1].summary or "")
    assert session.query(ResourceLease).count() == 0


def test_running_worker_honors_cancellation_request(session: Session) -> None:
    """A cancellation raised while the handler is active wins over its outcome."""
    _queued(session, "cancel")
    scheduler = Scheduler(session, lease_ttl_seconds=60)
    grant = scheduler.dispatch_once(limit=1)[0]

    def cancelling_handler(current_job):
        TaskService(session).cancel(current_job.id)
        return WorkerOutcome(state=TaskState.SUCCEEDED, summary="late success")

    result = TaskWorker(session, scheduler=scheduler).run_one(grant, cancelling_handler)

    assert result.state is TaskState.CANCELLED
    assert result.cancel_requested_at is not None
    assert result.events[-1].to_state == TaskState.CANCELLED.value


def test_recovery_requeues_expired_running_job_and_records_event(session: Session) -> None:
    job = _queued(session, "9")
    scheduler = Scheduler(session, lease_ttl_seconds=60)
    grant = scheduler.dispatch_once(limit=1)[0]
    TaskService(session).transition(job.id, TaskState.RUNNING, owner_token=grant.owner_token)
    for lease in session.query(ResourceLease).filter(ResourceLease.task_job_id == job.id):
        lease.expires_at = utc_now() - timedelta(seconds=1)
    session.flush()

    recovered = TaskWorker(session, scheduler=scheduler).recover_expired()
    assert recovered == [job.id]
    assert job.state is TaskState.QUEUED
    assert job.failure_code == "lease_expired"
    assert session.query(ResourceLease).count() == 0
    assert session.query(TaskEvent).filter(
        TaskEvent.task_job_id == job.id,
        TaskEvent.event_type == "lease_expired_recovery",
    ).count() == 1
