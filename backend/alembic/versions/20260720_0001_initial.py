"""Initial normalized production schema.

Revision ID: 20260720_0001
Revises: none
Create Date: 2026-07-20

The schema SQL beside this revision is a frozen generated artifact. Production processes
never call create_all. Regeneration is prohibited after release; later changes require a
new Alembic revision.
"""

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "20260720_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    schema_path = Path(__file__).with_name("20260720_0001_schema.sql")
    for statement in schema_path.read_text(encoding="utf-8").split(";\n"):
        if statement.strip() and not statement.lstrip().startswith("--"):
            op.execute(statement)
        elif statement.lstrip().startswith("--"):
            sql = "\n".join(statement.splitlines()[1:]).strip()
            if sql:
                op.execute(sql)


def downgrade() -> None:
    tables = [
        "worker_state",
        "backup_runs",
        "generated_reports",
        "report_definitions",
        "export_jobs",
        "firmware_deployments",
        "firmware_releases",
        "notification_attempts",
        "notification_channels",
        "alert_instances",
        "alert_rules",
        "manual_bill_adjustments",
        "daily_cost_rollups",
        "cost_interval_results",
        "cost_calculation_runs",
        "billing_cycles",
        "rate_adjustments",
        "fixed_charge_rules",
        "baseline_rules",
        "rate_periods",
        "rate_day_types",
        "rate_seasons",
        "rate_versions",
        "rate_plans",
        "site_rollups",
        "monthly_device_rollups",
        "daily_device_rollups",
        "normalized_intervals",
        "raw_readings",
        "device_nonces",
        "sequence_gaps",
        "sync_cursors",
        "enrollment_tokens",
        "device_events",
        "device_heartbeats",
        "device_status_snapshots",
        "device_config_versions",
        "device_capabilities",
        "device_addresses",
        "device_credentials",
        "aggregate_members",
        "devices",
        "aggregate_sets",
        "circuits",
        "utility_accounts",
        "utilities",
        "sites",
        "audit_events",
        "totp_credentials",
        "sessions",
        "user_roles",
        "roles",
        "users",
    ]
    for table in tables:
        op.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
