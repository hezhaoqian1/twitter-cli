"""Read-only Kredo balance query and sync-task endpoints."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ...api.dependencies import get_db
from ...models.bindings import AccountWalletBinding, BindingState
from ...models.tasks import TaskKind
from ...schemas.balances import (
    BalanceListResponse,
    BalanceResponse,
    BalanceSyncRequest,
    BalanceSyncResponse,
)
from ...services.tasks import TaskError, TaskService

router = APIRouter(prefix="/api/balances", tags=["balances"])


def _response(binding: AccountWalletBinding) -> BalanceResponse:
    """Render one binding and preserve nulls until the first successful sync."""
    snapshot = binding.balance_snapshot
    return BalanceResponse(
        id=snapshot.id if snapshot else None,
        binding_id=binding.id,
        account_handle=binding.account.handle,
        wallet_address=binding.wallet.address,
        points=snapshot.points if snapshot else None,
        cash_hsk_available=snapshot.cash_hsk_available if snapshot else None,
        positions_value_hsk=snapshot.positions_value_hsk if snapshot else None,
        total_hsk=(
            snapshot.cash_hsk_available + snapshot.positions_value_hsk
            if snapshot
            and snapshot.cash_hsk_available is not None
            and snapshot.positions_value_hsk is not None
            else None
        ),
        sync_status=snapshot.sync_status if snapshot else "never",
        error_code=snapshot.error_code if snapshot else None,
        last_synced_at=snapshot.last_synced_at if snapshot else None,
    )


def _query():
    """Reuse one eager-load shape so list and detail paths avoid N+1 queries."""
    return (
        select(AccountWalletBinding)
        .options(
            joinedload(AccountWalletBinding.account),
            joinedload(AccountWalletBinding.wallet),
            joinedload(AccountWalletBinding.balance_snapshot),
        )
        .where(AccountWalletBinding.state != BindingState.ARCHIVED)
    )


@router.get("", response_model=BalanceListResponse)
def list_balances(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
) -> BalanceListResponse:
    """List the latest balance cache for every active binding."""
    bindings = session.scalars(
        _query()
        .order_by(AccountWalletBinding.created_at, AccountWalletBinding.id)
        .offset(offset)
        .limit(limit)
    ).unique().all()
    total = session.scalar(
        select(func.count())
        .select_from(AccountWalletBinding)
        .where(AccountWalletBinding.state != BindingState.ARCHIVED)
    ) or 0
    return BalanceListResponse(
        items=[_response(binding) for binding in bindings],
        offset=offset,
        limit=limit,
        total=total,
    )


@router.get("/{binding_id}", response_model=BalanceResponse)
def get_balance(binding_id: UUID, session: Session = Depends(get_db)) -> BalanceResponse:
    """Return the latest balance for one binding."""
    binding = session.scalars(_query().where(AccountWalletBinding.id == binding_id)).unique().one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="binding not found")
    return _response(binding)


@router.post("/sync", response_model=BalanceSyncResponse, status_code=202)
def queue_balance_sync(
    request: BalanceSyncRequest,
    session: Session = Depends(get_db),
) -> BalanceSyncResponse:
    """Queue isolated read-only account-summary jobs for selected bindings."""
    query = _query().where(AccountWalletBinding.state == BindingState.BOUND)
    if request.binding_ids:
        query = query.where(AccountWalletBinding.id.in_(request.binding_ids))
    bindings = session.scalars(query).unique().all()
    if request.binding_ids and len(bindings) != len(set(request.binding_ids)):
        raise HTTPException(status_code=404, detail="one or more bindings not found or unbound")

    task_service = TaskService(session)
    task_ids: list[UUID] = []
    try:
        for binding in bindings:
            # 每次手动同步使用唯一目标，避免被历史成功任务的幂等键复用。
            result = task_service.create(
                TaskKind.BALANCE_SYNC,
                binding_id=binding.id,
                external_target=f"kredo:account-summary:{uuid4()}",
                priority=request.priority,
            )
            task_ids.append(result.job.id)
    except TaskError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return BalanceSyncResponse(task_ids=task_ids, queued=len(task_ids))
