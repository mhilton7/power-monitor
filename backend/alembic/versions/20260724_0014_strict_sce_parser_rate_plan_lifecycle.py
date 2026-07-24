"""Add strict SCE evidence fields and safe rate-plan lifecycle metadata.

Revision ID: 20260724_0014
Revises: 20260724_0013
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260724_0014"
down_revision = "20260724_0013"
branch_labels = None
depends_on = None

RATE_LIFECYCLE_PERMISSIONS = (
    (
        "rates.remove",
        "Remove rate plans",
        "Delete unused drafts or retire rate plans after dependency review.",
    ),
    (
        "rates.restore",
        "Restore rate plans",
        "Restore locally removed or retired rate plans without reassigning accounts.",
    ),
)


def upgrade() -> None:
    op.add_column(
        "rate_plans",
        sa.Column(
            "lifecycle_revision",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.add_column("rate_plans", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "rate_plans",
        sa.Column(
            "removed_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("rate_plans", sa.Column("removal_reason", sa.String(500)))
    op.add_column("rate_plans", sa.Column("restored_at", sa.DateTime(timezone=True)))
    op.add_column(
        "rate_plans",
        sa.Column(
            "restored_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_rate_plans_removed_at", "rate_plans", ["removed_at"])
    op.create_check_constraint(
        "rate_plan_lifecycle_status",
        "rate_plans",
        "status IN ('draft','active','retired','removed')",
    )
    op.create_check_constraint(
        "rate_plan_lifecycle_revision",
        "rate_plans",
        "lifecycle_revision > 0",
    )

    op.add_column(
        "utility_bill_extracted_fields",
        sa.Column("parser_rule", sa.String(160)),
    )
    op.add_column(
        "utility_bill_extracted_fields",
        sa.Column("validation_result", sa.JSON()),
    )

    connection = op.get_bind()
    for code, label, description in RATE_LIFECYCLE_PERMISSIONS:
        connection.execute(
            sa.text(
                """
                INSERT INTO permissions (code, group_name, label, description, high_risk)
                VALUES (:code, 'Rates', :label, :description, true)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {
                "code": code,
                "label": label,
                "description": description,
            },
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO role_permissions (role_name, permission_code)
                SELECT roles.name, CAST(:code AS VARCHAR(80))
                FROM roles
                WHERE roles.name IN ('admin', 'rate-manager')
                ON CONFLICT DO NOTHING
                """
            ),
            {"code": code},
        )


def downgrade() -> None:
    connection = op.get_bind()
    for code, _display_name, _description in RATE_LIFECYCLE_PERMISSIONS:
        connection.execute(
            sa.text("DELETE FROM role_permissions WHERE permission_code = :code"),
            {"code": code},
        )
        connection.execute(
            sa.text("DELETE FROM permissions WHERE code = :code"),
            {"code": code},
        )
    op.drop_column("utility_bill_extracted_fields", "validation_result")
    op.drop_column("utility_bill_extracted_fields", "parser_rule")
    op.drop_constraint("rate_plan_lifecycle_revision", "rate_plans", type_="check")
    op.drop_constraint("rate_plan_lifecycle_status", "rate_plans", type_="check")
    op.drop_index("ix_rate_plans_removed_at", table_name="rate_plans")
    op.drop_column("rate_plans", "restored_by")
    op.drop_column("rate_plans", "restored_at")
    op.drop_column("rate_plans", "removal_reason")
    op.drop_column("rate_plans", "removed_by")
    op.drop_column("rate_plans", "removed_at")
    op.drop_column("rate_plans", "lifecycle_revision")
