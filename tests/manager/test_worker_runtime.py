from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from threading import Event

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from manager_api.db.base import Base
from manager_api.heartbeat import WorkerHeartbeat
from manager_api.models.accounts import AccountHealth, LifecycleState, SocialAccount
from manager_api.models.wallets import Wallet
from manager_api.queue import TaskMessage
from manager_api.runner import TaskRunner
from manager_api.scheduler import Scheduler
from manager_api.services.tasks import TaskService
from manager_api.task_outcomes import WorkerOutcome
from manager_api.models.tasks import TaskJob, TaskKind, TaskState
from manager_api.worker import TaskWorker


class MemoryQueue:
    """Deterministic ready/processing queue for the long-running loop tests."""

    def __init__(self) -> None:
        self.ready: deque[TaskMessage] = deque()
        self.processing: deque[TaskMessage] = deque()

    def enqueue(self, grant) -> None:
        self.ready.append(TaskMessage.from_grant(grant))

    def receive(self, *, timeout: int = 0) -> TaskMessage | None:
        del timeout
        if not self.ready:
            return None
        message = self.ready.popleft()
        self.processing.append(message)
        return message

    def acknowledge(self, message: TaskMessage) -> None:
        self.processing.remove(message)

    def requeue(self, message: TaskMessage) -> None:
        self.processing.remove(message)
        self.ready.appendleft(message)


class HeartbeatRedis:
    """Minimal Redis hash double that records ownership operations."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, str]] = {}
        self.operations: list[tuple[str, str]] = []

    def hset(self, name: str, key: str, value: str) -> int:
        self.values.setdefault(name, {})[key] = value
        self.operations.append(("hset", key))
        return 1

    def hdel(self, name: str, *keys: str) -> int:
        values = self.values.setdefault(name, {})
        removed = sum(1 for key in keys if values.pop(key, None) is not None)
        self.operations.extend(("hdel", key) for key in keys)
        return removed


def _runner_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _task(session: Session) -> None:
    account = SocialAccount(
        handle="worker-runtime-fixture",
        normalized_handle="worker-runtime-fixture",
        state=LifecycleState.ACTIVE,
        health=AccountHealth.UNKNOWN,
    )
    wallet = Wallet(
        address="0x" + "1" * 40,
        normalized_address="0x" + "1" * 40,
        state="active",
    )
    session.add_all([account, wallet])
    session.flush()
    TaskService(session).create(
        TaskKind.BIND,
        social_account_id=account.id,
        wallet_id=wallet.id,
        external_target="fixture",
    )


def test_worker_heartbeat_publishes_and_clears_one_owner() -> None:
    client = HeartbeatRedis()
    heartbeat = WorkerHeartbeat(
        client,
        worker_id="worker-fixture",
    )

    heartbeat.beat()

    assert "worker-fixture" in client.values["manager:workers:heartbeats"]
    heartbeat.close()
    assert client.values["manager:workers:heartbeats"] == {}
    assert client.operations == [
        ("hset", "worker-fixture"),
        ("hdel", "worker-fixture"),
    ]


def test_runner_forever_consumes_work_and_stops_cleanly() -> None:
    session = _runner_session()
    try:
        _task(session)
        scheduler = Scheduler(
            session,
            worker_concurrency=1,
            browser_concurrency=1,
            lease_ttl_seconds=60,
        )
        queue = MemoryQueue()
        worker = TaskWorker(session, scheduler=scheduler)
        stop = Event()
        client = HeartbeatRedis()

        def handler(_job):
            stop.set()
            return WorkerOutcome(
                state=TaskState.SUCCEEDED,
                summary="runtime fixture complete",
            )

        result = TaskRunner(
            scheduler=scheduler,
            worker=worker,
            queue=queue,
            handler=handler,
        ).run_forever(
            heartbeat=WorkerHeartbeat(client, worker_id="worker-runtime"),
            stop_requested=stop.is_set,
            receive_timeout=0,
            recovery_interval_seconds=30,
            heartbeat_interval_seconds=15,
            idle_sleep_seconds=0.01,
        )

        assert result.dispatched == 1
        assert result.completed == 1
        assert result.errors == 0
        assert not queue.ready
        assert not queue.processing
        assert client.values["manager:workers:heartbeats"] == {}
        assert session.query(TaskJob).count() == 1
        assert session.query(TaskJob).one().state is TaskState.SUCCEEDED
    finally:
        session.close()


def test_worker_acknowledges_stale_queue_message_without_requeue() -> None:
    """Redis 旧消息与数据库状态不匹配时，以数据库状态为准清理消息。"""
    session = _runner_session()
    try:
        _task(session)
        job = session.query(TaskJob).one()
        job.state = TaskState.FAILED
        session.flush()
        message = TaskMessage(
            task_job_id=job.id,
            owner_token="stale-owner",
            lease_keys=tuple(job.lease_keys),
            expires_at=datetime.now(timezone.utc),
        )
        queue = MemoryQueue()
        queue.processing.append(message)

        result = TaskWorker(session).run_message(
            message,
            queue,
            lambda _: WorkerOutcome(state=TaskState.SUCCEEDED, summary="unused"),
        )

        assert result.id == job.id
        assert result.state is TaskState.FAILED
        assert not queue.ready
        assert not queue.processing
    finally:
        session.close()
