"""Durable task creation and state command endpoints."""

from __future__ import annotations

from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ...api.dependencies import get_db
from ...models.tasks import TaskBatch, TaskJob
from ...schemas.tasks import (
    TaskBatchCreateRequest,
    TaskBatchListResponse,
    TaskBatchResponse,
    TaskCreateRequest,
    TaskEventResponse,
    TaskListResponse,
    TaskResponse,
    TaskTransitionRequest,
    WorkflowStageBatchCreateRequest,
    WorkflowBatchCreateRequest,
)
from ...services.tasks import TaskBatchItem, TaskError, TaskService
from ...services.workflows import WorkflowBatchItem, WorkflowService, WorkflowStageBatchItem

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _response(job: TaskJob) -> TaskResponse:
    """Convert a task and its events to a public response."""
    return TaskResponse(
        id=job.id,
        kind=job.kind,
        state=job.state.value,
        attempt=job.attempt,
        priority=job.priority,
        social_account_id=job.social_account_id,
        wallet_id=job.wallet_id,
        binding_id=job.binding_id,
        depends_on_task_id=job.depends_on_task_id,
        idempotency_key=job.idempotency_key,
        lease_keys=list(job.lease_keys),
        scheduled_at=job.scheduled_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        external_operation_ref=job.external_operation_ref,
        # 只返回是否存在目标，避免把原始 URL 或任务参数带回前端。
        target_configured=bool(job.external_target.strip()),
        result_summary=job.result_summary,
        failure_code=job.failure_code,
        poll_deadline_at=job.poll_deadline_at,
        next_poll_at=job.next_poll_at,
        cancel_requested_at=job.cancel_requested_at,
        events=[
            TaskEventResponse(
                id=event.id,
                sequence=event.sequence,
                event_type=event.event_type,
                from_state=event.from_state,
                to_state=event.to_state,
                summary=event.summary,
                created_at=event.created_at,
            )
            for event in job.events
        ],
    )


def _batch_response(batch: TaskBatch) -> TaskBatchResponse:
    """Convert one durable batch and its redacted jobs for list rendering."""
    return TaskBatchResponse(
        id=batch.id,
        name=batch.name,
        kind=batch.kind,
        workflow_type=batch.workflow_type,
        state=batch.state,
        dispatch_limit=batch.dispatch_limit,
        created_at=batch.created_at,
        paused_at=batch.paused_at,
        jobs=[_response(job) for job in batch.jobs],
    )


