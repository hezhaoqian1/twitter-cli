"""Public runtime observability response models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class RuntimeQueueMetrics(BaseModel):
    """Redacted Redis queue depths."""

    ready: int
    processing: int


class RuntimeTaskMetrics(BaseModel):
    """Durable task counts grouped by state."""

    total: int
    active: int
    counts: dict[str, int]
    last_finished_at: datetime | None


class RuntimeLeaseMetrics(BaseModel):
    """Current resource lease occupancy."""

    active: int
    expiring_soon: int


class RuntimeWorkerMetrics(BaseModel):
    """Worker heartbeat summary without worker identifiers."""

    active: int
    heartbeat_at: datetime | None


class RuntimeMetricsResponse(BaseModel):
    """Single read-only snapshot used by the operator overview."""

    generated_at: datetime
    queues: RuntimeQueueMetrics
    tasks: RuntimeTaskMetrics
    leases: RuntimeLeaseMetrics
    workers: RuntimeWorkerMetrics
