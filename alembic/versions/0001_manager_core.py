"""Create manager core tables.

Revision ID: 0001_manager_core
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from manager_api.models.accounts import AccountHealth, LifecycleState
from manager_api.models.bindings import BindingState
from manager_api.models.imports import ImportRowStatus
from manager_api.models.tasks import TaskKind, TaskState
from manager_api.models.wallets import WalletSourceType

revision: str = "0001_manager_core"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(enum_type: type) -> sa.Enum:
    """Build portable string-backed enums for PostgreSQL and SQLite."""
    return sa.Enum(
        *[member.value for member in enum_type],
        name=enum_type.__name__.lower(),
        native_enum=False,
        metadata=None,
    )


def upgrade() -> None:
    """Create all manager durable-state tables and indexes."""
    uuid = sa.Uuid()
    timestamp = sa.DateTime(timezone=True)

    op.create_table(
        "social_accounts",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("handle", sa.String(64), nullable=False),
        sa.Column("normalized_handle", sa.String(64), nullable=False),
        sa.Column("email_masked", sa.String(320)),
        sa.Column("state", _enum(LifecycleState), nullable=False),
        sa.Column("health", _enum(AccountHealth), nullable=False),
        sa.Column("source_label", sa.String(255)),
        sa.Column("last_verified_at", timestamp),
        sa.Column("archived_at", timestamp),
        sa.UniqueConstraint("normalized_handle", name="uq_social_accounts_normalized_handle"),
    )
    op.create_index("ix_social_accounts_state_created_at", "social_accounts", ["state", "created_at"])

    op.create_table(
        "account_secrets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("social_account_id", uuid, sa.ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("envelope", sa.LargeBinary(), nullable=False),
        sa.Column("envelope_version", sa.Integer(), nullable=False),
        sa.Column("secret_fingerprint", sa.String(64), nullable=False),
        sa.Column("redacted_metadata", sa.Text()),
        sa.UniqueConstraint("social_account_id", "version", name="uq_account_secrets_account_version"),
    )
    op.create_index("ix_account_secrets_current", "account_secrets", ["social_account_id", "is_current"])

    op.create_table(
        "wallet_sources",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("source_type", _enum(WalletSourceType), nullable=False),
        sa.Column("label", sa.String(255)),
        sa.Column("derivation_path", sa.String(128)),
        sa.Column("encrypted_source_ref", sa.LargeBinary(), nullable=False),
        sa.Column("envelope_version", sa.Integer(), nullable=False),
        sa.Column("archived_at", timestamp),
    )
    op.create_table(
        "wallets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("wallet_source_id", uuid, sa.ForeignKey("wallet_sources.id", ondelete="SET NULL")),
        sa.Column("address", sa.String(42), nullable=False),
        sa.Column("normalized_address", sa.String(42), nullable=False),
        sa.Column("derivation_path", sa.String(128)),
        sa.Column("derivation_index", sa.Integer()),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("archived_at", timestamp),
        sa.UniqueConstraint("normalized_address", name="uq_wallets_normalized_address"),
    )
    op.create_index("ix_wallets_state_created_at", "wallets", ["state", "created_at"])
    op.create_table(
        "wallet_secrets",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("wallet_id", uuid, sa.ForeignKey("wallets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("envelope", sa.LargeBinary(), nullable=False),
        sa.Column("envelope_version", sa.Integer(), nullable=False),
        sa.Column("secret_fingerprint", sa.String(64), nullable=False),
        sa.Column("redacted_metadata", sa.Text()),
        sa.UniqueConstraint("wallet_id", "version", name="uq_wallet_secrets_wallet_version"),
    )
    op.create_index("ix_wallet_secrets_current", "wallet_secrets", ["wallet_id", "is_current"])

    op.create_table(
        "account_wallet_bindings",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("social_account_id", uuid, sa.ForeignKey("social_accounts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("wallet_id", uuid, sa.ForeignKey("wallets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("binding_key", sa.String(96), nullable=False),
        sa.Column("state", _enum(BindingState), nullable=False),
        sa.Column("bound_at", timestamp),
        sa.Column("external_reference", sa.String(512)),
        sa.Column("archived_at", timestamp),
        sa.UniqueConstraint("social_account_id", "wallet_id", "binding_key", name="uq_account_wallet_bindings_pair_key"),
    )
    op.create_index(
        "ix_one_active_binding_per_account",
        "account_wallet_bindings",
        ["social_account_id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
        sqlite_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_one_active_binding_per_wallet",
        "account_wallet_bindings",
        ["wallet_id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
        sqlite_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "import_batches",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("source_name", sa.String(255)),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("committed_rows", sa.Integer(), nullable=False),
        sa.Column("skipped_rows", sa.Integer(), nullable=False),
        sa.Column("malformed_rows", sa.Integer(), nullable=False),
    )
    op.create_table(
        "import_rows",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("import_batch_id", uuid, sa.ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("status", _enum(ImportRowStatus), nullable=False),
        sa.Column("handle_masked", sa.String(64)),
        sa.Column("email_masked", sa.String(320)),
        sa.Column("diagnostic_code", sa.String(64)),
        sa.Column("diagnostic_detail", sa.Text()),
        sa.Column("result_metadata", sa.JSON()),
        sa.Column("social_account_id", uuid, sa.ForeignKey("social_accounts.id", ondelete="SET NULL")),
        sa.UniqueConstraint("import_batch_id", "line_number", name="uq_import_rows_batch_line"),
    )
    op.create_index("ix_import_rows_status", "import_rows", ["status"])

    op.create_table(
        "task_batches",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", _enum(TaskKind), nullable=False),
        sa.Column("dispatch_limit", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("paused_at", timestamp),
    )
    op.create_table(
        "task_jobs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column("task_batch_id", uuid, sa.ForeignKey("task_batches.id", ondelete="SET NULL")),
        sa.Column("kind", _enum(TaskKind), nullable=False),
        sa.Column("state", _enum(TaskState), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("social_account_id", uuid, sa.ForeignKey("social_accounts.id", ondelete="RESTRICT")),
        sa.Column("wallet_id", uuid, sa.ForeignKey("wallets.id", ondelete="RESTRICT")),
        sa.Column("binding_id", uuid, sa.ForeignKey("account_wallet_bindings.id", ondelete="RESTRICT")),
        sa.Column("idempotency_key", sa.String(512), nullable=False),
        sa.Column("lease_keys", sa.JSON(), nullable=False),
        sa.Column("scheduled_at", timestamp, nullable=False),
        sa.Column("started_at", timestamp),
        sa.Column("finished_at", timestamp),
        sa.Column("external_operation_ref", sa.String(512)),
        sa.Column("result_summary", sa.Text()),
        sa.Column("failure_code", sa.String(96)),
        sa.Column("poll_deadline_at", timestamp),
        sa.Column("next_poll_at", timestamp),
        sa.Column("cancel_requested_at", timestamp),
        sa.UniqueConstraint("idempotency_key", name="uq_task_jobs_idempotency_key"),
    )
    op.create_index("ix_task_jobs_dispatch", "task_jobs", ["state", "scheduled_at", "priority"])
    op.create_index("ix_task_jobs_next_poll", "task_jobs", ["state", "next_poll_at"])
    op.create_table(
        "task_events",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("task_job_id", uuid, sa.ForeignKey("task_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("from_state", sa.String(48)),
        sa.Column("to_state", sa.String(48)),
        sa.Column("summary", sa.Text()),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_at", timestamp, nullable=False),
        sa.UniqueConstraint("task_job_id", "sequence", name="uq_task_events_job_sequence"),
    )
    op.create_index("ix_task_events_job_created_at", "task_events", ["task_job_id", "created_at"])
    op.create_table(
        "resource_leases",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("lease_key", sa.String(160), nullable=False),
        sa.Column("task_job_id", uuid, sa.ForeignKey("task_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_token", sa.String(96), nullable=False),
        sa.Column("acquired_at", timestamp, nullable=False),
        sa.Column("expires_at", timestamp, nullable=False),
        sa.UniqueConstraint("lease_key", name="uq_resource_leases_lease_key"),
    )
    op.create_index("ix_resource_leases_expiry", "resource_leases", ["expires_at"])

    op.create_table(
        "audit_logs",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64)),
        sa.Column("resource_id", uuid),
        sa.Column("summary", sa.Text()),
        sa.Column("metadata", sa.JSON()),
        sa.Column("created_at", timestamp, nullable=False),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "vault_metadata",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("singleton_key", sa.String(32), nullable=False),
        sa.Column("format_version", sa.Integer(), nullable=False),
        sa.Column("password_kdf", sa.JSON(), nullable=False),
        sa.Column("recovery_kdf", sa.JSON(), nullable=False),
        sa.Column("password_salt", sa.LargeBinary(), nullable=False),
        sa.Column("recovery_salt", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_with_password", sa.LargeBinary(), nullable=False),
        sa.Column("wrapped_with_recovery", sa.LargeBinary(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("initialized_at", timestamp, nullable=False),
        sa.UniqueConstraint("singleton_key", name="uq_vault_metadata_singleton_key"),
    )


def downgrade() -> None:
    """Drop manager tables in dependency order."""
    for table in (
        "vault_metadata",
        "audit_logs",
        "resource_leases",
        "task_events",
        "task_jobs",
        "task_batches",
        "import_rows",
        "import_batches",
        "account_wallet_bindings",
        "wallet_secrets",
        "wallets",
        "wallet_sources",
        "account_secrets",
        "social_accounts",
    ):
        op.drop_table(table)
