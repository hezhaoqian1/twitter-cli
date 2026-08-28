from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.api.routers.tasks import create_task_batch, list_task_batches
from manager_api.db.base import Base
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.tasks import TaskKind
from manager_api.models.wallets import Wallet
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
