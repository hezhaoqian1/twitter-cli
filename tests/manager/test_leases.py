from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from typing import Generator

from manager_api.db.base import Base, utc_now
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.tasks import TaskBatch, TaskJob, TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.repositories.leases import LeaseRepository
from manager_api.scheduler import Scheduler
from manager_api.services.tasks import TaskService


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _job(session: Session, suffix: str) -> TaskJob:
    account = SocialAccount(
        handle=f"lease-account-{suffix}",
        normalized_handle=f"lease-account-{suffix}",
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
        external_target=f"target-{suffix}",
    ).job


def test_acquire_is_all_or_nothing_and_release_requires_owner(session: Session) -> None:
    job = _job(session, "1")
    leases = LeaseRepository(session)
    first = leases.acquire(job, ttl_seconds=60, owner_token="owner-1")
    assert first is not None
    assert set(first.lease_keys) == set(job.lease_keys)

    second = leases.acquire(job, ttl_seconds=60, owner_token="owner-2")
    assert second is None
    assert leases.release("wrong-owner", task_job_id=job.id) == 0
    assert leases.release("owner-1", task_job_id=job.id) == 2

    third = leases.acquire(job, ttl_seconds=60, owner_token="owner-3")
    assert third is not None


def test_scheduler_round_robins_batches_and_skips_blocked_resources(session: Session) -> None:
    first = _job(session, "2")
    second = _job(session, "3")
    third = _job(session, "4")
    batch_a = TaskBatch(name="batch-a", kind=TaskKind.BIND, dispatch_limit=10, state="active")
    batch_b = TaskBatch(name="batch-b", kind=TaskKind.BIND, dispatch_limit=10, state="active")
    session.add_all([batch_a, batch_b])
    session.flush()
    first.task_batch_id = batch_a.id
    second.task_batch_id = batch_a.id
    third.task_batch_id = batch_b.id
    session.flush()

    grants = Scheduler(
        session,
        lease_ttl_seconds=60,
        worker_concurrency=3,
        browser_concurrency=3,
    ).dispatch_once(limit=3)
    assert [grant.task_job_id for grant in grants] == [first.id, third.id, second.id]
    leased_states = []
    for grant in grants:
        leased_job = session.get(TaskJob, grant.task_job_id)
        assert leased_job is not None
        leased_states.append(leased_job.state)
    assert leased_states == [TaskState.LEASED, TaskState.LEASED, TaskState.LEASED]

    blocked = _job(session, "5")
    blocked.lease_keys = list(first.lease_keys)
    session.flush()
    more = Scheduler(session, lease_ttl_seconds=60).dispatch_once(limit=1)
    assert more == []


def test_expired_lease_is_replaced_before_acquisition(session: Session) -> None:
    job = _job(session, "6")
    now = utc_now()
    leases = LeaseRepository(session)
    old = leases.acquire(job, ttl_seconds=-1, owner_token="expired-owner", now=now)
    assert old is not None

    replacement = leases.acquire(job, ttl_seconds=60, owner_token="fresh-owner", now=now)
    assert replacement is not None
    assert replacement.owner_token == "fresh-owner"
    assert replacement.expires_at > now + timedelta(seconds=59)
