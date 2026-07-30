"""separate utility-bill tariff evidence from monitored usage authority

Revision ID: 20260730_0021
Revises: 20260730_0020
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260730_0021"
down_revision = "20260730_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "utility_bill_extracted_fields",
        sa.Column(
            "calculation_role",
            sa.String(32),
            nullable=False,
            server_default="reference_only",
        ),
    )
    op.execute(
        """
        UPDATE utility_bill_extracted_fields
        SET calculation_role = CASE
          WHEN field_key IN (
            'baseline_allowance_kwh','daily_baseline_formula','threshold_interpretation'
          ) THEN 'tariff_rule'
          WHEN output_kind = 'rate_plan'
            AND field_key NOT LIKE '%usage%'
            AND field_key NOT LIKE '%energy_charge%'
            AND field_key NOT LIKE '%subtotal%'
            AND field_key NOT LIKE '%total%'
            AND field_key NOT LIKE '%payment%'
            AND field_key NOT LIKE '%credit%'
            AND field_key NOT LIKE '%tax%'
            AND field_key NOT LIKE '%adjustment%'
            AND field_key NOT LIKE '%balance%'
            AND field_key NOT LIKE '%service_voltage%'
            THEN 'tariff_rule'
          ELSE 'reference_only'
        END
        """
    )
    op.create_index(
        "ix_utility_bill_extracted_fields_calculation_role",
        "utility_bill_extracted_fields",
        ["calculation_role"],
    )
    op.create_check_constraint(
        "utility_bill_field_calculation_role",
        "utility_bill_extracted_fields",
        "calculation_role IN ('tariff_rule','reference_only')",
    )

    op.add_column(
        "utility_bill_cycle_drafts",
        sa.Column(
            "calculation_role",
            sa.String(32),
            nullable=False,
            server_default="reference_only",
        ),
    )
    op.create_index(
        "ix_utility_bill_cycle_drafts_calculation_role",
        "utility_bill_cycle_drafts",
        ["calculation_role"],
    )
    op.create_check_constraint(
        "utility_bill_cycle_draft_reference_only",
        "utility_bill_cycle_drafts",
        "calculation_role = 'reference_only'",
    )

    op.add_column(
        "account_usage_authorities",
        sa.Column(
            "calculation_role",
            sa.String(40),
            nullable=False,
            server_default="sensor_measurements",
        ),
    )
    op.execute(
        """
        UPDATE account_usage_authorities
        SET calculation_role = CASE
          WHEN source_reference LIKE 'utility-bill:%'
            OR source_reference LIKE 'urn:power-monitor:utility-bill:%'
            THEN 'reference_only'
          WHEN authority_type IN (
            'complete_site_aggregate','service_leg_pair','whole_account_meter'
          ) THEN 'sensor_measurements'
          ELSE 'advanced_external_correction'
        END
        """
    )
    op.create_index(
        "ix_account_usage_authorities_calculation_role",
        "account_usage_authorities",
        ["calculation_role"],
    )
    op.create_check_constraint(
        "account_usage_authority_calculation_role",
        "account_usage_authorities",
        "calculation_role IN "
        "('sensor_measurements','advanced_external_correction','reference_only')",
    )

    op.add_column(
        "manual_account_usage",
        sa.Column(
            "calculation_role",
            sa.String(40),
            nullable=False,
            server_default="advanced_external_correction",
        ),
    )
    op.execute(
        """
        UPDATE manual_account_usage
        SET calculation_role = 'reference_only'
        WHERE evidence_reference LIKE 'utility-bill:%'
           OR idempotency_key LIKE 'utility-bill-%'
        """
    )
    op.create_index(
        "ix_manual_account_usage_calculation_role",
        "manual_account_usage",
        ["calculation_role"],
    )
    op.create_check_constraint(
        "manual_account_usage_calculation_role",
        "manual_account_usage",
        "calculation_role IN ('advanced_external_correction','reference_only')",
    )

    op.add_column(
        "utility_usage_imports",
        sa.Column(
            "calculation_role",
            sa.String(40),
            nullable=False,
            server_default="advanced_external_correction",
        ),
    )
    op.execute(
        """
        UPDATE utility_usage_imports
        SET calculation_role = 'reference_only'
        WHERE source_name LIKE 'Private utility bill import %'
        """
    )
    op.create_index(
        "ix_utility_usage_imports_calculation_role",
        "utility_usage_imports",
        ["calculation_role"],
    )
    op.create_check_constraint(
        "utility_usage_import_calculation_role",
        "utility_usage_imports",
        "calculation_role IN ('advanced_external_correction','reference_only')",
    )

    op.add_column(
        "billing_cycles",
        sa.Column(
            "usage_source_type",
            sa.String(40),
            nullable=False,
            server_default="sensor_measurements",
        ),
    )
    op.add_column(
        "billing_cycles",
        sa.Column(
            "projection_source_type",
            sa.String(40),
            nullable=False,
            server_default="sensor_trend",
        ),
    )
    op.add_column(
        "billing_cycles",
        sa.Column(
            "tier_progress_source_type",
            sa.String(40),
            nullable=False,
            server_default="sensor_measurements",
        ),
    )
    op.add_column(
        "billing_cycles",
        sa.Column(
            "recalculation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "billing_cycles",
        sa.Column(
            "legacy_bill_authority_review_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        """
        UPDATE billing_cycles
        SET legacy_bill_authority_review_required = TRUE
        WHERE
          EXISTS (
            SELECT 1
            FROM utility_bill_cycle_drafts draft
            WHERE draft.billing_cycle_id = billing_cycles.id
              AND draft.utility_usage_import_id IS NOT NULL
          )
          OR EXISTS (
            SELECT 1
            FROM account_usage_authorities authority
            WHERE authority.utility_account_id = billing_cycles.utility_account_id
              AND authority.calculation_role = 'reference_only'
          )
          OR EXISTS (
            SELECT 1
            FROM manual_account_usage manual
            WHERE manual.billing_cycle_id = billing_cycles.id
              AND manual.calculation_role = 'reference_only'
          )
        """
    )
    op.execute(
        """
        UPDATE billing_cycles
        SET status = 'recalculating',
            recalculation_required = TRUE,
            usage_source_type = 'sensor_measurements',
            projection_source_type = 'sensor_trend',
            tier_progress_source_type = 'sensor_measurements'
        WHERE finalized_at IS NULL
          AND legacy_bill_authority_review_required = TRUE
        """
    )
    op.create_index(
        "ix_billing_cycles_usage_source_type",
        "billing_cycles",
        ["usage_source_type"],
    )
    op.create_check_constraint(
        "billing_cycle_usage_source_type",
        "billing_cycles",
        "usage_source_type IN ('sensor_measurements','advanced_external_correction','unavailable')",
    )
    op.create_check_constraint(
        "billing_cycle_projection_source_type",
        "billing_cycles",
        "projection_source_type IN ('sensor_trend','advanced_external_correction','unavailable')",
    )
    op.create_check_constraint(
        "billing_cycle_tier_progress_source_type",
        "billing_cycles",
        "tier_progress_source_type IN "
        "('sensor_measurements','advanced_external_correction','unavailable')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "billing_cycle_tier_progress_source_type",
        "billing_cycles",
        type_="check",
    )
    op.drop_constraint(
        "billing_cycle_projection_source_type",
        "billing_cycles",
        type_="check",
    )
    op.drop_constraint(
        "billing_cycle_usage_source_type",
        "billing_cycles",
        type_="check",
    )
    op.drop_index("ix_billing_cycles_usage_source_type", table_name="billing_cycles")
    op.drop_column("billing_cycles", "legacy_bill_authority_review_required")
    op.drop_column("billing_cycles", "recalculation_required")
    op.drop_column("billing_cycles", "tier_progress_source_type")
    op.drop_column("billing_cycles", "projection_source_type")
    op.drop_column("billing_cycles", "usage_source_type")

    op.drop_constraint(
        "utility_usage_import_calculation_role",
        "utility_usage_imports",
        type_="check",
    )
    op.drop_index(
        "ix_utility_usage_imports_calculation_role",
        table_name="utility_usage_imports",
    )
    op.drop_column("utility_usage_imports", "calculation_role")

    op.drop_constraint(
        "manual_account_usage_calculation_role",
        "manual_account_usage",
        type_="check",
    )
    op.drop_index(
        "ix_manual_account_usage_calculation_role",
        table_name="manual_account_usage",
    )
    op.drop_column("manual_account_usage", "calculation_role")

    op.drop_constraint(
        "account_usage_authority_calculation_role",
        "account_usage_authorities",
        type_="check",
    )
    op.drop_index(
        "ix_account_usage_authorities_calculation_role",
        table_name="account_usage_authorities",
    )
    op.drop_column("account_usage_authorities", "calculation_role")

    op.drop_constraint(
        "utility_bill_cycle_draft_reference_only",
        "utility_bill_cycle_drafts",
        type_="check",
    )
    op.drop_index(
        "ix_utility_bill_cycle_drafts_calculation_role",
        table_name="utility_bill_cycle_drafts",
    )
    op.drop_column("utility_bill_cycle_drafts", "calculation_role")

    op.drop_constraint(
        "utility_bill_field_calculation_role",
        "utility_bill_extracted_fields",
        type_="check",
    )
    op.drop_index(
        "ix_utility_bill_extracted_fields_calculation_role",
        table_name="utility_bill_extracted_fields",
    )
    op.drop_column("utility_bill_extracted_fields", "calculation_role")
