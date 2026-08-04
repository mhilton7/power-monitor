"""add authenticated OTA update-window waiting state

Revision ID: 20260803_0030
Revises: 20260803_0029
"""

from __future__ import annotations

from alembic import op

revision = "20260803_0030"
down_revision = "20260803_0029"
branch_labels = None
depends_on = None

_WITH_WAITING = (
    "state IN ('waiting_canary','scheduled','offered','manifest_authenticated',"
    "'waiting_for_schedule','download_started','downloading','binary_verified',"
    "'partition_written','rebooting','post_boot_validation','validated',"
    "'awaiting_heartbeat','completed','failed','cancelled','rollback_detected',"
    "'rolled_back')"
)
_WITHOUT_WAITING = (
    "state IN ('waiting_canary','scheduled','offered','manifest_authenticated',"
    "'download_started','downloading','binary_verified','partition_written',"
    "'rebooting','post_boot_validation','validated','awaiting_heartbeat',"
    "'completed','failed','cancelled','rollback_detected','rolled_back')"
)


def upgrade() -> None:
    op.drop_constraint("firmware_deployment_state", "firmware_deployments", type_="check")
    op.create_check_constraint("firmware_deployment_state", "firmware_deployments", _WITH_WAITING)


def downgrade() -> None:
    op.execute(
        "UPDATE firmware_deployments SET state = 'manifest_authenticated', "
        "status = 'manifest_authenticated' WHERE state = 'waiting_for_schedule'"
    )
    op.drop_constraint("firmware_deployment_state", "firmware_deployments", type_="check")
    op.create_check_constraint(
        "firmware_deployment_state", "firmware_deployments", _WITHOUT_WAITING
    )
