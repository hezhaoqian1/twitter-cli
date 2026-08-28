"""Public contracts for redacted Kredo balance snapshots."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BalanceResponse(BaseModel):
    """One binding balance with no account or wallet secret material."""

    model_config = ConfigDict(extra="forbid")

    id: UUID | None = None
    binding_id: UUID
    account_handle: str
    wallet_address: str
    points: Decimal | None = None
    cash_hsk_available: Decimal | None = None
    positions_value_hsk: Decimal | None = None
    total_hsk: Decimal | None = None
    sync_status: str = "never"
    error_code: str | None = None
    last_synced_at: datetime | None = None


class BalanceListResponse(BaseModel):
    """Paginated binding balances for the operations console."""

    model_config = ConfigDict(extra="forbid")

    items: list[BalanceResponse]
    offset: int
    limit: int
    total: int


class BalanceSyncRequest(BaseModel):
    """Request one read-only balance sync for selected or all bound records."""

    model_config = ConfigDict(extra="forbid")

    binding_ids: list[UUID] | None = Field(default=None, max_length=500)
    priority: int = Field(default=0, ge=-100, le=100)


class BalanceSyncResponse(BaseModel):
    """Identifiers of the durable sync jobs created for the request."""

    model_config = ConfigDict(extra="forbid")

    task_ids: list[UUID]
    queued: int
