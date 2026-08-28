"""Shared worker outcome contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models.tasks import TaskState


@dataclass(frozen=True)
class WorkerOutcome:
    """脱敏的适配器结果，供 Worker 映射到持久化任务状态。"""

    state: TaskState
    summary: str | None = None
    external_operation_ref: str | None = None
    failure_code: str | None = None
    next_poll_at: datetime | None = None
