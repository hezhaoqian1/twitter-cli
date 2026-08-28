"""Persist restart-safe external task targets."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_task_external_target"
down_revision: Union[str, None] = "0002_task_workflows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """判断当前迁移是否运行在需要表重建的 SQLite 上。"""
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    """Store the normalized public target required by a resumed worker."""
    op.add_column(
        "task_jobs",
        sa.Column("external_target", sa.String(512), nullable=False, server_default=""),
    )
    if _is_sqlite():
        # SQLite 不支持直接移除默认值，只能重建 task_jobs。
        with op.batch_alter_table("task_jobs", recreate="always") as batch_op:
            batch_op.alter_column("external_target", server_default=None)
    else:
        op.alter_column("task_jobs", "external_target", server_default=None)


def downgrade() -> None:
    """Remove the persisted task target."""
    op.drop_column("task_jobs", "external_target")
