"""Add per-import utility-bill history visibility.

Revision ID: 20260724_0015
Revises: 20260724_0014
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260724_0015"
down_revision = "20260724_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "utility_bill_imports",
        sa.Column("history_cleared_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "utility_bill_imports",
        sa.Column(
            "history_cleared_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_utility_bill_imports_history_cleared_at",
        "utility_bill_imports",
        ["history_cleared_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_utility_bill_imports_history_cleared_at",
        table_name="utility_bill_imports",
    )
    op.drop_column("utility_bill_imports", "history_cleared_by")
    op.drop_column("utility_bill_imports", "history_cleared_at")
