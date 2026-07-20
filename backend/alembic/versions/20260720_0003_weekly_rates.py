"""Add weekly SCE evidence, candidate review, and custom-rate lifecycles.

Revision ID: 20260720_0003
Revises: 20260720_0002
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0003"
down_revision: str | None = "20260720_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_columns() -> None:
    op.add_column("audit_events", sa.Column("correlation_id", sa.String(128)))
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])
    op.add_column(
        "utility_accounts",
        sa.Column("provider_mode", sa.String(32), nullable=False, server_default="sce_bundled"),
    )
    op.add_column(
        "utility_accounts",
        sa.Column(
            "cost_scope_default", sa.String(40), nullable=False, server_default="energy_only"
        ),
    )

    for column in (
        sa.Column("plan_kind", sa.String(32), nullable=False, server_default="official_sce"),
        sa.Column("ownership_scope", sa.String(32), nullable=False, server_default="global"),
        sa.Column("owner_site_id", sa.String(36), sa.ForeignKey("sites.id", ondelete="CASCADE")),
        sa.Column(
            "owner_utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="CASCADE"),
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="America/Los_Angeles"),
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "cloned_from_rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="SET NULL", use_alter=True),
        ),
    ):
        op.add_column("rate_plans", column)
    op.create_index("ix_rate_plans_owner_site_id", "rate_plans", ["owner_site_id"])
    op.create_index(
        "ix_rate_plans_owner_utility_account_id", "rate_plans", ["owner_utility_account_id"]
    )

    for column in (
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("source_kind", sa.String(32), nullable=False, server_default="custom"),
        sa.Column("source_checked_at", sa.DateTime(timezone=True)),
        sa.Column("source_label", sa.String(240)),
        sa.Column("change_summary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("approved_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("activated_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("normalized_payload", sa.JSON()),
        sa.Column(
            "automatically_activated", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    ):
        op.add_column("rate_versions", column)
    op.create_index("ix_rate_versions_status", "rate_versions", ["status"])
    op.execute(
        "UPDATE rate_versions SET status = CASE WHEN is_active THEN 'active' ELSE 'retired' END, "
        "source_kind = 'official_sce', "
        "source_checked_at = source_checked_on::timestamp with time zone"
    )

    op.add_column(
        "rate_seasons", sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "rate_seasons",
        sa.Column("leap_day_behavior", sa.String(32), nullable=False, server_default="include"),
    )
    for column in (
        sa.Column("delivery_per_kwh", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("generation_per_kwh", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("adjustment_per_kwh", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    ):
        op.add_column("rate_periods", column)
    op.execute("UPDATE rate_periods SET delivery_per_kwh = price_per_kwh")
    for column in (
        sa.Column("unit", sa.String(32), nullable=False, server_default="per_kwh"),
        sa.Column("scope", sa.String(40), nullable=False, server_default="all_energy"),
        sa.Column("eligibility", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("effective_from", sa.Date()),
        sa.Column("effective_to", sa.Date()),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    ):
        op.add_column("rate_adjustments", column)
    op.add_column(
        "cost_interval_results",
        sa.Column(
            "adjustment_breakdown", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column(
        "cost_interval_results",
        sa.Column(
            "calculation_version", sa.String(40), nullable=False, server_default="rate-engine/1"
        ),
    )


def _create_tables() -> None:
    op.create_table(
        "rate_sync_configuration",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("schedule_cron", sa.String(64), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("jitter_minutes", sa.Integer(), nullable=False),
        sa.Column("approval_mode", sa.String(32), nullable=False),
        sa.Column("auto_activate_verified", sa.Boolean(), nullable=False),
        sa.Column("last_scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("next_scheduled_run", sa.DateTime(timezone=True)),
        sa.Column("last_attempted_run", sa.DateTime(timezone=True)),
        sa.Column("last_successful_run", sa.DateTime(timezone=True)),
        sa.Column("last_source_change", sa.DateTime(timezone=True)),
        sa.Column("last_candidate_created", sa.DateTime(timezone=True)),
        sa.Column("last_approved_version", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_table(
        "rate_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("url", sa.String(500), nullable=False, unique=True),
        sa.Column("parser_id", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("etag", sa.String(500)),
        sa.Column("last_modified", sa.String(200)),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.Text()),
    )
    for name, columns in (
        ("ix_background_jobs_job_type", ["job_type"]),
        ("ix_background_jobs_status", ["status"]),
        ("ix_background_jobs_requested_at", ["requested_at"]),
        ("ix_background_jobs_scheduled_for", ["scheduled_for"]),
        ("ix_background_jobs_correlation_id", ["correlation_id"]),
    ):
        op.create_index(name, "background_jobs", columns)
    op.create_table(
        "rate_source_checks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "job_id",
            sa.String(36),
            sa.ForeignKey("background_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rate_source_id",
            sa.String(36),
            sa.ForeignKey("rate_sources.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("final_url", sa.String(500)),
        sa.Column("etag", sa.String(500)),
        sa.Column("last_modified", sa.String(200)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("response_bytes", sa.Integer()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_detail", sa.Text()),
    )
    for column in ("job_id", "rate_source_id", "checked_at", "outcome"):
        op.create_index(f"ix_rate_source_checks_{column}", "rate_source_checks", [column])
    op.create_table(
        "rate_source_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "source_check_id",
            sa.String(36),
            sa.ForeignKey("rate_source_checks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("original_filename", sa.String(255)),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("source_check_id", "sha256", "captured_at"):
        op.create_index(f"ix_rate_source_artifacts_{column}", "rate_source_artifacts", [column])
    op.create_table(
        "rate_extraction_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("rate_source_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("parser_id", sa.String(80), nullable=False),
        sa.Column("parser_version", sa.String(40), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("normalized_payload", sa.JSON()),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_rate_extraction_results_artifact_id", "rate_extraction_results", ["artifact_id"]
    )
    op.create_index("ix_rate_extraction_results_status", "rate_extraction_results", ["status"])
    op.create_table(
        "rate_change_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "rate_plan_id", sa.String(36), sa.ForeignKey("rate_plans.id", ondelete="SET NULL")
        ),
        sa.Column(
            "extraction_result_id",
            sa.String(36),
            sa.ForeignKey("rate_extraction_results.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "base_rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "candidate_rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="SET NULL"),
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("risk_level", sa.String(24), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    for column in ("rate_plan_id", "extraction_result_id", "status", "created_at"):
        op.create_index(f"ix_rate_change_candidates_{column}", "rate_change_candidates", [column])
    op.create_table(
        "rate_candidate_differences",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("rate_change_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("change_type", sa.String(24), nullable=False),
        sa.Column("before_value", sa.JSON()),
        sa.Column("after_value", sa.JSON()),
        sa.Column("material", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_rate_candidate_differences_candidate_id", "rate_candidate_differences", ["candidate_id"]
    )
    op.create_table(
        "rate_approval_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("rate_change_candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column(
            "decided_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_rate_approval_decisions_candidate_id", "rate_approval_decisions", ["candidate_id"]
    )
    op.create_index(
        "ix_rate_approval_decisions_decided_at", "rate_approval_decisions", ["decided_at"]
    )
    op.create_table(
        "rate_version_sources",
        sa.Column(
            "rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "artifact_id",
            sa.String(36),
            sa.ForeignKey("rate_source_artifacts.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "extraction_result_id",
            sa.String(36),
            sa.ForeignKey("rate_extraction_results.id", ondelete="SET NULL"),
        ),
        sa.Column("relationship", sa.String(32), nullable=False),
    )
    op.create_table(
        "rate_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("assigned_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("utility_account_id", "rate_version_id", "effective_from"):
        op.create_index(f"ix_rate_assignments_{column}", "rate_assignments", [column])


def upgrade() -> None:
    _add_columns()
    _create_tables()
    op.execute(
        "INSERT INTO rate_sync_configuration "
        "(id, enabled, schedule_cron, timezone, jitter_minutes, approval_mode, "
        "auto_activate_verified, updated_at) VALUES "
        "('default', true, '15 3 * * 0', 'America/Los_Angeles', 20, 'manual_review', false, now())"
    )


def downgrade() -> None:
    for table in (
        "rate_assignments",
        "rate_version_sources",
        "rate_approval_decisions",
        "rate_candidate_differences",
        "rate_change_candidates",
        "rate_extraction_results",
        "rate_source_artifacts",
        "rate_source_checks",
        "background_jobs",
        "rate_sources",
        "rate_sync_configuration",
    ):
        op.drop_table(table)
    for table, columns in (
        ("cost_interval_results", ("calculation_version", "adjustment_breakdown")),
        (
            "rate_adjustments",
            (
                "description",
                "display_order",
                "effective_to",
                "effective_from",
                "eligibility",
                "scope",
                "unit",
            ),
        ),
        (
            "rate_periods",
            ("display_order", "adjustment_per_kwh", "generation_per_kwh", "delivery_per_kwh"),
        ),
        ("rate_seasons", ("leap_day_behavior", "priority")),
        (
            "rate_versions",
            (
                "automatically_activated",
                "normalized_payload",
                "activated_at",
                "activated_by",
                "approved_at",
                "approved_by",
                "change_summary",
                "source_label",
                "source_checked_at",
                "source_kind",
                "status",
            ),
        ),
        (
            "rate_plans",
            (
                "cloned_from_rate_version_id",
                "created_by",
                "status",
                "timezone",
                "currency",
                "owner_utility_account_id",
                "owner_site_id",
                "ownership_scope",
                "plan_kind",
            ),
        ),
        ("utility_accounts", ("cost_scope_default", "provider_mode")),
    ):
        for column in columns:
            op.drop_column(table, column)
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_column("audit_events", "correlation_id")