def _raise(exc: TaskError) -> NoReturn:
    """Map service errors to stable UI-facing HTTP responses."""
    from ...services.tasks import TaskConflictError, TaskNotFoundError

    if isinstance(exc, TaskNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    if isinstance(exc, TaskConflictError):
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("", response_model=TaskResponse, status_code=201)
def create_task(
    request: TaskCreateRequest,
    session: Session = Depends(get_db),
) -> TaskResponse:
    """Create a queued task and reuse duplicates by deterministic key."""
    try:
        result = TaskService(session).create(
            request.kind,
            social_account_id=request.social_account_id,
            wallet_id=request.wallet_id,
            binding_id=request.binding_id,
            external_target=request.external_target,
            priority=request.priority,
            scheduled_at=request.scheduled_at,
        )
    except TaskError as exc:
        _raise(exc)
    return _response(result.job)


@router.post("/batches", response_model=TaskBatchResponse, status_code=201)
def create_task_batch(
    request: TaskBatchCreateRequest,
    session: Session = Depends(get_db),
) -> TaskBatchResponse:
    """Create a batch whose child tasks retain their usual idempotency rules."""
    try:
        result = TaskService(session).create_batch(
            name=request.name,
            kind=request.kind,
            dispatch_limit=request.dispatch_limit,
            items=[
                TaskBatchItem(
                    social_account_id=item.social_account_id,
                    wallet_id=item.wallet_id,
                    binding_id=item.binding_id,
                    external_target=item.external_target,
                    priority=item.priority,
                )
                for item in request.items
            ],
        )
    except TaskError as exc:
        _raise(exc)
    batch = session.execute(
        select(TaskBatch)
        .where(TaskBatch.id == result.batch.id)
        .options(joinedload(TaskBatch.jobs).joinedload(TaskJob.events))
    ).unique().scalar_one()
    return _batch_response(batch)


@router.post("/workflows", response_model=TaskBatchResponse, status_code=201)
def create_workflow_batch(
    request: WorkflowBatchCreateRequest,
    session: Session = Depends(get_db),
) -> TaskBatchResponse:
    """Create one ordered verify-bind-repost-claim chain per pair."""
    try:
        result = WorkflowService(session).create_batch(
            name=request.name,
            dispatch_limit=request.dispatch_limit,
            items=[
                WorkflowBatchItem(
                    social_account_id=item.social_account_id,
                    wallet_id=item.wallet_id,
                    repost_target=item.repost_target,
                    priority=item.priority,
                )
                for item in request.items
            ],
        )
    except TaskError as exc:
        _raise(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    batch = session.execute(
        select(TaskBatch)
        .where(TaskBatch.id == result.batch.id)
        .options(joinedload(TaskBatch.jobs).joinedload(TaskJob.events))
    ).unique().scalar_one()
    return _batch_response(batch)


@router.post("/stages", response_model=TaskBatchResponse, status_code=201)
def create_stage_batch(
    request: WorkflowStageBatchCreateRequest,
    session: Session = Depends(get_db),
) -> TaskBatchResponse:
    """Create one homogeneous workflow stage batch."""
    try:
        result = WorkflowService(session).create_stage_batch(
            name=request.name,
            stage=request.stage.value,
            dispatch_limit=request.dispatch_limit,
            items=[
                WorkflowStageBatchItem(
                    social_account_id=item.social_account_id,
                    wallet_id=item.wallet_id,
                    binding_id=item.binding_id,
                    external_target=item.external_target,
                    priority=item.priority,
                )
                for item in request.items
            ],
        )
    except TaskError as exc:
        _raise(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    batch = session.execute(
        select(TaskBatch)
        .where(TaskBatch.id == result.batch.id)
        .options(joinedload(TaskBatch.jobs).joinedload(TaskJob.events))
    ).unique().scalar_one()
    return _batch_response(batch)


@router.get("/batches", response_model=TaskBatchListResponse)
def list_task_batches(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
) -> TaskBatchListResponse:
    """List operator-created batches and their redacted jobs."""
    batches = session.scalars(
        select(TaskBatch)
        .options(joinedload(TaskBatch.jobs).joinedload(TaskJob.events))
        .order_by(TaskBatch.created_at.desc(), TaskBatch.id)
        .offset(offset)
        .limit(limit)
    ).unique().all()
    total = session.scalar(select(func.count()).select_from(TaskBatch)) or 0
    return TaskBatchListResponse(
        items=[_batch_response(batch) for batch in batches],
        offset=offset,
        limit=limit,
        total=total,
    )


@router.post("/batches/{batch_id}/pause", response_model=TaskBatchResponse)
def pause_task_batch(batch_id: UUID, session: Session = Depends(get_db)) -> TaskBatchResponse:
    """Pause a batch and remove its queued work from future dispatch."""
    try:
        return _batch_response(TaskService(session).pause_batch(batch_id))
    except TaskError as exc:
        _raise(exc)


@router.post("/batches/{batch_id}/resume", response_model=TaskBatchResponse)
def resume_task_batch(batch_id: UUID, session: Session = Depends(get_db)) -> TaskBatchResponse:
    """Resume a paused batch and requeue its paused child jobs."""
    try:
        return _batch_response(TaskService(session).resume_batch(batch_id))
    except TaskError as exc:
        _raise(exc)


@router.post("/batches/{batch_id}/cancel", response_model=TaskBatchResponse)
def cancel_task_batch(batch_id: UUID, session: Session = Depends(get_db)) -> TaskBatchResponse:
    """Cancel pending batch work and block any further batch dispatch."""
    try:
        return _batch_response(TaskService(session).cancel_batch(batch_id))
    except TaskError as exc:
        _raise(exc)


@router.get("", response_model=TaskListResponse)
def list_tasks(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
) -> TaskListResponse:
    """List durable task state and redacted event history."""
    jobs, total = TaskService(session).list_jobs(offset=offset, limit=limit)
    return TaskListResponse(
        items=[_response(job) for job in jobs],
        offset=offset,
        limit=limit,
        total=total,
    )


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: UUID, session: Session = Depends(get_db)) -> TaskResponse:
    """Return one task detail row."""
    try:
        return _response(TaskService(session).get(task_id))
    except TaskError as exc:
        _raise(exc)


@router.post("/{task_id}/pause", response_model=TaskResponse)
def pause_task(task_id: UUID, session: Session = Depends(get_db)) -> TaskResponse:
    """Pause a queued task before dispatch."""
    try:
        return _response(TaskService(session).pause(task_id))
    except TaskError as exc:
        _raise(exc)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(task_id: UUID, session: Session = Depends(get_db)) -> TaskResponse:
    """Cancel a task before execution begins."""
    try:
        return _response(TaskService(session).cancel(task_id))
    except TaskError as exc:
        _raise(exc)


@router.post("/{task_id}/retry", response_model=TaskResponse)
def retry_task(task_id: UUID, session: Session = Depends(get_db)) -> TaskResponse:
    """Requeue a failed task with an incremented attempt counter."""
    try:
        return _response(TaskService(session).retry(task_id))
    except TaskError as exc:
        _raise(exc)


@router.post("/{task_id}/poll", response_model=TaskResponse)
def poll_task(task_id: UUID, session: Session = Depends(get_db)) -> TaskResponse:
    """Requeue a waiting external validation for another status poll."""
    try:
        return _response(TaskService(session).poll(task_id))
    except TaskError as exc:
        _raise(exc)


@router.post("/{task_id}/transition", response_model=TaskResponse)
def transition_task(
    task_id: UUID,
    request: TaskTransitionRequest,
    session: Session = Depends(get_db),
) -> TaskResponse:
    """Apply one worker-facing state transition."""
    try:
        return _response(
            TaskService(session).transition(
                task_id,
                request.to_state,
                summary=request.summary,
                external_operation_ref=request.external_operation_ref,
                failure_code=request.failure_code,
                next_poll_at=request.next_poll_at,
                owner_token=request.owner_token,
            )
        )
    except TaskError as exc:
        _raise(exc)
