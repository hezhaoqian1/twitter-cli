from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from uuid import uuid4

from manager_api.queue import RedisTaskQueue, TaskMessage
from manager_api.scheduler import DispatchGrant


class FakeRedis:
    """Small reliable-list double for queue ordering and acknowledgement tests."""

    def __init__(self) -> None:
        self.lists: dict[str, deque[str]] = {}

    def _list(self, name: str) -> deque[str]:
        return self.lists.setdefault(name, deque())

    def rpush(self, name: str, value: str) -> int:
        queue = self._list(name)
        queue.append(value)
        return len(queue)

    def brpoplpush(self, source: str, destination: str, timeout: int = 0) -> str | None:
        del timeout
        return self.rpoplpush(source, destination)

    def rpoplpush(self, source: str, destination: str) -> str | None:
        source_queue = self._list(source)
        if not source_queue:
            return None
        value = source_queue.pop()
        self._list(destination).appendleft(value)
        return value

    def lrem(self, name: str, count: int, value: str) -> int:
        queue = self._list(name)
        removed = 0
        kept: deque[str] = deque()
        for item in queue:
            if item == value and (count == 0 or removed < abs(count)):
                removed += 1
            else:
                kept.append(item)
        self.lists[name] = kept
        return removed


def _grant() -> DispatchGrant:
    return DispatchGrant(
        task_job_id=uuid4(),
        owner_token="owner-fixture",
        lease_keys=("account:fixture", "wallet:fixture"),
        expires_at=datetime.now(timezone.utc),
    )


def test_task_message_round_trip_preserves_lease_contract() -> None:
    grant = _grant()
    message = TaskMessage.from_grant(grant)
    decoded = TaskMessage.decode(message.encode())

    assert decoded == message


def test_redis_queue_moves_to_processing_and_acknowledges_after_work() -> None:
    client = FakeRedis()
    queue = RedisTaskQueue(client, ready_key="ready", processing_key="processing")
    grant = _grant()

    queue.enqueue(grant)
    message = queue.receive()
    assert message == TaskMessage.from_grant(grant)
    assert list(client.lists["ready"]) == []
    assert len(client.lists["processing"]) == 1

    queue.acknowledge(message)
    assert list(client.lists["processing"]) == []


def test_redis_queue_timeout_zero_is_non_blocking() -> None:
    """bounded drain 使用 timeout=0 时不能永久阻塞 Redis。"""
    client = FakeRedis()
    queue = RedisTaskQueue(client, ready_key="ready", processing_key="processing")

    assert queue.receive(timeout=0) is None
    assert list(client.lists["ready"]) == []
    assert list(client.lists.get("processing", [])) == []


def test_redis_queue_requeues_uncompleted_message() -> None:
    client = FakeRedis()
    queue = RedisTaskQueue(client, ready_key="ready", processing_key="processing")
    grant = _grant()
    queue.enqueue(grant)
    message = queue.receive()
    assert message is not None

    queue.requeue(message)
    assert len(client.lists["processing"]) == 0
    assert len(client.lists["ready"]) == 1
