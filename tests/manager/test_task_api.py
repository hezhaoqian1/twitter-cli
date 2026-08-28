from __future__ import annotations

from typing import Generator
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.api.routers.tasks import create_task, list_tasks, transition_task
from manager_api.config import ManagerSettings
from manager_api.db.base import Base
from manager_api.main import create_app
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.tasks import TaskKind, TaskState
from manager_api.models.wallets import Wallet
from manager_api.schemas.tasks import TaskCreateRequest, TaskTransitionRequest


@pytest.fixture
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _resources(session: Session) -> tuple[SocialAccount, Wallet]:
    account = SocialAccount(
        handle="api-task-account",
        normalized_handle="api-task-account",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet = Wallet(
        address="0x" + "a" * 40,
        normalized_address="0x" + "a" * 40,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
    return account, wallet


def test_task_api_is_registered_idempotent_and_redacted(session: Session) -> None:
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/tasks" in app.openapi()["paths"]
    account, wallet = _resources(session)
    request = TaskCreateRequest(
        kind=TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="https://provider.example/private-target",
    )

    first = create_task(request, session=session)
    second = create_task(request, session=session)
    listed = list_tasks(offset=0, limit=50, session=session)

    assert first.id == second.id
    assert listed.total == 1
    assert "provider.example" not in first.model_dump_json()
    assert "private-target" not in first.model_dump_json()
    assert first.state == TaskState.QUEUED.value
    assert isinstance(first.id, UUID)
    assert listed.items[0].events[0].to_state == TaskState.QUEUED.value


def test_task_api_maps_conflicts_and_rejects_invalid_resource_references(
    session: Session,
) -> None:
    app = create_app(
        ManagerSettings(
            database_url="sqlite+pysqlite:///:memory:",
            redis_url="redis://localhost/0",
            session_secret="test-session-secret-123",
        )
    )
    assert "/api/tasks/{task_id}/transition" in app.openapi()["paths"]

    request = TaskCreateRequest(
        kind=TaskKind.VERIFY_ACCOUNT,
        social_account_id=UUID("00000000-0000-0000-0000-000000000001"),
        external_target="account-health",
    )
    with pytest.raises(HTTPException) as error:
        create_task(request, session=session)
    assert error.value.status_code == 409
    assert error.value.detail == {
        "code": "account_not_found",
        "message": "account not found",
    }

    account, wallet = _resources(session)
    created = create_task(
        TaskCreateRequest(
            kind=TaskKind.BIND,
            social_account_id=account.id,
            wallet_id=wallet.id,
            external_target="bind-fixture",
        ),
        session=session,
    )
    with pytest.raises(HTTPException) as transition_error:
        transition_task(
            created.id,
            TaskTransitionRequest(to_state=TaskState.RUNNING),
            session=session,
        )
    assert transition_error.value.status_code == 409
    detail = transition_error.value.detail
    assert isinstance(detail, dict)
    assert detail["code"] == "invalid_transition"
