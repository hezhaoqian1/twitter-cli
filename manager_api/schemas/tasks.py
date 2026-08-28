"""Redacted task state and event API contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models.tasks import TaskKind, TaskState


class TaskCreateRequest(BaseModel):
    """Create one durable manual task with a restart-safe public target."""

    model_config = ConfigDict(extra="forbid")

    kind: TaskKind
    social_account_id: UUID | None = None
    wallet_id: UUID | None = None
    binding_id: UUID | None = None
    external_target: str = Field(min_length=1, max_length=512)
    priority: int = Field(default=0, ge=-100, le=100)
    scheduled_at: datetime | None = None


class TaskBatchItemRequest(BaseModel):
    """One task scope included in a multi-row operator command."""

    model_config = ConfigDict(extra="forbid")

    social_account_id: UUID | None = None
    wallet_id: UUID | None = None
    binding_id: UUID | None = None
    external_target: str = Field(min_length=1, max_length=512)
    priority: int = Field(default=0, ge=-100, le=100)


class TaskBatchCreateRequest(BaseModel):
    """Create up to 500 independently scheduled jobs under one batch."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    kind: TaskKind
    items: list[TaskBatchItemRequest] = Field(min_length=1, max_length=500)
    dispatch_limit: int = Field(default=10, ge=1, le=32)


class WorkflowBatchItemRequest(BaseModel):
    """One account-wallet pair in the login, binding, repost, and claim workflow."""

    model_config = ConfigDict(extra="forbid")

    social_account_id: UUID
    wallet_id: UUID
    repost_target: str = Field(min_length=1, max_length=512)
    priority: int = Field(default=0, ge=-100, le=100)


class WorkflowBatchCreateRequest(BaseModel):
    """Create verify, bind, repost, and claim jobs for each independent pair."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    items: list[WorkflowBatchItemRequest] = Field(min_length=1, max_length=500)
    dispatch_limit: int = Field(default=10, ge=1, le=32)


class TaskTransitionRequest(BaseModel):
    """Optional redacted observation for a worker-owned transition."""

    model_config = ConfigDict(extra="forbid")

    to_state: TaskState
    summary: str | None = Field(default=None, max_length=2000)
    external_operation_ref: str | None = Field(default=None, max_length=512)
    failure_code: str | None = Field(default=None, max_length=96)
    next_poll_at: datetime | None = None
    owner_token: str | None = Field(default=None, min_length=1, max_length=96)


class TaskEventResponse(BaseModel):
    """One append-only redacted task event."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    sequence: int
    event_type: str
    from_state: str | None = None
    to_state: str | None = None
    summary: str | None = None
    created_at: datetime


class TaskResponse(BaseModel):
    """Public durable task state without secret or raw target material."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: TaskKind
    state: str
    attempt: int
    priority: int
    social_account_id: UUID | None = None
    wallet_id: UUID | None = None
    binding_id: UUID | None = None
    depends_on_task_id: UUID | None = None
    idempotency_key: str
    lease_keys: list[str]
    scheduled_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    external_operation_ref: str | None = None
    result_summary: str | None = None
    failure_code: str | None = None
    poll_deadline_at: datetime | None = None
    next_poll_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    events: list[TaskEventResponse] = Field(default_factory=list)


class TaskListResponse(BaseModel):
    """Paginated task list for the operations console."""

    model_config = ConfigDict(extra="forbid")

    items: list[TaskResponse]
    offset: int
    limit: int
    total: int


class TaskBatchResponse(BaseModel):
    """Public task-batch metadata and redacted child task rows."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    kind: TaskKind
    workflow_type: str
    state: str
    dispatch_limit: int
    created_at: datetime
    paused_at: datetime | None = None
    jobs: list[TaskResponse]


class TaskBatchListResponse(BaseModel):
    """Paginated task batches for the operations console."""

    model_config = ConfigDict(extra="forbid")

    items: list[TaskBatchResponse]
    offset: int
    limit: int
    total: int
