"""add deterministic OTA interruption reconciliation evidence

Revision ID: 20260803_0027
Revises: 20260802_0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260803_0027"
down_revision = "20260802_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("firmware_deployments", sa.Column("source_version", sa.String(80)))
    op.add_column("firmware_deployments", sa.Column("source_build_hash", sa.String(128)))
    op.add_column("firmware_deployments", sa.Column("source_boot_id", sa.String(80)))
    op.add_column(
        "firmware_deployments",
        sa.Column(
            "interruption_evidence",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.execute(
        "UPDATE firmware_deployments AS deployment SET "
        "source_version = device.firmware_version, "
        "source_build_hash = device.firmware_build_hash "
        "FROM devices AS device WHERE deployment.device_id = device.id "
        "AND deployment.state NOT IN ('completed','failed','cancelled','rolled_back')"
    )


def downgrade() -> None:
    op.drop_column("firmware_deployments", "interruption_evidence")
    op.drop_column("firmware_deployments", "source_boot_id")
    op.drop_column("firmware_deployments", "source_build_hash")
    op.drop_column("firmware_deployments", "source_version")
