"""Add durable normalized utility-bill artifacts and truthful confidence.

Revision ID: 20260725_0016
Revises: 20260724_0015
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260725_0016"
down_revision = "20260724_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "utility_bill_extraction_revisions",
        sa.Column(
            "normalized_artifact",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.drop_constraint(
        "utility_bill_field_confidence",
        "utility_bill_extracted_fields",
        type_="check",
    )
    op.execute(
        """
        UPDATE utility_bill_extracted_fields
        SET confidence = CASE
            WHEN normalized_value IS NULL AND corrected_value IS NULL THEN 'missing'
            ELSE 'manual_confirmed'
        END
        WHERE confidence = 'administrator_confirmed'
        """
    )
    op.execute(
        """
        UPDATE utility_bill_extracted_fields
        SET confidence = 'conflict'
        WHERE confidence IN ('conflicts_current', 'conflicts_source')
        """
    )
    op.create_check_constraint(
        "utility_bill_field_confidence",
        "utility_bill_extracted_fields",
        "confidence IN ('parser_confirmed','arithmetic_confirmed','high','medium','low',"
        "'manual_confirmed','missing','conflict','not_applicable')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "utility_bill_field_confidence",
        "utility_bill_extracted_fields",
        type_="check",
    )
    op.execute(
        """
        UPDATE utility_bill_extracted_fields
        SET confidence = 'administrator_confirmed'
        WHERE confidence = 'manual_confirmed'
        """
    )
    op.execute(
        """
        UPDATE utility_bill_extracted_fields
        SET confidence = 'conflicts_current'
        WHERE confidence = 'conflict'
        """
    )
    op.execute(
        """
        UPDATE utility_bill_extracted_fields
        SET confidence = 'high'
        WHERE confidence IN ('parser_confirmed', 'arithmetic_confirmed')
        """
    )
    op.create_check_constraint(
        "utility_bill_field_confidence",
        "utility_bill_extracted_fields",
        "confidence IN ('administrator_confirmed','high','medium','low','missing',"
        "'conflicts_current','conflicts_source','not_applicable')",
    )
    op.drop_column("utility_bill_extraction_revisions", "normalized_artifact")
