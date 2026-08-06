"""add outbound headless agent boot replay state and durable commands

Revision ID: 20260806_0032
Revises: 20260806_0031
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0032"
down_revision: str | None = "20260806_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "headless_agent_boots",
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("boot_id", sa.String(length=36), nullable=False),
        sa.Column("highest_counter", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("highest_counter > 0", name="headless_agent_counter_positive"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("device_id", "boot_id"),
    )
    op.create_index(
        "ix_headless_agent_boots_active",
        "headless_agent_boots",
        ["active"],
    )
    op.create_index(
        "ix_headless_agent_boots_last_seen_at",
        "headless_agent_boots",
        ["last_seen_at"],
    )
    op.create_index(
        "uq_headless_agent_active_boot",
        "headless_agent_boots",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("active"),
        sqlite_where=sa.text("active = 1"),
    )
    op.create_table(
        "device_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("device_id", sa.String(length=36), nullable=False),
        sa.Column("command_type", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("expected_state", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(length=80), nullable=True),
        sa.CheckConstraint(
            "command_type IN ('apply_configuration','reboot','data_reset_prepare',"
            "'data_reset_commit','data_reset_cancel','data_reset_status','ota_update',"
            "'sync_now')",
            name="device_command_type",
        ),
        sa.CheckConstraint(
            "state IN ('queued','delivered','accepted','running','completed','failed',"
            "'expired','cancelled')",
            name="device_command_state",
        ),
        sa.CheckConstraint("delivery_attempts >= 0", name="device_command_attempts_nonnegative"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_device_commands_device_id", "device_commands", ["device_id"])
    op.create_index("ix_device_commands_command_type", "device_commands", ["command_type"])
    op.create_index("ix_device_commands_state", "device_commands", ["state"])
    op.create_index("ix_device_commands_created_at", "device_commands", ["created_at"])
    op.create_index("ix_device_commands_expires_at", "device_commands", ["expires_at"])
    op.create_index(
        "ix_device_commands_delivery",
        "device_commands",
        ["device_id", "state", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("device_commands")
    op.drop_table("headless_agent_boots")
