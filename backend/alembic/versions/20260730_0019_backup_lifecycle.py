"""durable backup lifecycle, verification evidence, and global single flight

Revision ID: 20260730_0019
Revises: 20260729_0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260730_0019"
down_revision = "20260729_0018"
branch_labels = None
depends_on = None


BACKUP_JOB_TYPES = "'backup_create','backup_verify','backup_restore_preflight','backup_delete'"


def upgrade() -> None:
    op.add_column("backup_runs", sa.Column("requested_by", sa.String(36)))
    op.create_foreign_key(
        "fk_backup_runs_requested_by",
        "backup_runs",
        "users",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_backup_runs_requested_by", "backup_runs", ["requested_by"])
    op.add_column(
        "backup_runs",
        sa.Column("trigger_type", sa.String(24), nullable=False, server_default="manual"),
    )
    op.add_column("backup_runs", sa.Column("size_bytes", sa.BigInteger()))
    op.add_column(
        "backup_runs",
        sa.Column("encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("backup_runs", sa.Column("verification_started_at", sa.DateTime(timezone=True)))
    op.add_column("backup_runs", sa.Column("verification_completed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "backup_runs",
        sa.Column("verification_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("backup_runs", sa.Column("failed_stage", sa.String(80)))
    op.add_column("backup_runs", sa.Column("safe_error_code", sa.String(80)))
    op.add_column("backup_runs", sa.Column("safe_error_summary", sa.String(500)))
    op.add_column("backup_runs", sa.Column("exit_code", sa.Integer()))
    op.add_column("backup_runs", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.create_index("ix_backup_runs_deleted_at", "backup_runs", ["deleted_at"])
    op.add_column("backup_runs", sa.Column("deleted_by", sa.String(36)))
    op.create_foreign_key(
        "fk_backup_runs_deleted_by",
        "backup_runs",
        "users",
        ["deleted_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("backup_runs", sa.Column("deletion_reason", sa.String(500)))
    op.add_column("backup_runs", sa.Column("original_size_bytes", sa.BigInteger()))
    op.add_column("backup_runs", sa.Column("artifact_removal_result", sa.String(80)))
    op.add_column(
        "backup_runs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        """
        UPDATE backup_runs
        SET status = CASE
            WHEN status = 'running' THEN 'backup_failed'
            WHEN status = 'failed' THEN 'backup_failed'
            WHEN status = 'completed' AND verified_at IS NOT NULL THEN 'verified'
            WHEN status = 'completed' THEN 'completed_unverified'
            ELSE status
        END,
        safe_error_code = CASE
            WHEN status = 'running' THEN 'INTERRUPTED_CREATE'
            ELSE safe_error_code
        END,
        safe_error_summary = CASE
            WHEN status = 'running' THEN 'Backup creation was interrupted before this upgrade'
            ELSE safe_error_summary
        END
        """
    )
    op.create_index(
        "uq_background_jobs_active_backup_operation",
        "background_jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key = 'backup:global' AND status IN ('queued','running')"),
        sqlite_where=sa.text("dedupe_key = 'backup:global' AND status IN ('queued','running')"),
    )
    op.create_index(
        "uq_background_jobs_backup_idempotency",
        "background_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            f"idempotency_key IS NOT NULL AND job_type IN ({BACKUP_JOB_TYPES})"
        ),
        sqlite_where=sa.text(f"idempotency_key IS NOT NULL AND job_type IN ({BACKUP_JOB_TYPES})"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_backup_idempotency", table_name="background_jobs")
    op.drop_index("uq_background_jobs_active_backup_operation", table_name="background_jobs")
    op.drop_column("backup_runs", "updated_at")
    op.drop_column("backup_runs", "artifact_removal_result")
    op.drop_column("backup_runs", "original_size_bytes")
    op.drop_column("backup_runs", "deletion_reason")
    op.drop_constraint("fk_backup_runs_deleted_by", "backup_runs", type_="foreignkey")
    op.drop_column("backup_runs", "deleted_by")
    op.drop_index("ix_backup_runs_deleted_at", table_name="backup_runs")
    op.drop_column("backup_runs", "deleted_at")
    op.drop_column("backup_runs", "exit_code")
    op.drop_column("backup_runs", "safe_error_summary")
    op.drop_column("backup_runs", "safe_error_code")
    op.drop_column("backup_runs", "failed_stage")
    op.drop_column("backup_runs", "verification_attempt_count")
    op.drop_column("backup_runs", "verification_completed_at")
    op.drop_column("backup_runs", "verification_started_at")
    op.drop_column("backup_runs", "encrypted")
    op.drop_column("backup_runs", "size_bytes")
    op.drop_column("backup_runs", "trigger_type")
    op.drop_index("ix_backup_runs_requested_by", table_name="backup_runs")
    op.drop_constraint("fk_backup_runs_requested_by", "backup_runs", type_="foreignkey")
    op.drop_column("backup_runs", "requested_by")
