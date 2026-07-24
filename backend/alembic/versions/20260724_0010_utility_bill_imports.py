"""Add private utility-bill extraction, review, and billing-cycle drafts.

Revision ID: 20260724_0010
Revises: 20260723_0009
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260724_0010"
down_revision = "20260723_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "utility_bill_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("background_jobs.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("rate_source_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="review_required"),
        sa.Column("source_role", sa.String(40), nullable=False, server_default="supporting"),
        sa.Column("extraction_method", sa.String(16), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("retention_mode", sa.String(32), nullable=False, server_default="retain"),
        sa.Column("retain_until", sa.DateTime(timezone=True)),
        sa.Column("original_deleted_at", sa.DateTime(timezone=True)),
        sa.Column("sanitized_evidence_path", sa.String(1000), nullable=False),
        sa.Column(
            "rate_plan_id",
            sa.String(36),
            sa.ForeignKey("rate_plans.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "blocking_warnings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "extraction_warnings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewed_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "utility_account_id", "content_sha256", name="uq_utility_bill_import_account_hash"
        ),
        sa.CheckConstraint(
            "status IN ('processing','review_required','ready_to_publish','published',"
            "'rejected','failed')",
            name="utility_bill_import_status",
        ),
        sa.CheckConstraint(
            "source_role IN ('supporting','authoritative_account_specific','reference_only')",
            name="utility_bill_import_source_role",
        ),
        sa.CheckConstraint(
            "extraction_method IN ('text','ocr','mixed')",
            name="utility_bill_import_extraction_method",
        ),
        sa.CheckConstraint(
            "retention_mode IN ('retain','retain_until','delete_after_approval')",
            name="utility_bill_import_retention",
        ),
        sa.CheckConstraint("page_count > 0", name="utility_bill_import_page_count"),
        sa.CheckConstraint("revision > 0", name="utility_bill_import_revision"),
    )
    for columns, name in (
        (["job_id"], "ix_utility_bill_imports_job_id"),
        (["utility_account_id"], "ix_utility_bill_imports_utility_account_id"),
        (["artifact_id"], "ix_utility_bill_imports_artifact_id"),
        (["content_sha256"], "ix_utility_bill_imports_content_sha256"),
        (["status"], "ix_utility_bill_imports_status"),
        (["rate_plan_id"], "ix_utility_bill_imports_rate_plan_id"),
        (["rate_version_id"], "ix_utility_bill_imports_rate_version_id"),
        (["created_at"], "ix_utility_bill_imports_created_at"),
    ):
        op.create_index(name, "utility_bill_imports", columns)

    op.create_table(
        "utility_bill_extraction_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "bill_import_id",
            sa.String(36),
            sa.ForeignKey("utility_bill_imports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="review_required"),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("ocr_version", sa.String(80)),
        sa.Column(
            "normalized_account_data",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "normalized_rate_data",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "normalized_cycle_data",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("raw_text_sha256", sa.String(64), nullable=False),
        sa.Column("normalized_text_sha256", sa.String(64), nullable=False),
        sa.Column("sanitized_text_path", sa.String(1000), nullable=False),
        sa.Column(
            "extraction_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "bill_import_id", "revision", name="uq_utility_bill_extraction_revision"
        ),
        sa.CheckConstraint(
            "status IN ('review_required','approved','superseded','failed')",
            name="utility_bill_extraction_status",
        ),
        sa.CheckConstraint("revision > 0", name="utility_bill_extraction_revision_positive"),
    )
    op.create_index(
        "ix_utility_bill_extraction_revisions_bill_import_id",
        "utility_bill_extraction_revisions",
        ["bill_import_id"],
    )
    op.create_index(
        "ix_utility_bill_extraction_revisions_created_at",
        "utility_bill_extraction_revisions",
        ["created_at"],
    )

    op.create_table(
        "utility_bill_extracted_fields",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "extraction_revision_id",
            sa.String(36),
            sa.ForeignKey("utility_bill_extraction_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("output_kind", sa.String(24), nullable=False),
        sa.Column("field_key", sa.String(240), nullable=False),
        sa.Column("raw_value", sa.JSON()),
        sa.Column("normalized_value", sa.JSON()),
        sa.Column("corrected_value", sa.JSON()),
        sa.Column("page_number", sa.Integer()),
        sa.Column("text_region", sa.JSON()),
        sa.Column("source_excerpt", sa.Text()),
        sa.Column("extraction_method", sa.String(16), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("confidence", sa.String(32), nullable=False),
        sa.Column("review_state", sa.String(24), nullable=False, server_default="unreviewed"),
        sa.Column(
            "warnings",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "normalization_history",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "confirmed_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "extraction_revision_id",
            "output_kind",
            "field_key",
            name="uq_utility_bill_extracted_field",
        ),
        sa.CheckConstraint(
            "output_kind IN ('account','rate_plan','billing_cycle')",
            name="utility_bill_field_output_kind",
        ),
        sa.CheckConstraint(
            "extraction_method IN ('text','ocr','mixed','administrator')",
            name="utility_bill_field_method",
        ),
        sa.CheckConstraint(
            "confidence IN ('administrator_confirmed','high','medium','low','missing',"
            "'conflicts_current','conflicts_source','not_applicable')",
            name="utility_bill_field_confidence",
        ),
        sa.CheckConstraint(
            "review_state IN ('unreviewed','confirmed','corrected','rejected')",
            name="utility_bill_field_review_state",
        ),
    )
    op.create_index(
        "ix_utility_bill_extracted_fields_extraction_revision_id",
        "utility_bill_extracted_fields",
        ["extraction_revision_id"],
    )
    op.create_index(
        "ix_utility_bill_extracted_fields_output_kind",
        "utility_bill_extracted_fields",
        ["output_kind"],
    )
    op.create_index(
        "ix_utility_bill_extracted_fields_confidence",
        "utility_bill_extracted_fields",
        ["confidence"],
    )

    op.create_table(
        "utility_bill_field_conflicts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "bill_import_id",
            sa.String(36),
            sa.ForeignKey("utility_bill_imports.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_key", sa.String(240), nullable=False),
        sa.Column("extracted_value", sa.JSON()),
        sa.Column("configured_value", sa.JSON()),
        sa.Column("comparison_source", sa.String(120), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="unresolved"),
        sa.Column("blocking", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("resolution_note", sa.String(1000)),
        sa.Column(
            "resolved_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "bill_import_id",
            "field_key",
            "comparison_source",
            name="uq_utility_bill_field_conflict",
        ),
        sa.CheckConstraint(
            "status IN ('unresolved','accepted_bill','accepted_configured','dismissed')",
            name="utility_bill_conflict_status",
        ),
    )
    op.create_index(
        "ix_utility_bill_field_conflicts_bill_import_id",
        "utility_bill_field_conflicts",
        ["bill_import_id"],
    )
    op.create_index(
        "ix_utility_bill_field_conflicts_status",
        "utility_bill_field_conflicts",
        ["status"],
    )

    op.create_table(
        "utility_bill_cycle_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "bill_import_id",
            sa.String(36),
            sa.ForeignKey("utility_bill_imports.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "extraction_revision_id",
            sa.String(36),
            sa.ForeignKey("utility_bill_extraction_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("cycle_days", sa.Integer()),
        sa.Column("meter_read_date", sa.Date()),
        sa.Column("total_usage_kwh", sa.Numeric(24, 9)),
        sa.Column(
            "usage_by_tier",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "usage_by_tou",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "meter_records",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("current_tier", sa.String(120)),
        sa.Column("projected_tier", sa.String(120)),
        sa.Column("energy_subtotal", sa.Numeric(24, 12)),
        sa.Column("full_bill_total", sa.Numeric(24, 12)),
        sa.Column("fixed_charges", sa.Numeric(24, 12)),
        sa.Column("taxes_fees", sa.Numeric(24, 12)),
        sa.Column("credits", sa.Numeric(24, 12)),
        sa.Column("adjustments", sa.Numeric(24, 12)),
        sa.Column(
            "threshold_interpretation",
            sa.String(40),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "reconciliation_status",
            sa.String(32),
            nullable=False,
            server_default="not_compared",
        ),
        sa.Column(
            "billing_cycle_id",
            sa.String(36),
            sa.ForeignKey("billing_cycles.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "utility_usage_import_id",
            sa.String(36),
            sa.ForeignKey("utility_usage_imports.id", ondelete="SET NULL"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "reviewed_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('draft','approved','imported','rejected')",
            name="utility_bill_cycle_draft_status",
        ),
        sa.CheckConstraint(
            "threshold_interpretation IN ('fixed_cycle_threshold','daily_baseline',"
            "'baseline_multiplier','unknown')",
            name="utility_bill_cycle_threshold_interpretation",
        ),
        sa.CheckConstraint(
            "reconciliation_status IN ('not_compared','matched','difference','adjusted')",
            name="utility_bill_cycle_reconciliation",
        ),
        sa.CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="utility_bill_cycle_window",
        ),
        sa.CheckConstraint(
            "total_usage_kwh IS NULL OR total_usage_kwh >= 0",
            name="utility_bill_cycle_usage_nonnegative",
        ),
        sa.CheckConstraint("revision > 0", name="utility_bill_cycle_revision"),
    )
    for columns, name in (
        (["bill_import_id"], "ix_utility_bill_cycle_drafts_bill_import_id"),
        (["extraction_revision_id"], "ix_utility_bill_cycle_drafts_extraction_revision_id"),
        (["utility_account_id"], "ix_utility_bill_cycle_drafts_utility_account_id"),
        (["status"], "ix_utility_bill_cycle_drafts_status"),
        (["billing_cycle_id"], "ix_utility_bill_cycle_drafts_billing_cycle_id"),
        (["utility_usage_import_id"], "ix_utility_bill_cycle_drafts_usage_import_id"),
    ):
        op.create_index(name, "utility_bill_cycle_drafts", columns)

    permissions = (
        (
            "utility_bills.view",
            "View utility bill imports",
            "View private utility-bill extraction evidence and comparison history.",
            True,
        ),
        (
            "utility_bills.manage",
            "Manage utility bill imports",
            "Upload, review, publish, retain, and delete private utility-bill artifacts.",
            True,
        ),
    )
    for code, label, description, high_risk in permissions:
        op.execute(
            sa.text(
                "INSERT INTO permissions (code, group_name, label, description, high_risk) "
                "VALUES (:code, 'Rates and billing', :label, :description, :high_risk)"
            ).bindparams(
                code=code,
                label=label,
                description=description,
                high_risk=high_risk,
            )
        )
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', :code)"
            ).bindparams(code=code)
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission_code IN "
        "('utility_bills.view','utility_bills.manage')"
    )
    op.execute(
        "DELETE FROM permissions WHERE code IN ('utility_bills.view','utility_bills.manage')"
    )
    op.drop_table("utility_bill_cycle_drafts")
    op.drop_table("utility_bill_field_conflicts")
    op.drop_table("utility_bill_extracted_fields")
    op.drop_table("utility_bill_extraction_revisions")
    op.drop_table("utility_bill_imports")
