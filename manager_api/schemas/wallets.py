"""Redacted wallet import, derivation, and list API contracts."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ..models.wallets import WalletSourceType


class WalletImportRequest(BaseModel):
    """Accept wallet material only in a POST body and never echo it."""

    model_config = ConfigDict(extra="forbid")

    source_type: WalletSourceType
    secret: SecretStr = Field(min_length=1)
    label: str | None = Field(default=None, max_length=255)
    start_index: int = Field(default=0, ge=0)
    count: int = Field(default=1, ge=1, le=100)


class WalletDeriveRequest(BaseModel):
    """Request more addresses from an already encrypted mnemonic source."""

    model_config = ConfigDict(extra="forbid")

    start_index: int = Field(default=0, ge=0)
    count: int = Field(default=1, ge=1, le=100)


class WalletPreviewItem(BaseModel):
    """Safe public result for one wallet candidate."""

    model_config = ConfigDict(extra="forbid")

    index: int | None = None
    address: str
    derivation_path: str | None = None
    status: str
    diagnostic_code: str | None = None
    diagnostic_detail: str | None = None


class WalletImportSummary(BaseModel):
    """Aggregate counts for wallet preview or commit."""

    model_config = ConfigDict(extra="forbid")

    total: int
    valid: int
    duplicate_in_file: int
    duplicate_existing: int
    committed: int = 0
    skipped: int = 0


class WalletImportPreviewResponse(BaseModel):
    """Preview response containing public addresses and derivation metadata."""

    model_config = ConfigDict(extra="forbid")

    source_type: WalletSourceType
    label: str | None = None
    summary: WalletImportSummary
    wallets: list[WalletPreviewItem]


class WalletImportCommitResponse(WalletImportPreviewResponse):
    """Commit response with the durable source identifier and safe results."""

    source_id: UUID | None = None


class WalletListItem(BaseModel):
    """Public wallet identity fields for list views."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    address: str
    source_type: WalletSourceType | None = None
    derivation_path: str | None = None
    derivation_index: int | None = None
    state: str
    has_secret: bool
    is_bound: bool


class WalletListResponse(BaseModel):
    """Paginated wallet list without private material or vault envelopes."""

    model_config = ConfigDict(extra="forbid")

    items: list[WalletListItem]
    offset: int
    limit: int
    total: int
