"""Redis delivery optimization for already-durable task dispatches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from .scheduler import DispatchGrant


class RedisListClient(Protocol):
    """Minimal Redis list surface used by the manager queue."""

    def rpush(self, name: str, value: str) -> int: ...

    def rpoplpush(self, source: str, destination: str) -> str | None: ...

    def brpoplpush(self, source: str, destination: str, timeout: int = 0) -> str | None: ...

    def lrem(self, name: str, count: int, value: str) -> int: ...


@dataclass(frozen=True)
class TaskMessage:
    """Internal queue payload containing no provider or vault material."""

    task_job_id: UUID
    owner_token: str
    lease_keys: tuple[str, ...]
    expires_at: datetime

    def encode(self) -> str:
        """Serialize only the identifiers required to finish one leased job."""
        return json.dumps(
            {
                "task_job_id": str(self.task_job_id),
                "owner_token": self.owner_token,
                "lease_keys": list(self.lease_keys),
                "expires_at": self.expires_at.isoformat(),
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def decode(cls, payload: str) -> TaskMessage:
        """Validate a Redis payload before it crosses into worker execution."""
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("task message must be an object")
        task_job_id = data.get("task_job_id")
        owner_token = data.get("owner_token")
        lease_keys = data.get("lease_keys")
        expires_at = data.get("expires_at")
        if (
            not isinstance(task_job_id, str)
            or not isinstance(owner_token, str)
            or not isinstance(lease_keys, list)
            or not all(isinstance(key, str) for key in lease_keys)
            or not isinstance(expires_at, str)
        ):
            raise ValueError("task message has invalid fields")
        return cls(
            task_job_id=UUID(task_job_id),
            owner_token=owner_token,
            lease_keys=tuple(lease_keys),
            expires_at=datetime.fromisoformat(expires_at),
        )

    @classmethod
    def from_grant(cls, grant: DispatchGrant) -> TaskMessage:
        """Build a queue payload from a successful durable lease grant."""
        return cls(
            task_job_id=grant.task_job_id,
            owner_token=grant.owner_token,
            lease_keys=grant.lease_keys,
            expires_at=grant.expires_at,
        )


@runtime_checkable
class TaskQueue(Protocol):
    """Queue contract shared by Redis and deterministic test doubles."""

    def enqueue(self, grant: DispatchGrant) -> None: ...

    def receive(self, *, timeout: int = 0) -> TaskMessage | None: ...

    def acknowledge(self, message: TaskMessage) -> None: ...

    def requeue(self, message: TaskMessage) -> None: ...


class RedisTaskQueue:
    """Reliable-list Redis queue with a ready and processing list."""

    def __init__(
        self,
        client: RedisListClient,
        *,
        ready_key: str = "manager:tasks:ready",
        processing_key: str = "manager:tasks:processing",
    ) -> None:
        self.client = client
        self.ready_key = ready_key
        self.processing_key = processing_key

    def enqueue(self, grant: DispatchGrant) -> None:
        """Add a message only after the database lease/state transaction succeeds."""
        self.client.rpush(self.ready_key, TaskMessage.from_grant(grant).encode())

    def receive(self, *, timeout: int = 0) -> TaskMessage | None:
        """Move one ready message to processing so it survives worker crashes."""
        if timeout <= 0:
            payload = self.client.rpoplpush(self.ready_key, self.processing_key)
        else:
            payload = self.client.brpoplpush(
                self.ready_key,
                self.processing_key,
                timeout=timeout,
            )
        return TaskMessage.decode(payload) if payload is not None else None

    def acknowledge(self, message: TaskMessage) -> None:
        """Remove a processing message after durable task completion."""
        self.client.lrem(self.processing_key, 1, message.encode())

    def requeue(self, message: TaskMessage) -> None:
        """Return an uncompleted message to ready delivery."""
        self.client.lrem(self.processing_key, 1, message.encode())
        self.client.rpush(self.ready_key, message.encode())
