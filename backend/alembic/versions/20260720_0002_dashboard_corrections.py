"""Add sensor lifecycle history and application-log export jobs.

Revision ID: 20260720_0002
Revises: 20260720_0001
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0002"
down_revision: str | None = "20260720_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "lifecycle_status",
            sa.String(length=24),
            nullable=False,
            server_default="active",
        ),
    )
    op.add_column(
        "devices",
        sa.Column(
            "lifecycle_generation", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "devices", sa.Column("decommissioned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "devices",
        sa.Column(
            "decommissioned_by",
            sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("devices", sa.Column("decommission_reason", sa.String(length=64), nullable=True))
    op.create_index("ix_devices_lifecycle_status", "devices", ["lifecycle_status"])
    op.create_index("ix_devices_decommissioned_at", "devices", ["decommissioned_at"])
    op.create_index("ix_devices_decommissioned_by", "devices", ["decommissioned_by"])
    op.execute(
        "UPDATE devices SET lifecycle_status = 'decommissioned', "
        "decommissioned_at = revoked_at, decommission_reason = 'legacy_revoke', "
        "lifecycle_generation = 1 WHERE revoked_at IS NOT NULL"
    )

    op.create_table(
        "device_lifecycle_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.String(length=64), nullable=True),
        sa.Column("site_id", sa.String(length=36), nullable=True),
        sa.Column("circuit_id", sa.String(length=36), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["circuit_id"], ["circuits.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_id",
            "generation",
            "event_type",
            name="uq_device_lifecycle_generation_event",
        ),
    )
    op.create_index(
        "ix_device_lifecycle_events_device_id", "device_lifecycle_events", ["device_id"]
    )
    op.create_index(
        "ix_device_lifecycle_events_event_type", "device_lifecycle_events", ["event_type"]
    )
    op.create_index(
        "ix_device_lifecycle_events_occurred_at", "device_lifecycle_events", ["occurred_at"]
    )
    op.create_index("ix_device_lifecycle_events_actor_id", "device_lifecycle_events", ["actor_id"])

    op.create_table(
        "log_export_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("requested_by", sa.String(length=36), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("services", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_log_export_jobs_requested_by", "log_export_jobs", ["requested_by"])
    op.create_index("ix_log_export_jobs_requested_at", "log_export_jobs", ["requested_at"])
    op.create_index("ix_log_export_jobs_status", "log_export_jobs", ["status"])
    op.create_index("ix_log_export_jobs_expires_at", "log_export_jobs", ["expires_at"])
    op.create_index("ix_log_export_jobs_correlation_id", "log_export_jobs", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_log_export_jobs_correlation_id", table_name="log_export_jobs")
    op.drop_index("ix_log_export_jobs_expires_at", table_name="log_export_jobs")
    op.drop_index("ix_log_export_jobs_status", table_name="log_export_jobs")
    op.drop_index("ix_log_export_jobs_requested_at", table_name="log_export_jobs")
    op.drop_index("ix_log_export_jobs_requested_by", table_name="log_export_jobs")
    op.drop_table("log_export_jobs")

    op.drop_index("ix_device_lifecycle_events_actor_id", table_name="device_lifecycle_events")
    op.drop_index("ix_device_lifecycle_events_occurred_at", table_name="device_lifecycle_events")
    op.drop_index("ix_device_lifecycle_events_event_type", table_name="device_lifecycle_events")
    op.drop_index("ix_device_lifecycle_events_device_id", table_name="device_lifecycle_events")
    op.drop_table("device_lifecycle_events")

    op.drop_index("ix_devices_decommissioned_by", table_name="devices")
    op.drop_index("ix_devices_decommissioned_at", table_name="devices")
    op.drop_index("ix_devices_lifecycle_status", table_name="devices")
    op.drop_column("devices", "decommission_reason")
    op.drop_column("devices", "decommissioned_by")
    op.drop_column("devices", "decommissioned_at")
    op.drop_column("devices", "lifecycle_generation")
    op.drop_column("devices", "lifecycle_status")
