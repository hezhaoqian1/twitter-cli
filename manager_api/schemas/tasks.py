"""Redacted task state and event API contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic import SecretStr

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


class WorkflowStage(str, Enum):
    """Independent console stage for batch creation."""

    VERIFY = "verify"
    BIND = "bind"
    REPOST = "repost"
    CLAIM = "claim"


class WorkflowStageBatchItemRequest(BaseModel):
    """One row in a stage-oriented batch command."""

    model_config = ConfigDict(extra="forbid")

    social_account_id: UUID | None = None
    wallet_id: UUID | None = None
    binding_id: UUID | None = None
    external_target: str = Field(default="", max_length=512)
    priority: int = Field(default=0, ge=-100, le=100)


class WorkflowStageBatchCreateRequest(BaseModel):
    """Create one homogeneous stage batch without implicit chaining."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    stage: WorkflowStage
    items: list[WorkflowStageBatchItemRequest] = Field(min_length=1, max_length=500)
    dispatch_limit: int = Field(default=10, ge=1, le=32)


class StagePollRequeueRequest(BaseModel):
    """Preview or requeue waiting external-validation tasks for one stage."""

    model_config = ConfigDict(extra="forbid")

    stage: WorkflowStage
    limit: int = Field(default=10, ge=1, le=500)
    apply: bool = False


class StagePollRequeueResponse(BaseModel):
    """Redacted result for bulk status-poll maintenance."""

    model_config = ConfigDict(extra="forbid")

    stage: WorkflowStage
    selected: int
    requeued: int
    skipped_missing_ref: int
    apply: bool


class StageRetryRequest(BaseModel):
    """Preview or retry failed tasks for one independent workflow stage."""

    model_config = ConfigDict(extra="forbid")

    stage: WorkflowStage
    limit: int = Field(default=10, ge=1, le=500)
    apply: bool = False


class StageRetryResponse(BaseModel):
    """Redacted result for bulk failed-stage retry maintenance."""

    model_config = ConfigDict(extra="forbid")

    stage: WorkflowStage
    selected: int
    retried: int
    apply: bool


class BindStatusSyncRequest(BaseModel):
    """Preview or queue read-only status sync jobs for pending bindings."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="bind status sync", min_length=1, max_length=255)
    limit: int = Field(default=10, ge=1, le=500)
    dispatch_limit: int = Field(default=10, ge=1, le=32)
    apply: bool = False


class BindStatusSyncResponse(BaseModel):
    """Redacted aggregate result for pending-binding status sync."""

    model_config = ConfigDict(extra="forbid")

    apply: bool
    name: str
    limit: int
    pending_bindings: int
    selected: int
    created_jobs: int
    reused_jobs: int
    paused_action_jobs: int
    skipped_existing_status_job: int
    skipped_active_lease: int
    skipped_missing_secret: int


class PairedBindStageRequest(BaseModel):
    """Create or preview bind jobs by matching account and private-key input rows."""

    model_config = ConfigDict(extra="forbid")

    accounts_content: SecretStr
    private_keys_content: SecretStr
    name: str = Field(min_length=1, max_length=255)
    limit: int = Field(default=10, ge=1, le=500)
    dispatch_limit: int = Field(default=10, ge=1, le=32)
    include_unverified: bool = False
    apply: bool = False


class PairedBindStageResponse(BaseModel):
    """Redacted aggregate result for exact row-paired bind creation."""

    model_config = ConfigDict(extra="forbid")

    apply: bool
    name: str
    limit: int
    dispatch_limit: int
    total_pairs: int
    selected_pairs: int
    created_jobs: int
    counts: dict[str, int]


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
    target_configured: bool
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
