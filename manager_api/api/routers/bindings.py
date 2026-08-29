"""Immutable account-wallet binding endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...api.dependencies import get_db, get_vault
from ...models.bindings import BindingState
from ...models.tasks import TaskJob, TaskKind, TaskState
from ...schemas.balances import BalanceResponse
from ...schemas.bindings import (
    BindingConfirmRequest,
    BindingCreateRequest,
    BindingListResponse,
    BindingResponse,
    BindingStageResponse,
    ManualWorkbenchLaunchResponse,
    ManualWorkbenchRequest,
    ManualWorkbenchResponse,
)
from ...services.manual_workbench import ManualWorkbenchLaunch, ManualWorkbenchService
from ...services.vault import VaultService
from ...services.bindings import (
    BindingConflictError,
    BindingError,
    BindingNotFoundError,
    BindingService,
    BindingView,
)

router = APIRouter(prefix="/api/bindings", tags=["bindings"])


ACTIVE_TASK_STATES = {
    TaskState.QUEUED,
    TaskState.LEASED,
    TaskState.RUNNING,
    TaskState.WAITING_EXTERNAL_VALIDATION,
}


def _latest_task(session: Session, binding_id: UUID, kind: TaskKind) -> TaskJob | None:
    """读取当前绑定指定阶段的最近任务，不暴露外部目标。"""
    return session.scalars(
        select(TaskJob)
        .where(TaskJob.binding_id == binding_id, TaskJob.kind == kind)
        .order_by(TaskJob.created_at.desc(), TaskJob.id.desc())
        .limit(1)
    ).first()


def _stage_response(session: Session, view: BindingView) -> BindingStageResponse:
    """从 durable task 状态计算行级按钮可用性。"""
    repost = _latest_task(session, view.binding.id, TaskKind.REPOST)
    claim = _latest_task(session, view.binding.id, TaskKind.CLAIM)
    is_bound = view.binding.state is BindingState.BOUND
    repost_state = repost.state if repost is not None else None
    claim_state = claim.state if claim is not None else None
    repost_succeeded = repost_state is TaskState.SUCCEEDED
    claim_succeeded = claim_state is TaskState.SUCCEEDED
    repost_created = repost_state is not None
    claim_created = claim_state is not None
    return BindingStageResponse(
        repost_state=repost_state.value if repost_state is not None else None,
        claim_state=claim_state.value if claim_state is not None else None,
        can_repost=is_bound and not repost_created and not claim_succeeded,
        can_claim=is_bound and repost_succeeded and not claim_created,
        repost_waiting=repost_state is TaskState.WAITING_EXTERNAL_VALIDATION,
        claim_waiting=claim_state is TaskState.WAITING_EXTERNAL_VALIDATION,
    )


def _response(view: BindingView, session: Session) -> BindingResponse:
    """Convert a binding view to public-only response data."""
    snapshot = view.binding.balance_snapshot
    return BindingResponse(
        id=view.binding.id,
        social_account_id=view.binding.social_account_id,
        wallet_id=view.binding.wallet_id,
        account_handle=view.account.handle,
        wallet_address=view.wallet.address,
        binding_key=view.binding.binding_key,
        state=view.binding.state.value,
        bound_at=view.binding.bound_at,
        external_reference=view.binding.external_reference,
        archived_at=view.binding.archived_at,
        stage=_stage_response(session, view),
        balance=(
            BalanceResponse(
                id=snapshot.id,
                binding_id=view.binding.id,
                account_handle=view.account.handle,
                wallet_address=view.wallet.address,
                points=snapshot.points,
                cash_hsk_available=snapshot.cash_hsk_available,
                positions_value_hsk=snapshot.positions_value_hsk,
                total_hsk=(
                    snapshot.cash_hsk_available + snapshot.positions_value_hsk
                    if snapshot.cash_hsk_available is not None
                    and snapshot.positions_value_hsk is not None
                    else None
                ),
                sync_status=snapshot.sync_status,
                error_code=snapshot.error_code,
                last_synced_at=snapshot.last_synced_at,
            )
            if snapshot is not None
            else None
        ),
    )


def _raise(exc: BindingError) -> None:
    """Map service errors to stable UI-facing HTTP error contracts."""
    if isinstance(exc, BindingNotFoundError):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(exc)}) from exc
    if isinstance(exc, BindingConflictError):
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": exc.detail},
        ) from exc
    raise HTTPException(status_code=422, detail=str(exc)) from exc


def _unlock_for_local_workbench(request: Request, vault: VaultService) -> None:
    """用服务端环境密码解锁本机工作台路径，不把密码返回给前端。"""
    if vault.is_unlocked:
        return
    settings = request.app.state.settings
    if not settings.worker_vault_password:
        raise HTTPException(status_code=423, detail="vault is locked")
    vault.unlock_with_password(settings.worker_vault_password.get_secret_value())


def _workbench_response(item: ManualWorkbenchLaunch) -> ManualWorkbenchLaunchResponse:
    """Convert a launched process into a public API item."""
    return ManualWorkbenchLaunchResponse(
        binding_id=item.binding_id,
        process_id=item.process_id,
        screenshot=item.screenshot,
    )


@router.post("", response_model=BindingResponse, status_code=201)
def create_binding(
    request: BindingCreateRequest,
    session: Session = Depends(get_db),
) -> BindingResponse:
    """Create a pending pairing intent after all resource checks pass."""
    try:
        view = BindingService(session).create_pending(
            request.social_account_id,
            request.wallet_id,
            binding_key=request.binding_key,
        )
    except BindingError as exc:
        _raise(exc)
    return _response(view, session)


@router.post("/{binding_id}/confirm", response_model=BindingResponse)
def confirm_binding(
    binding_id: UUID,
    request: BindingConfirmRequest,
    session: Session = Depends(get_db),
) -> BindingResponse:
    """Finalize a pending binding with the external confirmation reference."""
    try:
        view = BindingService(session).confirm(binding_id, request.external_reference)
    except BindingError as exc:
        _raise(exc)
    return _response(view, session)


@router.post("/{binding_id}/archive", response_model=BindingResponse)
def archive_binding(
    binding_id: UUID,
    session: Session = Depends(get_db),
) -> BindingResponse:
    """Archive a binding without deleting its history or changing its pair."""
    try:
        view = BindingService(session).archive(binding_id)
    except BindingError as exc:
        _raise(exc)
    return _response(view, session)


@router.post("/{binding_id}/manual-workbench", response_model=ManualWorkbenchResponse)
def launch_manual_workbench(
    binding_id: UUID,
    request_body: ManualWorkbenchRequest,
    request: Request,
    session: Session = Depends(get_db),
    vault: VaultService = Depends(get_vault),
) -> ManualWorkbenchResponse:
    """Launch one headed browser with X cookies, wallet provider, and manual X flow."""
    _unlock_for_local_workbench(request, vault)
    try:
        launch = ManualWorkbenchService(session, vault).launch(
            binding_id,
            timeout_seconds=request_body.timeout_seconds,
        )
    except BindingError as exc:
        _raise(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ManualWorkbenchResponse(launched=1, items=[_workbench_response(launch)])


@router.post("/manual-workbenches", response_model=ManualWorkbenchResponse)
def launch_manual_workbenches(
    request_body: ManualWorkbenchRequest,
    request: Request,
    session: Session = Depends(get_db),
    vault: VaultService = Depends(get_vault),
) -> ManualWorkbenchResponse:
    """Launch up to ten independent headed browser workbenches."""
    if not request_body.binding_ids:
        raise HTTPException(status_code=422, detail="binding_ids must not be empty")
    _unlock_for_local_workbench(request, vault)
    try:
        launches = ManualWorkbenchService(session, vault).launch_many(
            request_body.binding_ids,
            limit=request_body.limit,
            timeout_seconds=request_body.timeout_seconds,
        )
    except BindingError as exc:
        _raise(exc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ManualWorkbenchResponse(
        launched=len(launches),
        items=[_workbench_response(item) for item in launches],
    )


@router.get("/{binding_id}", response_model=BindingResponse)
def get_binding(
    binding_id: UUID,
    session: Session = Depends(get_db),
) -> BindingResponse:
    """Return one redacted binding row."""
    try:
        view = BindingService(session).get(binding_id)
    except BindingError as exc:
        _raise(exc)
    return _response(view, session)


@router.get("", response_model=BindingListResponse)
def list_bindings(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
) -> BindingListResponse:
    """List binding history for table views."""
    views, total = BindingService(session).list(offset=offset, limit=limit)
    return BindingListResponse(
        items=[_response(view, session) for view in views],
        offset=offset,
        limit=limit,
        total=total,
    )
