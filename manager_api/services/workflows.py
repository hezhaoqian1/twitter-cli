"""Creation of the per-pair login, binding, and repost workflow."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from ..models.tasks import TaskBatch, TaskKind, TaskJob
from .bindings import BindingConflictError, BindingService
from .tasks import TaskConflictError, TaskService


@dataclass(frozen=True)
class WorkflowBatchItem:
    """Public identifiers and target for one independent account-wallet pair."""

    social_account_id: UUID
    wallet_id: UUID
    repost_target: str
    priority: int = 0


@dataclass(frozen=True)
class WorkflowBatchCreateResult:
    """Batch plus the four ordered jobs created per pair."""

    batch: TaskBatch
    jobs: tuple[tuple[TaskJob, TaskJob, TaskJob, TaskJob], ...]


class WorkflowService:
    """Build a durable verify -> bind -> repost -> claim task graph."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_batch(
        self,
        *,
        name: str,
        items: list[WorkflowBatchItem],
        dispatch_limit: int = 10,
    ) -> WorkflowBatchCreateResult:
        """Create independent chains while sharing one fair dispatch batch."""
        if not name.strip():
            raise ValueError("workflow batch name must not be empty")
        if not items:
            raise ValueError("workflow batch must contain at least one item")
        if dispatch_limit < 1:
            raise ValueError("dispatch limit must be positive")

        batch = TaskBatch(
            name=name.strip(),
            kind=TaskKind.VERIFY_ACCOUNT,
            workflow_type="account_wallet",
            dispatch_limit=dispatch_limit,
            state="active",
        )
        self.session.add(batch)
        self.session.flush()

        task_service = TaskService(self.session)
        chains: list[tuple[TaskJob, TaskJob, TaskJob, TaskJob]] = []
        for item in items:
            try:
                binding = BindingService(self.session).create_pending(
                    item.social_account_id,
                    item.wallet_id,
                ).binding
            except BindingConflictError as exc:
                raise TaskConflictError(exc.code, exc.detail) from exc
            verify = task_service.create(
                TaskKind.VERIFY_ACCOUNT,
                social_account_id=item.social_account_id,
                external_target="x:verify",
                priority=item.priority,
                task_batch_id=batch.id,
            ).job
            bind = task_service.create(
                TaskKind.BIND,
                binding_id=binding.id,
                external_target="kredo:bind",
                priority=item.priority,
                task_batch_id=batch.id,
                depends_on_task_id=verify.id,
            ).job
            repost = task_service.create(
                TaskKind.REPOST,
                binding_id=binding.id,
                external_target=item.repost_target,
                priority=item.priority,
                task_batch_id=batch.id,
                depends_on_task_id=bind.id,
                allow_pending_binding=True,
            ).job
            claim = task_service.create(
                TaskKind.CLAIM,
                binding_id=binding.id,
                external_target="kredo:claim",
                priority=item.priority,
                task_batch_id=batch.id,
                depends_on_task_id=repost.id,
                allow_pending_binding=True,
            ).job
            chains.append((verify, bind, repost, claim))
        return WorkflowBatchCreateResult(batch=batch, jobs=tuple(chains))
