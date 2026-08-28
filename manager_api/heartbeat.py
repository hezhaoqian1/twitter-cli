"""Redis-backed heartbeat ownership for long-lived manager workers."""

from __future__ import annotations

from datetime import datetime
from threading import Event, Thread
from typing import Protocol
from uuid import uuid4

from .db.base import utc_now


class HeartbeatRedisClient(Protocol):
    """Minimal Redis hash surface required by the heartbeat owner."""

    def hset(self, name: str, key: str, value: str) -> int: ...

    def hdel(self, name: str, *keys: str) -> int: ...


class WorkerHeartbeat:
    """Publish one process heartbeat and remove it during graceful shutdown."""

    def __init__(
        self,
        client: HeartbeatRedisClient,
        *,
        worker_id: str | None = None,
        key: str = "manager:workers:heartbeats",
        clock=utc_now,
    ) -> None:
        self.client = client
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.key = key
        self.clock = clock
        self._stop_event: Event | None = None
        self._thread: Thread | None = None

    def beat(self, *, now: datetime | None = None) -> None:
        """Write an ISO timestamp; stale records are ignored by metrics readers."""
        timestamp = now or self.clock()
        self.client.hset(self.key, self.worker_id, timestamp.isoformat())

    def start(self, interval_seconds: float) -> None:
        """Pulse in the background so long browser jobs remain observable."""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        if self._thread is not None:
            raise RuntimeError("heartbeat already started")

        self.beat()
        stop_event = Event()
        self._stop_event = stop_event

        def pulse() -> None:
            """Wait between pulses without delaying the main worker loop."""
            while not stop_event.wait(interval_seconds):
                self.beat()

        self._thread = Thread(
            target=pulse,
            name=f"{self.worker_id}-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Remove this process heartbeat without touching other workers."""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        self.client.hdel(self.key, self.worker_id)
