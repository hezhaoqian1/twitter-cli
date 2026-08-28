"""Redacted request and response schemas for vault operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VaultPasswordRequest(BaseModel):
    """Request body used by password unlock routes."""

    model_config = ConfigDict(extra="forbid")

    password: SecretStr = Field(min_length=1)


class VaultInitializeRequest(BaseModel):
    """Request body used during first-run vault setup."""

    model_config = ConfigDict(extra="forbid")

    password: SecretStr = Field(min_length=1)


class VaultRecoveryKeyRequest(BaseModel):
    """Request body used by recovery-key unlock routes."""

    model_config = ConfigDict(extra="forbid")

    recovery_key: SecretStr = Field(min_length=1)


class VaultInitializeResponse(BaseModel):
    """One-time setup response; the recovery key is returned only here."""

    model_config = ConfigDict(extra="forbid")

    initialized: bool
    recovery_key: str


class VaultStatusResponse(BaseModel):
    """Non-sensitive vault status for the management UI."""

    model_config = ConfigDict(extra="forbid")

    initialized: bool
    unlocked: bool
    initialized_at: datetime | None = None


class VaultBackupResponse(BaseModel):
    """Non-sensitive summary returned after creating or validating a backup."""

    model_config = ConfigDict(extra="forbid")

    format_version: int
    table_count: int
    row_count: int
    vault_recovery_key_valid: bool
    checksums_valid: bool


class VaultBackupCreateResponse(VaultBackupResponse):
    """Backup metadata returned alongside a downloadable package."""

    filename: str


class VaultRestoreResponse(VaultBackupResponse):
    """Restore result after inserting a verified package into an empty database."""

    restored: bool
