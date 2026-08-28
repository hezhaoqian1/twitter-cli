"""Immutable account-wallet binding endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...api.dependencies import get_db
from ...schemas.balances import BalanceResponse
from ...schemas.bindings import (
    BindingConfirmRequest,
    BindingCreateRequest,
    BindingListResponse,
    BindingResponse,
)
from ...services.bindings import (
    BindingConflictError,
    BindingError,
    BindingNotFoundError,
    BindingService,
    BindingView,
)

router = APIRouter(prefix="/api/bindings", tags=["bindings"])


def _response(view: BindingView) -> BindingResponse:
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
    return _response(view)


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
    return _response(view)


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
    return _response(view)


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
    return _response(view)


@router.get("", response_model=BindingListResponse)
def list_bindings(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
) -> BindingListResponse:
    """List binding history for table views."""
    views, total = BindingService(session).list(offset=offset, limit=limit)
    return BindingListResponse(
        items=[_response(view) for view in views],
        offset=offset,
        limit=limit,
        total=total,
    )
