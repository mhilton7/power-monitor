"""safe replacement backup workflow and audit tombstones

Revision ID: 20260730_0020
Revises: 20260730_0019
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260730_0020"
down_revision = "20260730_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backup_runs", sa.Column("pre_deletion_status", sa.String(24)))
    op.add_column("backup_runs", sa.Column("replaced_by_backup_id", sa.String(36)))
    op.create_foreign_key(
        "fk_backup_runs_replaced_by_backup_id",
        "backup_runs",
        "backup_runs",
        ["replaced_by_backup_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_backup_runs_replaced_by_backup_id",
        "backup_runs",
        ["replaced_by_backup_id"],
    )
    op.drop_index("uq_background_jobs_backup_idempotency", table_name="background_jobs")
    backup_job_types = (
        "'backup_create','backup_verify','backup_restore_preflight',"
        "'backup_delete','backup_replace_all'"
    )
    op.create_index(
        "uq_background_jobs_backup_idempotency",
        "background_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            f"idempotency_key IS NOT NULL AND job_type IN ({backup_job_types})"
        ),
        sqlite_where=sa.text(f"idempotency_key IS NOT NULL AND job_type IN ({backup_job_types})"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_backup_idempotency", table_name="background_jobs")
    backup_job_types = "'backup_create','backup_verify','backup_restore_preflight','backup_delete'"
    op.create_index(
        "uq_background_jobs_backup_idempotency",
        "background_jobs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text(
            f"idempotency_key IS NOT NULL AND job_type IN ({backup_job_types})"
        ),
        sqlite_where=sa.text(f"idempotency_key IS NOT NULL AND job_type IN ({backup_job_types})"),
    )
    op.drop_index("ix_backup_runs_replaced_by_backup_id", table_name="backup_runs")
    op.drop_constraint(
        "fk_backup_runs_replaced_by_backup_id",
        "backup_runs",
        type_="foreignkey",
    )
    op.drop_column("backup_runs", "replaced_by_backup_id")
    op.drop_column("backup_runs", "pre_deletion_status")
