"""Social account identity and encrypted session material."""

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
    from .imports import ImportRow


class LifecycleState(StringEnum):
    """Lifecycle values shared by imported resources."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class AccountHealth(StringEnum):
    """Normalized account-session health values."""

    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    INVALID = "invalid"


class SocialAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Non-secret X account identity and lifecycle state."""

    __tablename__ = "social_accounts"
    __table_args__ = (
        UniqueConstraint("normalized_handle", name="uq_social_accounts_normalized_handle"),
        Index("ix_social_accounts_state_created_at", "state", "created_at"),
    )

    handle: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_handle: Mapped[str] = mapped_column(String(64), nullable=False)
    email_masked: Mapped[str | None] = mapped_column(String(320))
    state: Mapped[LifecycleState] = mapped_column(default=LifecycleState.ACTIVE, nullable=False)
    health: Mapped[AccountHealth] = mapped_column(default=AccountHealth.UNKNOWN, nullable=False)
    source_label: Mapped[str | None] = mapped_column(String(255))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    secret: Mapped[AccountSecret | None] = relationship(
        back_populates="account",
        uselist=False,
        cascade="all, delete-orphan",
    )
    bindings: Mapped[list[AccountWalletBinding]] = relationship(back_populates="account")
    import_rows: Mapped[list[ImportRow]] = relationship(back_populates="account")


class AccountSecret(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Encrypted account credentials and browser session material."""

    __tablename__ = "account_secrets"
    __table_args__ = (
        UniqueConstraint("social_account_id", "version", name="uq_account_secrets_account_version"),
        Index("ix_account_secrets_current", "social_account_id", "is_current"),
    )

    social_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(nullable=False)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False)
    envelope: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    envelope_version: Mapped[int] = mapped_column(default=1, nullable=False)
    secret_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    redacted_metadata: Mapped[str | None] = mapped_column(Text)

    account: Mapped[SocialAccount] = relationship(back_populates="secret")
