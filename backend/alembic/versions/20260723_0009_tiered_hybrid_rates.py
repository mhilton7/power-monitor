"""Add billing-cycle tiered and hybrid pricing.

Revision ID: 20260723_0009
Revises: 20260721_0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260723_0009"
down_revision = "20260721_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rate_versions",
        sa.Column(
            "pricing_model",
            sa.String(32),
            nullable=False,
            server_default="time_of_use",
        ),
    )
    op.create_index("ix_rate_versions_pricing_model", "rate_versions", ["pricing_model"])
    op.create_check_constraint(
        "rate_version_pricing_model",
        "rate_versions",
        "pricing_model IN ('flat','time_of_use','tiered','time_of_use_tiered')",
    )

    op.create_table(
        "rate_tier_definitions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stable_tier_id", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("lower_bound_kwh", sa.Numeric(20, 9), nullable=False),
        sa.Column("upper_bound_kwh", sa.Numeric(20, 9)),
        sa.Column("lower_bound_multiplier", sa.Numeric(16, 8)),
        sa.Column("upper_bound_multiplier", sa.Numeric(16, 8)),
        sa.Column("price_per_kwh", sa.Numeric(14, 8), nullable=False),
        sa.Column("tou_prices", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("season_name", sa.String(80)),
        sa.Column("source_citation", sa.String(500)),
        sa.UniqueConstraint("rate_version_id", "stable_tier_id", name="uq_rate_tier_stable_id"),
        sa.UniqueConstraint("rate_version_id", "display_order", name="uq_rate_tier_order"),
        sa.CheckConstraint("display_order >= 0", name="rate_tier_order_nonnegative"),
        sa.CheckConstraint("lower_bound_kwh >= 0", name="rate_tier_lower_nonnegative"),
        sa.CheckConstraint(
            "upper_bound_kwh IS NULL OR upper_bound_kwh > lower_bound_kwh",
            name="rate_tier_bounds",
        ),
        sa.CheckConstraint("price_per_kwh >= 0", name="rate_tier_price_nonnegative"),
    )
    op.create_index(
        "ix_rate_tier_definitions_rate_version_id",
        "rate_tier_definitions",
        ["rate_version_id"],
    )

    op.create_table(
        "rate_threshold_rules",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("basis", sa.String(32), nullable=False, server_default="fixed_cycle_kwh"),
        sa.Column("daily_baseline_kwh", sa.Numeric(18, 9)),
        sa.Column("baseline_region", sa.String(120)),
        sa.Column("baseline_category", sa.String(120)),
        sa.Column("rounding_policy", sa.String(32), nullable=False, server_default="none"),
        sa.Column("expected_cycle_start_day", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source_citation", sa.String(500)),
        sa.CheckConstraint(
            "basis IN ('fixed_cycle_kwh','daily_baseline_kwh')",
            name="rate_threshold_basis",
        ),
        sa.CheckConstraint(
            "rounding_policy IN ('none','nearest_kwh','floor_kwh','ceil_kwh')",
            name="rate_threshold_rounding",
        ),
        sa.CheckConstraint(
            "expected_cycle_start_day >= 1 AND expected_cycle_start_day <= 31",
            name="rate_threshold_cycle_day",
        ),
    )
    op.create_index(
        "ix_rate_threshold_rules_rate_version_id",
        "rate_threshold_rules",
        ["rate_version_id"],
    )

    op.create_table(
        "rate_seasonal_baselines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("start_month", sa.Integer(), nullable=False),
        sa.Column("start_day", sa.Integer(), nullable=False),
        sa.Column("end_month", sa.Integer(), nullable=False),
        sa.Column("end_day", sa.Integer(), nullable=False),
        sa.Column("daily_kwh", sa.Numeric(18, 9), nullable=False),
        sa.Column("source_citation", sa.String(500)),
        sa.UniqueConstraint("rate_version_id", "name", name="uq_rate_seasonal_baseline_name"),
        sa.CheckConstraint("daily_kwh > 0", name="rate_seasonal_baseline_positive"),
    )
    op.create_index(
        "ix_rate_seasonal_baselines_rate_version_id",
        "rate_seasonal_baselines",
        ["rate_version_id"],
    )

    for column in (
        sa.Column("status", sa.String(24), nullable=False, server_default="expected"),
        sa.Column("boundary_source", sa.String(32), nullable=False, server_default="generated"),
        sa.Column("override_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recalculation_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_snapshot_hash", sa.String(64)),
        sa.Column("created_by", sa.String(36)),
        sa.Column("updated_by", sa.String(36)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ):
        op.add_column("billing_cycles", column)
    op.create_foreign_key(
        "fk_billing_cycles_created_by_users",
        "billing_cycles",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_billing_cycles_updated_by_users",
        "billing_cycles",
        "users",
        ["updated_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_billing_cycles_status", "billing_cycles", ["status"])
    op.create_unique_constraint(
        "uq_billing_cycle_account_window",
        "billing_cycles",
        ["utility_account_id", "starts_at", "ends_at"],
    )
    op.create_check_constraint("billing_cycle_window", "billing_cycles", "ends_at > starts_at")
    op.create_check_constraint(
        "billing_cycle_status",
        "billing_cycles",
        "status IN ('expected','confirmed','recalculating','finalized')",
    )
    op.create_check_constraint(
        "billing_cycle_boundary_source",
        "billing_cycles",
        "boundary_source IN ('generated','manual_override','utility_import','external_feed')",
    )

    op.create_table(
        "account_usage_authorities",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("authority_type", sa.String(48), nullable=False),
        sa.Column(
            "aggregate_set_id",
            sa.String(36),
            sa.ForeignKey("aggregate_sets.id", ondelete="SET NULL"),
        ),
        sa.Column("device_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("source_reference", sa.String(500)),
        sa.Column("confidence", sa.String(24), nullable=False, server_default="unverified"),
        sa.Column("complete_account", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "authority_type IN ('complete_site_aggregate','service_leg_pair',"
            "'whole_account_meter','utility_interval_import','manual_cycle_usage',"
            "'external_feed','partial_monitored_circuits')",
            name="account_usage_authority_type",
        ),
        sa.CheckConstraint(
            "confidence IN ('unverified','low','medium','high','utility_verified')",
            name="account_usage_authority_confidence",
        ),
    )
    op.create_index(
        "ix_account_usage_authorities_utility_account_id",
        "account_usage_authorities",
        ["utility_account_id"],
    )

    op.create_table(
        "manual_account_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "billing_cycle_id",
            sa.String(36),
            sa.ForeignKey("billing_cycles.id", ondelete="RESTRICT"),
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cumulative_kwh", sa.Numeric(24, 9), nullable=False),
        sa.Column("source_note", sa.String(500), nullable=False),
        sa.Column("evidence_reference", sa.String(500)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column(
            "verification_status",
            sa.String(24),
            nullable=False,
            server_default="unverified",
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("cumulative_kwh >= 0", name="manual_account_usage_nonnegative"),
        sa.CheckConstraint(
            "verification_status IN ('unverified','verified','reconciled')",
            name="manual_account_usage_verification",
        ),
        sa.UniqueConstraint(
            "utility_account_id",
            "idempotency_key",
            name="uq_manual_usage_idempotency",
        ),
    )
    op.create_index(
        "ix_manual_account_usage_utility_account_id",
        "manual_account_usage",
        ["utility_account_id"],
    )
    op.create_index(
        "ix_manual_account_usage_billing_cycle_id",
        "manual_account_usage",
        ["billing_cycle_id"],
    )
    op.create_index(
        "ix_manual_account_usage_effective_at",
        "manual_account_usage",
        ["effective_at"],
    )

    op.create_table(
        "utility_usage_imports",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("import_kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="preview"),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("source_name", sa.String(240), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("field_mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "normalized_rows",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "utility_account_id",
            "content_sha256",
            name="uq_utility_usage_import_content",
        ),
        sa.CheckConstraint(
            "import_kind IN ('interval','daily','cycle_cumulative','cycle_dates','bill_total')",
            name="utility_usage_import_kind",
        ),
        sa.CheckConstraint(
            "status IN ('preview','committed','rejected','reversed')",
            name="utility_usage_import_status",
        ),
    )
    op.create_index(
        "ix_utility_usage_imports_utility_account_id",
        "utility_usage_imports",
        ["utility_account_id"],
    )
    op.create_index(
        "ix_utility_usage_imports_content_sha256",
        "utility_usage_imports",
        ["content_sha256"],
    )

    op.create_table(
        "tier_allocation_segments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "billing_cycle_id",
            sa.String(36),
            sa.ForeignKey("billing_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "normalized_interval_id",
            sa.String(36),
            sa.ForeignKey("normalized_intervals.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "import_id",
            sa.String(36),
            sa.ForeignKey("utility_usage_imports.id", ondelete="RESTRICT"),
        ),
        sa.Column("segment_order", sa.Integer(), nullable=False),
        sa.Column("interval_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "rate_version_id",
            sa.String(36),
            sa.ForeignKey("rate_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tier_definition_id",
            sa.String(36),
            sa.ForeignKey("rate_tier_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tier_stable_id", sa.String(80), nullable=False),
        sa.Column("tier_name", sa.String(120), nullable=False),
        sa.Column("tou_period", sa.String(80)),
        sa.Column("cumulative_start_kwh", sa.Numeric(24, 9), nullable=False),
        sa.Column("cumulative_end_kwh", sa.Numeric(24, 9), nullable=False),
        sa.Column("segment_energy_kwh", sa.Numeric(20, 9), nullable=False),
        sa.Column("price_per_kwh", sa.Numeric(14, 8), nullable=False),
        sa.Column("unrounded_energy_charge", sa.Numeric(24, 12), nullable=False),
        sa.Column("derived_threshold_kwh", sa.Numeric(20, 9)),
        sa.Column("usage_authority_type", sa.String(48), nullable=False),
        sa.Column("quality_flags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("recalculation_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "billing_cycle_id",
            "normalized_interval_id",
            "segment_order",
            "recalculation_version",
            name="uq_tier_segment_interval_recalc",
        ),
        sa.CheckConstraint("segment_energy_kwh >= 0", name="tier_segment_energy_nonnegative"),
        sa.CheckConstraint(
            "cumulative_end_kwh >= cumulative_start_kwh",
            name="tier_segment_cumulative_order",
        ),
    )
    for columns, name in (
        (["billing_cycle_id"], "ix_tier_allocation_segments_billing_cycle_id"),
        (["utility_account_id"], "ix_tier_allocation_segments_utility_account_id"),
        (["normalized_interval_id"], "ix_tier_allocation_segments_normalized_interval_id"),
        (["interval_start"], "ix_tier_allocation_segments_interval_start"),
        (["rate_version_id"], "ix_tier_allocation_segments_rate_version_id"),
    ):
        op.create_index(name, "tier_allocation_segments", columns)

    op.create_table(
        "cycle_tier_summaries",
        sa.Column(
            "billing_cycle_id",
            sa.String(36),
            sa.ForeignKey("billing_cycles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("tier_stable_id", sa.String(80), primary_key=True),
        sa.Column("recalculation_version", sa.Integer(), primary_key=True),
        sa.Column("tier_name", sa.String(120), nullable=False),
        sa.Column("lower_bound_kwh", sa.Numeric(20, 9), nullable=False),
        sa.Column("upper_bound_kwh", sa.Numeric(20, 9)),
        sa.Column("usage_kwh", sa.Numeric(20, 9), nullable=False),
        sa.Column("energy_charge", sa.Numeric(24, 12), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "tier_projection_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "billing_cycle_id",
            sa.String(36),
            sa.ForeignKey("billing_cycles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("projected_usage_kwh", sa.Numeric(20, 9), nullable=False),
        sa.Column("projected_energy_charge", sa.Numeric(24, 12), nullable=False),
        sa.Column("projected_tier_stable_id", sa.String(80)),
        sa.Column("confidence", sa.String(24), nullable=False),
        sa.Column("coverage_percent", sa.Numeric(7, 4), nullable=False),
    )
    op.create_index(
        "ix_tier_projection_snapshots_billing_cycle_id",
        "tier_projection_snapshots",
        ["billing_cycle_id"],
    )
    op.create_index(
        "ix_tier_projection_snapshots_calculated_at",
        "tier_projection_snapshots",
        ["calculated_at"],
    )

    op.create_table(
        "account_reconciliation_adjustments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "billing_cycle_id",
            sa.String(36),
            sa.ForeignKey("billing_cycles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("component", sa.String(48), nullable=False),
        sa.Column("amount", sa.Numeric(18, 8), nullable=False),
        sa.Column("notes", sa.String(1000), nullable=False),
        sa.Column("provenance", sa.String(500), nullable=False),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_account_reconciliation_adjustments_utility_account_id",
        "account_reconciliation_adjustments",
        ["utility_account_id"],
    )
    op.create_index(
        "ix_account_reconciliation_adjustments_billing_cycle_id",
        "account_reconciliation_adjustments",
        ["billing_cycle_id"],
    )

    permissions = (
        (
            "costs.recalculate",
            "Recalculate costs",
            "Recalculate unfinalized billing-cycle cost allocations.",
            True,
        ),
        (
            "usage_imports.manage",
            "Manage utility usage imports",
            "Preview, commit, reconcile, and reverse utility usage imports.",
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
        "('costs.recalculate','usage_imports.manage')"
    )
    op.execute("DELETE FROM permissions WHERE code IN ('costs.recalculate','usage_imports.manage')")
    op.drop_table("account_reconciliation_adjustments")
    op.drop_table("tier_projection_snapshots")
    op.drop_table("cycle_tier_summaries")
    op.drop_table("tier_allocation_segments")
    op.drop_table("utility_usage_imports")
    op.drop_table("manual_account_usage")
    op.drop_table("account_usage_authorities")
    for name, kind in (
        ("billing_cycle_boundary_source", "check"),
        ("billing_cycle_status", "check"),
        ("billing_cycle_window", "check"),
        ("uq_billing_cycle_account_window", "unique"),
    ):
        op.drop_constraint(name, "billing_cycles", type_=kind)
    op.drop_index("ix_billing_cycles_status", table_name="billing_cycles")
    op.drop_constraint("fk_billing_cycles_updated_by_users", "billing_cycles", type_="foreignkey")
    op.drop_constraint("fk_billing_cycles_created_by_users", "billing_cycles", type_="foreignkey")
    for column in (
        "updated_at",
        "created_at",
        "updated_by",
        "created_by",
        "locked_snapshot_hash",
        "recalculation_version",
        "override_revision",
        "boundary_source",
        "status",
    ):
        op.drop_column("billing_cycles", column)
    op.drop_table("rate_seasonal_baselines")
    op.drop_table("rate_threshold_rules")
    op.drop_table("rate_tier_definitions")
    op.drop_constraint("rate_version_pricing_model", "rate_versions", type_="check")
    op.drop_index("ix_rate_versions_pricing_model", table_name="rate_versions")
    op.drop_column("rate_versions", "pricing_model")
