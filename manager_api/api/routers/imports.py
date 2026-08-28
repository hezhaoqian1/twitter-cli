"""Account import HTTP endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...api.dependencies import get_db, get_vault
from ...schemas.accounts import (
    AccountImportCommitResponse,
    AccountImportPreviewResponse,
    AccountImportRequest,
    AccountImportRowResponse,
    AccountImportSummary,
)
from ...services.imports import AccountImportService, ImportPreview, summarize_preview
from ...services.vault import VaultService, VaultUnlockError

router = APIRouter(prefix="/api/imports", tags=["imports"])


def _row_response(
    preview: ImportPreview,
    *,
    committed: bool = False,
) -> list[AccountImportRowResponse]:
    return [
        AccountImportRowResponse(
            line_number=decision.line_number,
            status=(
                "committed"
                if committed and decision.status.value == "valid"
                else decision.status.value
            ),
            handle_masked=decision.handle_masked,
            email_masked=decision.email_masked,
            diagnostic_code=decision.diagnostic_code,
            diagnostic_detail=decision.diagnostic_detail,
        )
        for decision in preview.decisions
    ]


def _summary(preview: ImportPreview, *, committed_rows: int = 0, skipped_rows: int | None = None) -> AccountImportSummary:
    counts = summarize_preview(preview)
    counts["committed_rows"] = committed_rows
    if skipped_rows is not None:
        counts["skipped_rows"] = skipped_rows
    return AccountImportSummary(**counts)


@router.post("/accounts/preview", response_model=AccountImportPreviewResponse)
def preview_accounts(
    request: AccountImportRequest,
    session: Session = Depends(get_db),
) -> AccountImportPreviewResponse:
    """Return redacted validation results without touching the database."""
    preview = AccountImportService(session).preview(request.content.get_secret_value())
    return AccountImportPreviewResponse(
        source_sha256=preview.source_sha256,
        summary=_summary(preview),
        rows=_row_response(preview),
    )


@router.post("/accounts/commit", response_model=AccountImportCommitResponse)
def commit_accounts(
    request: AccountImportRequest,
    session: Session = Depends(get_db),
    vault: VaultService = Depends(get_vault),
) -> AccountImportCommitResponse:
    """Persist valid rows after encrypting each secret field."""
    try:
        batch, preview = AccountImportService(session, vault).commit(
            request.content.get_secret_value(),
            source_name=request.source_name,
        )
    except VaultUnlockError as exc:
        raise HTTPException(status_code=423, detail="vault is locked") from exc
    return AccountImportCommitResponse(
        import_batch_id=batch.id,
        source_sha256=batch.source_sha256,
        summary=_summary(
            preview,
            committed_rows=batch.committed_rows,
            skipped_rows=batch.skipped_rows,
        ),
        rows=_row_response(preview, committed=True),
    )
