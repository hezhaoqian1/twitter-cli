from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.api.routers.tasks import (
    cancel_task_batch,
    create_task_batch,
    list_task_batches,
    pause_task_batch,
    resume_task_batch,
)
from manager_api.db.base import Base
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.tasks import ResourceLease, TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.scheduler import Scheduler
from manager_api.schemas.tasks import TaskBatchCreateRequest, TaskBatchItemRequest


def _resources(session: Session, suffix: str) -> tuple[SocialAccount, Wallet]:
    """Create public-only task fixtures with non-overlapping lease identities."""
    account = SocialAccount(
        handle=f"batch-account-{suffix}",
        normalized_handle=f"batch-account-{suffix}",
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
    return account, wallet


def test_create_batch_groups_new_jobs_and_keeps_duplicate_job_in_place() -> None:
    """A duplicated item returns its original job instead of moving it between batches."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first_account, first_wallet = _resources(session, "1")
        second_account, second_wallet = _resources(session, "2")
        request = TaskBatchCreateRequest(
            name="First 10 bindings",
            kind=TaskKind.BIND,
            dispatch_limit=10,
            items=[
                TaskBatchItemRequest(
                    social_account_id=first_account.id,
                    wallet_id=first_wallet.id,
                    external_target="bind-fixture-1",
                ),
                TaskBatchItemRequest(
                    social_account_id=second_account.id,
                    wallet_id=second_wallet.id,
                    external_target="bind-fixture-2",
                ),
            ],
        )
        first = create_task_batch(request, session=session)
        duplicate = create_task_batch(
            TaskBatchCreateRequest(
                name="Repeat selection",
                kind=TaskKind.BIND,
                items=[
                    TaskBatchItemRequest(
                        social_account_id=first_account.id,
                        wallet_id=first_wallet.id,
                        external_target="bind-fixture-1",
                    )
                ],
            ),
            session=session,
        )
        listed = list_task_batches(offset=0, limit=50, session=session)

        assert len(first.jobs) == 2
        assert first.dispatch_limit == 10
        assert duplicate.jobs == []
        assert [batch.name for batch in listed.items] == ["Repeat selection", "First 10 bindings"]
        assert len(listed.items[0].jobs) == 0
        assert len(listed.items[1].jobs) == 2


def test_batch_pause_resume_and_cancel_control_dispatch() -> None:
    """A batch command controls every queued child and scheduler eligibility."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        first_account, first_wallet = _resources(session, "3")
        second_account, second_wallet = _resources(session, "4")
        batch = create_task_batch(
            TaskBatchCreateRequest(
                name="Pauseable workflow",
                kind=TaskKind.BIND,
                dispatch_limit=2,
                items=[
                    TaskBatchItemRequest(
                        social_account_id=first_account.id,
                        wallet_id=first_wallet.id,
                        external_target="pause-fixture-1",
                    ),
                    TaskBatchItemRequest(
                        social_account_id=second_account.id,
                        wallet_id=second_wallet.id,
                        external_target="pause-fixture-2",
                    ),
                ],
            ),
            session=session,
        )

        paused = pause_task_batch(batch.id, session=session)
        assert paused.state == "paused"
        assert {job.state for job in paused.jobs} == {TaskState.PAUSED.value}
        assert Scheduler(session, worker_concurrency=2, browser_concurrency=2).dispatch_once() == []

        resumed = resume_task_batch(batch.id, session=session)
        assert resumed.state == "active"
        grants = Scheduler(session, worker_concurrency=2, browser_concurrency=2).dispatch_once()
        assert {grant.task_job_id for grant in grants} == {job.id for job in resumed.jobs}

        cancelled = cancel_task_batch(batch.id, session=session)
        assert cancelled.state == "cancelled"
        assert all(job.state == TaskState.CANCELLED.value for job in cancelled.jobs)
        assert session.query(ResourceLease).count() == 0
    engine.dispose()


def test_batch_dispatch_limit_caps_active_leases() -> None:
    """A batch limit applies even when global worker capacity is larger."""
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        items = []
        for suffix in ("5", "6", "7"):
            account, wallet = _resources(session, suffix)
            items.append(
                TaskBatchItemRequest(
                    social_account_id=account.id,
                    wallet_id=wallet.id,
                    external_target=f"limited-fixture-{suffix}",
                )
            )
        batch = create_task_batch(
            TaskBatchCreateRequest(
                name="Single-slot batch",
                kind=TaskKind.BIND,
                dispatch_limit=1,
                items=items,
            ),
            session=session,
        )

        scheduler = Scheduler(session, worker_concurrency=4, browser_concurrency=4)
        grants = scheduler.dispatch_once(limit=4)

        assert len(grants) == 1
        assert grants[0].task_job_id in {job.id for job in batch.jobs}
