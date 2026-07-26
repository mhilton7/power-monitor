"""rate assignment lifecycle and observable source runs

Revision ID: 20260725_0017
Revises: 20260725_0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260725_0017"
down_revision = "20260725_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("rate_versions", sa.Column("parent_version_id", sa.String(36)))
    op.create_foreign_key(
        "fk_rate_versions_parent_version",
        "rate_versions",
        "rate_versions",
        ["parent_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_rate_versions_parent_version_id", "rate_versions", ["parent_version_id"])
    op.add_column(
        "rate_versions",
        sa.Column("lifecycle_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("rate_versions", sa.Column("status_before_removal", sa.String(24)))
    op.add_column("rate_versions", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column("rate_versions", sa.Column("removed_by", sa.String(36)))
    op.create_foreign_key(
        "fk_rate_versions_removed_by",
        "rate_versions",
        "users",
        ["removed_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("rate_versions", sa.Column("removal_reason", sa.String(500)))
    op.add_column("rate_versions", sa.Column("restored_at", sa.DateTime(timezone=True)))
    op.add_column("rate_versions", sa.Column("restored_by", sa.String(36)))
    op.create_foreign_key(
        "fk_rate_versions_restored_by",
        "rate_versions",
        "users",
        ["restored_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_rate_versions_removed_at", "rate_versions", ["removed_at"])
    op.execute(
        "UPDATE rate_versions SET status = 'published' WHERE status IN ('active', 'approved')"
    )

    op.add_column(
        "rate_assignments",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("rate_assignments", sa.Column("cancelled_at", sa.DateTime(timezone=True)))
    op.add_column("rate_assignments", sa.Column("cancelled_by", sa.String(36)))
    op.create_foreign_key(
        "fk_rate_assignments_cancelled_by",
        "rate_assignments",
        "users",
        ["cancelled_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column("rate_assignments", sa.Column("cancellation_reason", sa.String(500)))
    op.add_column("rate_assignments", sa.Column("idempotency_key", sa.String(160)))
    op.create_index(
        "ix_rate_assignments_idempotency_key",
        "rate_assignments",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_rate_assignments_cancelled_at", "rate_assignments", ["cancelled_at"])
    op.create_check_constraint(
        "rate_assignment_revision",
        "rate_assignments",
        "revision > 0",
    )

    op.add_column("background_jobs", sa.Column("dedupe_key", sa.String(160)))
    op.add_column("background_jobs", sa.Column("idempotency_key", sa.String(160)))
    op.add_column(
        "background_jobs",
        sa.Column("trigger_type", sa.String(24), nullable=False, server_default="manual"),
    )
    op.create_index("ix_background_jobs_dedupe_key", "background_jobs", ["dedupe_key"])
    op.create_index(
        "ix_background_jobs_idempotency_key",
        "background_jobs",
        ["idempotency_key"],
    )
    op.create_index(
        "uq_background_jobs_active_dedupe",
        "background_jobs",
        ["job_type", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL AND status IN ('queued','running')"),
        sqlite_where=sa.text("dedupe_key IS NOT NULL AND status IN ('queued','running')"),
    )

    op.add_column("rate_source_checks", sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.add_column(
        "rate_source_checks",
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "rate_source_checks",
        sa.Column("artifact_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_rate_source_checks_finished_at", "rate_source_checks", ["finished_at"])

    op.add_column("utility_account_adjustments", sa.Column("reason", sa.String(500)))
    op.add_column(
        "utility_account_adjustments",
        sa.Column("evidence_reference", sa.String(500)),
    )
    op.add_column(
        "utility_account_adjustments",
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
    )
    op.add_column(
        "utility_account_adjustments",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("utility_account_adjustments", sa.Column("updated_by", sa.String(36)))
    op.create_foreign_key(
        "fk_utility_account_adjustments_updated_by",
        "utility_account_adjustments",
        "users",
        ["updated_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "utility_account_adjustments",
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_utility_account_adjustments_status",
        "utility_account_adjustments",
        ["status"],
    )
    op.create_check_constraint(
        "utility_adjustment_status",
        "utility_account_adjustments",
        "status IN ('active','removed')",
    )
    op.create_check_constraint(
        "utility_adjustment_revision",
        "utility_account_adjustments",
        "revision > 0",
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            INSERT INTO permissions
                (code, group_name, label, description, high_risk)
            VALUES
                (
                    'adjustments.manage',
                    'Rates',
                    'Manage account adjustments',
                    'Create, revise, and remove effective-dated utility-account adjustments.',
                    true
                )
            ON CONFLICT (code) DO NOTHING
            """
        )
    )
    bind.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_name, permission_code)
            SELECT roles.name, 'adjustments.manage'
            FROM roles
            WHERE roles.name IN ('admin', 'rate-manager')
            ON CONFLICT DO NOTHING
            """
        )
    )
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION prevent_rate_assignment_overlap()
            RETURNS trigger AS $$
            BEGIN
              -- Existing installations can contain overlaps. Permit a repair
              -- update that cancels or narrows one of those legacy rows; all
              -- inserts and range-expanding updates remain guarded below.
              IF TG_OP = 'UPDATE' AND OLD.cancelled_at IS NULL
                 AND NEW.cancelled_at IS NOT NULL THEN
                RETURN NEW;
              END IF;
              IF TG_OP = 'UPDATE'
                 AND OLD.cancelled_at IS NULL
                 AND NEW.cancelled_at IS NULL
                 AND NEW.effective_from >= OLD.effective_from
                 AND COALESCE(NEW.effective_to, 'infinity'::timestamptz)
                     <= COALESCE(OLD.effective_to, 'infinity'::timestamptz) THEN
                RETURN NEW;
              END IF;
              IF NEW.cancelled_at IS NULL AND EXISTS (
                SELECT 1
                FROM rate_assignments existing
                WHERE existing.utility_account_id = NEW.utility_account_id
                  AND existing.id <> NEW.id
                  AND existing.cancelled_at IS NULL
                  AND tstzrange(
                    existing.effective_from,
                    existing.effective_to,
                    '[)'
                  ) && tstzrange(NEW.effective_from, NEW.effective_to, '[)')
              ) THEN
                RAISE EXCEPTION 'rate_assignment_overlap'
                  USING ERRCODE = '23P01';
              END IF;
              RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER trg_rate_assignment_no_overlap
            BEFORE INSERT OR UPDATE OF utility_account_id, effective_from,
              effective_to, cancelled_at
            ON rate_assignments
            FOR EACH ROW EXECUTE FUNCTION prevent_rate_assignment_overlap()
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_rate_assignment_no_overlap ON rate_assignments")
        op.execute("DROP FUNCTION IF EXISTS prevent_rate_assignment_overlap()")
    bind.execute(
        sa.text("DELETE FROM role_permissions WHERE permission_code = 'adjustments.manage'")
    )
    bind.execute(sa.text("DELETE FROM permissions WHERE code = 'adjustments.manage'"))

    op.drop_constraint(
        "utility_adjustment_revision",
        "utility_account_adjustments",
        type_="check",
    )
    op.drop_constraint(
        "utility_adjustment_status",
        "utility_account_adjustments",
        type_="check",
    )
    op.drop_index(
        "ix_utility_account_adjustments_status",
        table_name="utility_account_adjustments",
    )
    op.drop_column("utility_account_adjustments", "updated_at")
    op.drop_constraint(
        "fk_utility_account_adjustments_updated_by",
        "utility_account_adjustments",
        type_="foreignkey",
    )
    op.drop_column("utility_account_adjustments", "updated_by")
    op.drop_column("utility_account_adjustments", "revision")
    op.drop_column("utility_account_adjustments", "status")
    op.drop_column("utility_account_adjustments", "evidence_reference")
    op.drop_column("utility_account_adjustments", "reason")

    op.drop_index("ix_rate_source_checks_finished_at", table_name="rate_source_checks")
    op.drop_column("rate_source_checks", "artifact_count")
    op.drop_column("rate_source_checks", "candidate_count")
    op.drop_column("rate_source_checks", "finished_at")

    op.drop_index("uq_background_jobs_active_dedupe", table_name="background_jobs")
    op.drop_index("ix_background_jobs_idempotency_key", table_name="background_jobs")
    op.drop_index("ix_background_jobs_dedupe_key", table_name="background_jobs")
    op.drop_column("background_jobs", "trigger_type")
    op.drop_column("background_jobs", "idempotency_key")
    op.drop_column("background_jobs", "dedupe_key")

    op.drop_constraint("rate_assignment_revision", "rate_assignments", type_="check")
    op.drop_index("ix_rate_assignments_idempotency_key", table_name="rate_assignments")
    op.drop_column("rate_assignments", "idempotency_key")
    op.drop_index("ix_rate_assignments_cancelled_at", table_name="rate_assignments")
    op.drop_column("rate_assignments", "cancellation_reason")
    op.drop_constraint(
        "fk_rate_assignments_cancelled_by",
        "rate_assignments",
        type_="foreignkey",
    )
    op.drop_column("rate_assignments", "cancelled_by")
    op.drop_column("rate_assignments", "cancelled_at")
    op.drop_column("rate_assignments", "revision")

    op.drop_index("ix_rate_versions_removed_at", table_name="rate_versions")
    op.drop_constraint("fk_rate_versions_restored_by", "rate_versions", type_="foreignkey")
    op.drop_column("rate_versions", "restored_by")
    op.drop_column("rate_versions", "restored_at")
    op.drop_column("rate_versions", "removal_reason")
    op.drop_constraint("fk_rate_versions_removed_by", "rate_versions", type_="foreignkey")
    op.drop_column("rate_versions", "removed_by")
    op.drop_column("rate_versions", "removed_at")
    op.drop_column("rate_versions", "status_before_removal")
    op.drop_column("rate_versions", "lifecycle_revision")
    op.drop_index("ix_rate_versions_parent_version_id", table_name="rate_versions")
    op.drop_constraint("fk_rate_versions_parent_version", "rate_versions", type_="foreignkey")
    op.drop_column("rate_versions", "parent_version_id")
