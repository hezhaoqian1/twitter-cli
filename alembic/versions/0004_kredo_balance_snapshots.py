"""Add the latest read-only Kredo Points and HSK snapshot."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_kredo_balance_snapshots"
down_revision: Union[str, None] = "0003_task_external_target"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """判断当前迁移是否运行在需要表重建的 SQLite 上。"""
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    """Create one replaceable balance row per immutable account-wallet binding."""
    op.create_table(
        "kredo_balance_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "binding_id",
            sa.Uuid(),
            sa.ForeignKey("account_wallet_bindings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("points", sa.Numeric(24, 8)),
        sa.Column("cash_hsk_available", sa.Numeric(24, 8)),
        sa.Column("positions_value_hsk", sa.Numeric(24, 8)),
        sa.Column("sync_status", sa.String(24), nullable=False, server_default="never"),
        sa.Column("error_code", sa.String(96)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("binding_id", name="uq_kredo_balance_snapshots_binding_id"),
    )
    if _is_sqlite():
        # SQLite 不支持直接移除默认值，只能重建余额快照表。
        with op.batch_alter_table("kredo_balance_snapshots", recreate="always") as batch_op:
            batch_op.alter_column("sync_status", server_default=None)
    else:
        op.alter_column("kredo_balance_snapshots", "sync_status", server_default=None)
    op.create_index(
        "ix_kredo_balance_snapshots_sync",
        "kredo_balance_snapshots",
        ["sync_status", "last_synced_at"],
    )


def downgrade() -> None:
    """Remove only the derived balance cache and keep binding history intact."""
    op.drop_index("ix_kredo_balance_snapshots_sync", table_name="kredo_balance_snapshots")
    op.drop_table("kredo_balance_snapshots")
