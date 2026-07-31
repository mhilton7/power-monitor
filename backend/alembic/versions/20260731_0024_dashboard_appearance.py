"""add administrator-published dashboard appearance

Revision ID: 20260731_0024
Revises: 20260731_0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260731_0024"
down_revision = "20260731_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dashboard_appearance",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("chart_power_color", sa.String(7), nullable=False),
        sa.Column("chart_energy_color", sa.String(7), nullable=False),
        sa.Column("chart_cost_color", sa.String(7), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "updated_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_dashboard_appearance_updated_by",
        "dashboard_appearance",
        ["updated_by"],
    )
    op.execute(
        "INSERT INTO dashboard_appearance "
        "(id, chart_power_color, chart_energy_color, chart_cost_color, revision, updated_at) "
        "VALUES ('current', '#78DFBF', '#78DFBF', '#C9A7FF', 1, CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("dashboard_appearance")
