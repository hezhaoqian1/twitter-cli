"""Durable task batches, jobs, events, and resource leases."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .enums import StringEnum


class TaskKind(StringEnum):
    """Supported operator-triggered task kinds."""

    BIND = "bind"
    REPOST = "repost"
    CLAIM = "claim"
    VERIFY_ACCOUNT = "verify_account"
    BALANCE_SYNC = "balance_sync"


class TaskState(StringEnum):
    """Durable task state machine values."""

    DRAFT = "draft"
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    WAITING_EXTERNAL_VALIDATION = "waiting_external_validation"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class TaskBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """User-created group of independently scheduled jobs."""

    __tablename__ = "task_batches"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[TaskKind] = mapped_column(nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), default="single", nullable=False)
    dispatch_limit: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    state: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    jobs: Mapped[list[TaskJob]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )


class TaskJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One durable execution and its idempotency boundary."""

    __tablename__ = "task_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_jobs_idempotency_key"),
        Index("ix_task_jobs_dispatch", "state", "scheduled_at", "priority"),
        Index("ix_task_jobs_next_poll", "state", "next_poll_at"),
    )

    task_batch_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_batches.id", ondelete="SET NULL")
    )
    kind: Mapped[TaskKind] = mapped_column(nullable=False)
    state: Mapped[TaskState] = mapped_column(default=TaskState.DRAFT, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    social_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="RESTRICT")
    )
    wallet_id: Mapped[UUID | None] = mapped_column(ForeignKey("wallets.id", ondelete="RESTRICT"))
    binding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("account_wallet_bindings.id", ondelete="RESTRICT")
    )
    depends_on_task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("task_jobs.id", ondelete="RESTRICT")
    )
    external_target: Mapped[str] = mapped_column(String(512), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    lease_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_operation_ref: Mapped[str | None] = mapped_column(String(512))
    result_summary: Mapped[str | None] = mapped_column(Text)
    failure_code: Mapped[str | None] = mapped_column(String(96))
    poll_deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    batch: Mapped[TaskBatch | None] = relationship(back_populates="jobs")
    depends_on: Mapped["TaskJob | None"] = relationship(
        remote_side="TaskJob.id",
        foreign_keys=[depends_on_task_id],
    )
    events: Mapped[list[TaskEvent]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="TaskEvent.sequence",
    )


class TaskEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only redacted state transition or observation."""

    __tablename__ = "task_events"
    __table_args__ = (
        UniqueConstraint("task_job_id", "sequence", name="uq_task_events_job_sequence"),
        Index("ix_task_events_job_created_at", "task_job_id", "created_at"),
    )

    task_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(48))
    to_state: Mapped[str | None] = mapped_column(String(48))
    summary: Mapped[str | None] = mapped_column(Text)
    event_metadata: Mapped[dict[str, object] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    job: Mapped[TaskJob] = relationship(back_populates="events")


class ResourceLease(UUIDPrimaryKeyMixin, Base):
    """Short-lived exclusive lease for an account or wallet resource."""

    __tablename__ = "resource_leases"
    __table_args__ = (
        UniqueConstraint("lease_key", name="uq_resource_leases_lease_key"),
        Index("ix_resource_leases_expiry", "expires_at"),
    )

    lease_key: Mapped[str] = mapped_column(String(160), nullable=False)
    task_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("task_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_token: Mapped[str] = mapped_column(String(96), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
