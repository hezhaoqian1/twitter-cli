"""Add ordered task workflow metadata."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_task_workflows"
down_revision: Union[str, None] = "0001_manager_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_sqlite() -> bool:
    """判断当前迁移是否运行在需要表重建的 SQLite 上。"""
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    """Store workflow type and a single durable predecessor per task."""
    op.add_column(
        "task_batches",
        sa.Column("workflow_type", sa.String(64), nullable=False, server_default="single"),
    )
    dependency_column = sa.Column(
        "depends_on_task_id",
        sa.Uuid(),
        nullable=True,
    )
    if _is_sqlite():
        # SQLite 不支持对现有表直接添加自引用外键，只能重建 task_jobs。
        with op.batch_alter_table("task_jobs", recreate="always") as batch_op:
            batch_op.add_column(dependency_column)
            batch_op.create_foreign_key(
                "fk_task_jobs_depends_on_task_id",
                "task_jobs",
                ["depends_on_task_id"],
                ["id"],
                ondelete="RESTRICT",
            )
    else:
        # PostgreSQL 原地加约束，避免重建表时碰到 task_events/resource_leases 的外键依赖。
        op.add_column("task_jobs", dependency_column)
        op.create_foreign_key(
            "fk_task_jobs_depends_on_task_id",
            "task_jobs",
            "task_jobs",
            ["depends_on_task_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_task_jobs_dependency_state",
        "task_jobs",
        ["depends_on_task_id", "state"],
    )


def downgrade() -> None:
    """Remove workflow metadata while retaining the original task tables."""
    op.drop_index("ix_task_jobs_dependency_state", table_name="task_jobs")
    if _is_sqlite():
        with op.batch_alter_table("task_jobs", recreate="always") as batch_op:
            batch_op.drop_constraint("fk_task_jobs_depends_on_task_id", type_="foreignkey")
            batch_op.drop_column("depends_on_task_id")
    else:
        op.drop_constraint(
            "fk_task_jobs_depends_on_task_id",
            "task_jobs",
            type_="foreignkey",
        )
        op.drop_column("task_jobs", "depends_on_task_id")
    op.drop_column("task_batches", "workflow_type")
