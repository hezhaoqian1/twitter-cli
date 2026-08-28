"""Wallet import, derivation, and redacted list endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...api.dependencies import get_db, get_vault
from ...models.wallets import Wallet
from ...schemas.wallets import (
    WalletDeriveRequest,
    WalletImportCommitResponse,
    WalletImportPreviewResponse,
    WalletImportRequest,
    WalletImportSummary,
    WalletListItem,
    WalletListResponse,
    WalletPreviewItem,
)
from ...services.vault import VaultService, VaultUnlockError
from ...services.wallets import WalletImportStatus, WalletInputError, WalletPreview, WalletService

router = APIRouter(prefix="/api", tags=["wallets"])


def _preview_items(preview: WalletPreview) -> list[WalletPreviewItem]:
    """Convert internal candidates to public-only response objects."""
    return [
        WalletPreviewItem(
            index=decision.candidate.index,
            address=decision.candidate.address,
            derivation_path=decision.candidate.derivation_path,
            status=decision.status.value,
            diagnostic_code=decision.diagnostic_code,
            diagnostic_detail=decision.diagnostic_detail,
        )
        for decision in preview.decisions
    ]


def _summary(preview: WalletPreview, *, committed: int = 0) -> WalletImportSummary:
    """Build a response summary from redacted service decisions."""
    return WalletImportSummary(**preview.summary(committed=committed))


def _preview_response(preview: WalletPreview) -> WalletImportPreviewResponse:
    """Build a preview response without source material or ciphertext."""
    return WalletImportPreviewResponse(
        source_type=preview.source_type,
        label=preview.label,
        summary=_summary(preview),
        wallets=_preview_items(preview),
    )


@router.post("/wallet-sources/preview", response_model=WalletImportPreviewResponse)
def preview_wallet_source(
    request: WalletImportRequest,
    session: Session = Depends(get_db),
) -> WalletImportPreviewResponse:
    """Validate and derive wallet candidates without persistence."""
    try:
        preview = WalletService(session).preview(
            request.source_type,
            request.secret.get_secret_value(),
            label=request.label,
            start_index=request.start_index,
            count=request.count,
        )
    except WalletInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _preview_response(preview)


@router.post("/wallet-sources", response_model=WalletImportCommitResponse)
def commit_wallet_source(
    request: WalletImportRequest,
    session: Session = Depends(get_db),
    vault: VaultService = Depends(get_vault),
) -> WalletImportCommitResponse:
    """Encrypt and persist accepted wallet source and private keys."""
    service = WalletService(session, vault=vault)
    try:
        source, preview = service.commit(
            request.source_type,
            request.secret.get_secret_value(),
            label=request.label,
            start_index=request.start_index,
            count=request.count,
        )
    except WalletInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VaultUnlockError as exc:
        raise HTTPException(status_code=423, detail="vault is locked") from exc
    committed = sum(
        decision.status is WalletImportStatus.COMMITTED for decision in preview.decisions
    )
    return WalletImportCommitResponse(
        source_id=source.id if source is not None else None,
        source_type=preview.source_type,
        label=preview.label,
        summary=_summary(preview, committed=committed),
        wallets=_preview_items(preview),
    )


@router.post("/wallet-sources/{source_id}/derive", response_model=WalletImportCommitResponse)
def derive_wallet_source(
    source_id: UUID,
    request: WalletDeriveRequest,
    session: Session = Depends(get_db),
    vault: VaultService = Depends(get_vault),
) -> WalletImportCommitResponse:
    """Derive more addresses from an encrypted mnemonic source."""
    service = WalletService(session, vault=vault)
    try:
        source, preview = service.derive(
            source_id,
            start_index=request.start_index,
            count=request.count,
        )
    except WalletInputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except VaultUnlockError as exc:
        raise HTTPException(status_code=423, detail="vault is locked") from exc
    committed = sum(
        decision.status is WalletImportStatus.COMMITTED for decision in preview.decisions
    )
    return WalletImportCommitResponse(
        source_id=source.id,
        source_type=source.source_type,
        label=source.label,
        summary=_summary(preview, committed=committed),
        wallets=_preview_items(preview),
    )


@router.get("/wallets", response_model=WalletListResponse)
def list_wallets(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
) -> WalletListResponse:
    """List public wallet identities without selecting secret envelopes."""
    total = session.scalar(select(func.count()).select_from(Wallet)) or 0
    wallets = session.scalars(
        select(Wallet)
        .order_by(Wallet.created_at, Wallet.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return WalletListResponse(
        items=[
            WalletListItem(
                id=wallet.id,
                address=wallet.address,
                source_type=wallet.source.source_type if wallet.source is not None else None,
                derivation_path=wallet.derivation_path,
                derivation_index=wallet.derivation_index,
                state=wallet.state,
                has_secret=wallet.secret is not None,
                is_bound=bool(wallet.bindings),
            )
            for wallet in wallets
        ],
        offset=offset,
        limit=limit,
        total=total,
    )
