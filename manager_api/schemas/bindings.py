"""Redacted account-wallet binding API contracts."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .balances import BalanceResponse


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


class BindingStageResponse(BaseModel):
    """Per-binding operation readiness derived from redacted task state."""

    model_config = ConfigDict(extra="forbid")

    repost_state: str | None = None
    claim_state: str | None = None
    can_repost: bool
    can_claim: bool
    repost_waiting: bool
    claim_waiting: bool


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
    stage: BindingStageResponse
    balance: BalanceResponse | None = None


class BindingListResponse(BaseModel):
    """Paginated binding history for the operations console."""

    model_config = ConfigDict(extra="forbid")

    items: list[BindingResponse]
    offset: int
    limit: int
    total: int


class ManualWorkbenchRequest(BaseModel):
    """Request for launching local headed browser workbenches."""

    model_config = ConfigDict(extra="forbid")

    binding_ids: list[UUID] = Field(default_factory=list, max_length=10)
    repost_target: str = Field(
        default="https://x.com/Kredofun/status/2092911885209444742",
        min_length=1,
        max_length=512,
    )
    limit: int = Field(default=10, ge=1, le=10)
    timeout_seconds: int = Field(default=45, ge=10, le=300)


class ManualWorkbenchLaunchResponse(BaseModel):
    """One launched browser workbench without secret material."""

    model_config = ConfigDict(extra="forbid")

    binding_id: UUID
    process_id: int
    screenshot: str
    repost_target: str


class ManualWorkbenchResponse(BaseModel):
    """Bulk launch response for semi-automatic browser workbenches."""

    model_config = ConfigDict(extra="forbid")

    launched: int
    items: list[ManualWorkbenchLaunchResponse]
