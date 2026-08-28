"""Redacted account list and health endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...api.dependencies import get_db
from ...models.accounts import SocialAccount
from ...schemas.accounts import AccountListItem, AccountListResponse

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=AccountListResponse)
def list_accounts(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_db),
) -> AccountListResponse:
    """List account identities without selecting encrypted secret columns."""
    total = session.scalar(select(func.count()).select_from(SocialAccount)) or 0
    accounts = session.scalars(
        select(SocialAccount)
        .order_by(SocialAccount.created_at, SocialAccount.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return AccountListResponse(
        items=[
            AccountListItem(
                id=account.id,
                handle=account.handle,
                email_masked=account.email_masked,
                state=account.state.value,
                health=account.health.value,
                has_secret=account.secret is not None,
            )
            for account in accounts
        ],
        offset=offset,
        limit=limit,
        total=total,
    )


@router.get("/{account_id}", response_model=AccountListItem)
def get_account(
    account_id: UUID,
    session: Session = Depends(get_db),
) -> AccountListItem:
    """Return one redacted account identity for a detail side panel."""
    account = session.get(SocialAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return AccountListItem(
        id=account.id,
        handle=account.handle,
        email_masked=account.email_masked,
        state=account.state.value,
        health=account.health.value,
        has_secret=account.secret is not None,
    )
