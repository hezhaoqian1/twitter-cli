"""Immutable account-wallet binding records."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from . import balances as _balances  # noqa: F401
from .enums import StringEnum

if TYPE_CHECKING:
    from .accounts import SocialAccount
    from .balances import KredoBalanceSnapshot
    from .wallets import Wallet


class BindingState(StringEnum):
    """Binding lifecycle states."""

    PENDING = "pending"
    BOUND = "bound"
    ARCHIVED = "archived"


class AccountWalletBinding(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Historical pairing with database-level active uniqueness."""

    __tablename__ = "account_wallet_bindings"
    __table_args__ = (
        UniqueConstraint(
            "social_account_id",
            "wallet_id",
            "binding_key",
            name="uq_account_wallet_bindings_pair_key",
        ),
        Index(
            "ix_one_active_binding_per_account",
            "social_account_id",
            unique=True,
            postgresql_where="archived_at IS NULL",
            sqlite_where=sa.text("archived_at IS NULL"),
        ),
        Index(
            "ix_one_active_binding_per_wallet",
            "wallet_id",
            unique=True,
            sqlite_where=sa.text("archived_at IS NULL"),
        ),
    )

    social_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    wallet_id: Mapped[UUID] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    binding_key: Mapped[str] = mapped_column(String(96), nullable=False)
    state: Mapped[BindingState] = mapped_column(default=BindingState.PENDING, nullable=False)
    bound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_reference: Mapped[str | None] = mapped_column(String(512))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    account: Mapped["SocialAccount"] = relationship(back_populates="bindings")
    wallet: Mapped["Wallet"] = relationship(back_populates="bindings")
    balance_snapshot: Mapped["KredoBalanceSnapshot | None"] = relationship(
        back_populates="binding",
        uselist=False,
        cascade="all, delete-orphan",
    )
