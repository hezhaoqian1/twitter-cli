"""Redacted account-wallet binding API contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BindingCreateRequest(BaseModel):
    """Request one immutable account-wallet pairing intent."""

    model_config = ConfigDict(extra="forbid")

    social_account_id: UUID
    wallet_id: UUID
    binding_key: str | None = Field(default=None, min_length=1, max_length=96)


class BindingConfirmRequest(BaseModel):
    """External confirmation reference used to finalize a pending binding."""

    model_config = ConfigDict(extra="forbid")

    external_reference: str = Field(min_length=1, max_length=512)


class BindingResponse(BaseModel):
    """Safe binding identity and lifecycle response."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    social_account_id: UUID
    wallet_id: UUID
    account_handle: str
    wallet_address: str
    binding_key: str
    state: str
    bound_at: datetime | None = None
    external_reference: str | None = None
    archived_at: datetime | None = None


class BindingListResponse(BaseModel):
    """Paginated binding history for the operations console."""

    model_config = ConfigDict(extra="forbid")

    items: list[BindingResponse]
    offset: int
    limit: int
    total: int
