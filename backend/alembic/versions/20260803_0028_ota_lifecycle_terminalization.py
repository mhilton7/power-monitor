"""terminalize OTA lifecycle state independently of API polling

Revision ID: 20260803_0028
Revises: 20260803_0027
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260803_0028"
down_revision = "20260803_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "firmware_deployments",
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "firmware_deployments",
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE firmware_deployments SET state_changed_at = "
        "COALESCE(last_report_at, validated_at, installed_at, downloaded_at, "
        "scheduled_at, created_at) "
        "WHERE state_changed_at IS NULL"
    )
    op.execute(
        "UPDATE firmware_deployments SET terminal_at = "
        "COALESCE(validated_at, rollback_at, last_report_at, state_changed_at) "
        "WHERE terminal_at IS NULL AND state IN ('completed','failed','cancelled','rolled_back')"
    )
    op.alter_column("firmware_deployments", "state_changed_at", nullable=False)
    op.create_index(
        "ix_firmware_deployment_state_changed",
        "firmware_deployments",
        ["state", "state_changed_at"],
    )
    op.create_index(
        "ix_firmware_deployment_state_expires",
        "firmware_deployments",
        ["state", "expires_at"],
    )
    op.create_index(
        "ix_firmware_deployments_terminal_at",
        "firmware_deployments",
        ["terminal_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_firmware_deployments_terminal_at", table_name="firmware_deployments")
    op.drop_index("ix_firmware_deployment_state_expires", table_name="firmware_deployments")
    op.drop_index("ix_firmware_deployment_state_changed", table_name="firmware_deployments")
    op.drop_column("firmware_deployments", "terminal_at")
    op.drop_column("firmware_deployments", "state_changed_at")
