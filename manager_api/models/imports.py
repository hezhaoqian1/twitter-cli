"""Import batches and per-line outcomes."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from .enums import StringEnum

if TYPE_CHECKING:
    from .accounts import SocialAccount


class ImportRowStatus(StringEnum):
    """Normalized result for one submitted import row."""

    VALID = "valid"
    MALFORMED = "malformed"
    DUPLICATE_IN_FILE = "duplicate_in_file"
    EXISTING_ACCOUNT = "existing_account"
    CONFLICTING_SESSION = "conflicting_session"
    COMMITTED = "committed"
    SKIPPED = "skipped"


class ImportBatch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable import request summary without plaintext source data."""

    __tablename__ = "import_batches"

    source_name: Mapped[str | None] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    malformed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rows: Mapped[list[ImportRow]] = relationship(
        back_populates="batch",
        cascade="all, delete-orphan",
    )


class ImportRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Redacted outcome for a single input line."""

    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("import_batch_id", "line_number", name="uq_import_rows_batch_line"),
        Index("ix_import_rows_status", "status"),
    )

    import_batch_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ImportRowStatus] = mapped_column(nullable=False)
    handle_masked: Mapped[str | None] = mapped_column(String(64))
    email_masked: Mapped[str | None] = mapped_column(String(320))
    diagnostic_code: Mapped[str | None] = mapped_column(String(64))
    diagnostic_detail: Mapped[str | None] = mapped_column(Text)
    result_metadata: Mapped[dict[str, object] | None] = mapped_column(JSON)
    social_account_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("social_accounts.id", ondelete="SET NULL")
    )

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")
    account: Mapped["SocialAccount | None"] = relationship(back_populates="import_rows")
