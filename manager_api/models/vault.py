"""Vault metadata and wrapped-key persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, UUIDPrimaryKeyMixin


class VaultMetadata(UUIDPrimaryKeyMixin, Base):
    """Exactly one active vault record with no plaintext key material."""

    __tablename__ = "vault_metadata"
    __table_args__ = (UniqueConstraint("singleton_key", name="uq_vault_metadata_singleton_key"),)

    singleton_key: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    format_version: Mapped[int] = mapped_column(default=1, nullable=False)
    password_kdf: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    recovery_kdf: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    password_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    recovery_salt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_with_password: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    wrapped_with_recovery: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    initialized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
