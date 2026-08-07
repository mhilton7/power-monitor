"""persist canonical headless hardware and measurement evidence

Revision ID: 20260806_0033
Revises: 20260806_0032
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0033"
down_revision: str | None = "20260806_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("device_heartbeats", sa.Column("current_voltage_volts", sa.Numeric(14, 4)))
    op.add_column("device_heartbeats", sa.Column("current_amps", sa.Numeric(14, 4)))
    op.add_column("device_heartbeats", sa.Column("current_power_factor", sa.Numeric(8, 5)))
    op.add_column("device_heartbeats", sa.Column("current_frequency_hz", sa.Numeric(8, 3)))
    op.add_column("device_heartbeats", sa.Column("current_energy_wh", sa.Numeric(20, 3)))
    op.add_column(
        "device_heartbeats",
        sa.Column("pzem_status", sa.String(64), nullable=False, server_default="unavailable"),
    )
    op.add_column(
        "device_heartbeats",
        sa.Column("pzem_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "device_heartbeats",
        sa.Column("sd_status", sa.String(64), nullable=False, server_default="unavailable"),
    )
    op.add_column(
        "device_heartbeats",
        sa.Column("sd_details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("device_heartbeats", sa.Column("card_generation", sa.String(32)))


def downgrade() -> None:
    op.drop_column("device_heartbeats", "card_generation")
    op.drop_column("device_heartbeats", "sd_details")
    op.drop_column("device_heartbeats", "sd_status")
    op.drop_column("device_heartbeats", "pzem_details")
    op.drop_column("device_heartbeats", "pzem_status")
    op.drop_column("device_heartbeats", "current_energy_wh")
    op.drop_column("device_heartbeats", "current_frequency_hz")
    op.drop_column("device_heartbeats", "current_power_factor")
    op.drop_column("device_heartbeats", "current_amps")
    op.drop_column("device_heartbeats", "current_voltage_volts")
