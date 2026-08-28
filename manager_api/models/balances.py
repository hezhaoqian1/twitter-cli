"""Latest redacted Kredo balance snapshot for each immutable binding."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from .bindings import AccountWalletBinding


class BalanceSyncStatus(str):
    """String values used by the latest balance snapshot."""

    SUCCESS = "success"
    ERROR = "error"
    NEVER = "never"


class KredoBalanceSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One replaceable balance snapshot scoped to one account-wallet binding."""

    __tablename__ = "kredo_balance_snapshots"
    __table_args__ = (
        UniqueConstraint("binding_id", name="uq_kredo_balance_snapshots_binding_id"),
        Index("ix_kredo_balance_snapshots_sync", "sync_status", "last_synced_at"),
    )

    binding_id: Mapped[UUID] = mapped_column(
        ForeignKey("account_wallet_bindings.id", ondelete="CASCADE"),
        nullable=False,
    )
    points: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    cash_hsk_available: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    positions_value_hsk: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    sync_status: Mapped[str] = mapped_column(
        String(24),
        default=BalanceSyncStatus.NEVER,
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(96))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    binding: Mapped["AccountWalletBinding"] = relationship(back_populates="balance_snapshot")
