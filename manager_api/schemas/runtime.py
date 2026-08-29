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


class OperationResourceCounts(BaseModel):
    """运营台公开资源计数，不包含任何密钥或会话内容。"""

    accounts_total: int
    accounts_active: int
    accounts_healthy: int
    accounts_available_for_binding: int
    wallets_total: int
    wallets_active: int
    wallets_available_for_binding: int
    bindings_total: int
    bindings_pending: int
    bindings_bound: int


class OperationStageSummary(BaseModel):
    """One independent batch stage and its current actionable count."""

    key: str
    label: str
    ready: int
    waiting: int = 0
    failed: int = 0
    pollable: int = 0
    retryable: int = 0
    status_syncable: int = 0
    detail: str


class OperationsSummaryResponse(BaseModel):
    """Read-only operations command-center summary for staged batch work."""

    generated_at: datetime
    resources: OperationResourceCounts
    stages: list[OperationStageSummary]


class NextStageRecommendationResponse(BaseModel):
    """Read-only next action suggestion for the staged operations console."""

    action: str
    stage: str | None
    command: str
    reason: str


class AcceptanceStageResponse(BaseModel):
    """One redacted acceptance row for an independently runnable stage."""

    stage: str
    ready: int
    waiting: int
    failed: int
    pollable: int
    retryable: int
    status_syncable: int = 0


class AcceptanceActionResponse(BaseModel):
    """One redacted operator action surfaced by acceptance audit."""

    action: str
    stage: str
    count: int
    command: str


class AcceptanceAuditResponse(BaseModel):
    """Read-only acceptance audit snapshot with no secret material."""

    resources: dict[str, int]
    stages: list[AcceptanceStageResponse]
    next_action: NextStageRecommendationResponse
    actions: list[AcceptanceActionResponse]
