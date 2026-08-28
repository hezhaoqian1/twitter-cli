"""Redacted request and response schemas for vault operations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class VaultPasswordRequest(BaseModel):
    """Request body used by password unlock routes."""

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
