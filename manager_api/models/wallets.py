"""Wallet source metadata, public addresses, and encrypted key material."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .enums import StringEnum

if TYPE_CHECKING:
    from .bindings import AccountWalletBinding


class WalletSourceType(StringEnum):
    """Supported wallet import sources."""

    PRIVATE_KEY = "private_key"
    MNEMONIC = "mnemonic"


class WalletSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Metadata for a private key or mnemonic held in the encrypted vault."""

    __tablename__ = "wallet_sources"

    source_type: Mapped[WalletSourceType] = mapped_column(nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    derivation_path: Mapped[str | None] = mapped_column(String(128))
    encrypted_source_ref: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    envelope_version: Mapped[int] = mapped_column(default=1, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    wallets: Mapped[list[Wallet]] = relationship(back_populates="source")


class Wallet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Public wallet address and non-secret derivation metadata."""

    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint("normalized_address", name="uq_wallets_normalized_address"),
        Index("ix_wallets_state_created_at", "state", "created_at"),
    )

    wallet_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("wallet_sources.id", ondelete="SET NULL")
    )
    address: Mapped[str] = mapped_column(String(42), nullable=False)
    normalized_address: Mapped[str] = mapped_column(String(42), nullable=False)
    derivation_path: Mapped[str | None] = mapped_column(String(128))
    derivation_index: Mapped[int | None] = mapped_column()
    state: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[WalletSource | None] = relationship(back_populates="wallets")
    secret: Mapped[WalletSecret | None] = relationship(
        back_populates="wallet",
        uselist=False,
        cascade="all, delete-orphan",
    )
    bindings: Mapped[list[AccountWalletBinding]] = relationship(back_populates="wallet")


class WalletSecret(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Encrypted private material or source reference for one wallet."""

    __tablename__ = "wallet_secrets"
    __table_args__ = (
        UniqueConstraint("wallet_id", "version", name="uq_wallet_secrets_wallet_version"),
        Index("ix_wallet_secrets_current", "wallet_id", "is_current"),
    )

    wallet_id: Mapped[UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(nullable=False)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False)
    envelope: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    envelope_version: Mapped[int] = mapped_column(default=1, nullable=False)
    secret_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_metadata: Mapped[str | None] = mapped_column(Text)

    wallet: Mapped[Wallet] = relationship(back_populates="secret")
