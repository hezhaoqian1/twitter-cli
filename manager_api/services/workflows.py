"""Creation of the per-pair login, binding, and repost workflow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


class WorkflowStage(str, Enum):
    """Independent batch stage supported by the management console."""

    VERIFY = "verify"
    BIND = "bind"
    REPOST = "repost"
    CLAIM = "claim"


@dataclass(frozen=True)
class WorkflowStageBatchItem:
    """One item inside a homogeneous stage batch."""

    social_account_id: UUID | None = None
    wallet_id: UUID | None = None
    binding_id: UUID | None = None
    external_target: str = ""
    priority: int = 0


@dataclass(frozen=True)
class WorkflowStageBatchCreateResult:
    """Batch plus the flat jobs created for one stage."""

    batch: TaskBatch
    jobs: tuple[TaskJob, ...]


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

    def create_stage_batch(
        self,
        *,
        name: str,
        stage: WorkflowStage | str,
        items: list[WorkflowStageBatchItem],
        dispatch_limit: int = 10,
    ) -> WorkflowStageBatchCreateResult:
        """Create a single-stage batch with no cross-stage dependencies."""
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("workflow batch name must not be empty")
        if not items:
            raise ValueError("workflow batch must contain at least one item")
        if dispatch_limit < 1:
            raise ValueError("dispatch limit must be positive")

        try:
            normalized_stage = stage if isinstance(stage, WorkflowStage) else WorkflowStage(stage)
        except ValueError as exc:
            raise ValueError("unsupported workflow stage") from exc

        if normalized_stage is WorkflowStage.BIND:
            return self._create_bind_stage_batch(
                name=normalized_name,
                items=items,
                dispatch_limit=dispatch_limit,
            )

        batch = TaskBatch(
            name=normalized_name,
            kind={
                WorkflowStage.VERIFY: TaskKind.VERIFY_ACCOUNT,
                WorkflowStage.REPOST: TaskKind.REPOST,
                WorkflowStage.CLAIM: TaskKind.CLAIM,
            }[normalized_stage],
            workflow_type=f"stage:{normalized_stage.value}",
            dispatch_limit=dispatch_limit,
            state="active",
        )
        self.session.add(batch)
        self.session.flush()

        task_service = TaskService(self.session)
        jobs: list[TaskJob] = []
        for item in items:
            if normalized_stage is WorkflowStage.VERIFY:
                if item.social_account_id is None:
                    raise ValueError("verify stage items require social_account_id")
                result = task_service.create(
                    TaskKind.VERIFY_ACCOUNT,
                    social_account_id=item.social_account_id,
                    external_target=item.external_target or "x:verify",
                    priority=item.priority,
                    task_batch_id=batch.id,
                )
            elif normalized_stage is WorkflowStage.REPOST:
                if item.binding_id is None:
                    raise ValueError("repost stage items require binding_id")
                result = task_service.create(
                    TaskKind.REPOST,
                    binding_id=item.binding_id,
                    external_target=item.external_target,
                    priority=item.priority,
                    task_batch_id=batch.id,
                )
            else:
                if item.binding_id is None:
                    raise ValueError("claim stage items require binding_id")
                result = task_service.create(
                    TaskKind.CLAIM,
                    binding_id=item.binding_id,
                    external_target=item.external_target or "kredo:claim",
                    priority=item.priority,
                    task_batch_id=batch.id,
                )
            jobs.append(result.job)
        return WorkflowStageBatchCreateResult(batch=batch, jobs=tuple(jobs))

    def _create_bind_stage_batch(
        self,
        *,
        name: str,
        items: list[WorkflowStageBatchItem],
        dispatch_limit: int,
    ) -> WorkflowStageBatchCreateResult:
        """Create pending bindings and bind jobs without chaining other stages."""
        batch = TaskBatch(
            name=name,
            kind=TaskKind.BIND,
            workflow_type="stage:bind",
            dispatch_limit=dispatch_limit,
            state="active",
        )
        self.session.add(batch)
        self.session.flush()

        task_service = TaskService(self.session)
        jobs: list[TaskJob] = []
        for item in items:
            if item.social_account_id is None or item.wallet_id is None:
                raise ValueError("bind stage items require social_account_id and wallet_id")
            try:
                binding = BindingService(self.session).create_pending(
                    item.social_account_id,
                    item.wallet_id,
                ).binding
            except BindingConflictError as exc:
                raise TaskConflictError(exc.code, exc.detail) from exc
            result = task_service.create(
                TaskKind.BIND,
                binding_id=binding.id,
                external_target=item.external_target or "kredo:bind",
                priority=item.priority,
                task_batch_id=batch.id,
            )
            jobs.append(result.job)
        return WorkflowStageBatchCreateResult(batch=batch, jobs=tuple(jobs))
