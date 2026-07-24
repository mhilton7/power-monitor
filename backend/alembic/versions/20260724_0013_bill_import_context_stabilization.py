"""Stabilize bill-import account context and permit deferred assignment.

Revision ID: 20260724_0013
Revises: 20260724_0012
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260724_0013"
down_revision = "20260724_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "utility_bill_imports",
        "utility_account_id",
        existing_type=sa.String(36),
        nullable=True,
    )
    op.alter_column(
        "utility_bill_cycle_drafts",
        "utility_account_id",
        existing_type=sa.String(36),
        nullable=True,
    )
    op.create_index(
        "uq_utility_bill_import_unassigned_creator_hash",
        "utility_bill_imports",
        ["created_by", "content_sha256"],
        unique=True,
        postgresql_where=sa.text("utility_account_id IS NULL"),
        sqlite_where=sa.text("utility_account_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_utility_bill_import_unassigned_creator_hash",
        table_name="utility_bill_imports",
    )
    op.execute(
        """
        DELETE FROM utility_bill_cycle_drafts
        WHERE utility_account_id IS NULL
        """
    )
    op.execute(
        """
        DELETE FROM utility_bill_imports
        WHERE utility_account_id IS NULL
        """
    )
    op.alter_column(
        "utility_bill_cycle_drafts",
        "utility_account_id",
        existing_type=sa.String(36),
        nullable=False,
    )
    op.alter_column(
        "utility_bill_imports",
        "utility_account_id",
        existing_type=sa.String(36),
        nullable=False,
    )
