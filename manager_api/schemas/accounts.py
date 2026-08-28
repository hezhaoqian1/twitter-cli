"""Redacted account import and account-list API contracts."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class AccountImportRequest(BaseModel):
    """Accept raw TSV content without ever echoing it in a response."""

    model_config = ConfigDict(extra="forbid")

    content: SecretStr = Field(min_length=1)
    source_name: str | None = Field(default=None, max_length=255)


class AccountImportRowResponse(BaseModel):
    """Expose only safe diagnostics for one imported line."""

    model_config = ConfigDict(extra="forbid")

    line_number: int
    status: str
    handle_masked: str | None = None
    email_masked: str | None = None
    diagnostic_code: str | None = None
    diagnostic_detail: str | None = None


class AccountImportSummary(BaseModel):
    """Aggregate import counts returned by preview and commit."""

    model_config = ConfigDict(extra="forbid")

    total_rows: int
    valid_rows: int
    malformed_rows: int
    duplicate_rows: int
    existing_rows: int
    conflicting_rows: int
    committed_rows: int = 0
    skipped_rows: int = 0


class AccountImportPreviewResponse(BaseModel):
    """Safe preview response with no source content or encrypted payloads."""

    model_config = ConfigDict(extra="forbid")

    source_sha256: str
    summary: AccountImportSummary
    rows: list[AccountImportRowResponse]


class AccountImportCommitResponse(BaseModel):
    """Safe commit response containing the durable batch identifier."""

    model_config = ConfigDict(extra="forbid")

    import_batch_id: UUID
    source_sha256: str
    summary: AccountImportSummary
    rows: list[AccountImportRowResponse]


class AccountListItem(BaseModel):
    """Public account identity fields for list views."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    handle: str
    email_masked: str | None = None
    state: str
    health: str
    has_secret: bool


class AccountListResponse(BaseModel):
    """Paginated account list without secret columns."""

    model_config = ConfigDict(extra="forbid")

    items: list[AccountListItem]
    offset: int
    limit: int
    total: int
