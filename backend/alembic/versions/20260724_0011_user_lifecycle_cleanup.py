"""Add reversible user disable, removal, and restoration lifecycle state.

Revision ID: 20260724_0011
Revises: 20260724_0010
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260724_0011"
down_revision = "20260724_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column(
        "users",
        sa.Column("is_protected", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "users",
        sa.Column(
            "removed_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("users", sa.Column("removal_reason", sa.String(500)))
    op.add_column("users", sa.Column("restored_at", sa.DateTime(timezone=True)))
    op.add_column(
        "users",
        sa.Column(
            "restored_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "removed_role_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "removed_site_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "users",
        sa.Column("removed_all_sites", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_check_constraint(
        "user_lifecycle_state",
        "users",
        "lifecycle_state IN ('active','disabled','removed')",
    )
    op.create_index("ix_users_lifecycle_state", "users", ["lifecycle_state"])
    op.create_index("ix_users_removed_at", "users", ["removed_at"])

    op.execute(
        "UPDATE users SET lifecycle_state = CASE WHEN is_active THEN 'active' ELSE 'disabled' END"
    )
    op.execute(
        """
        UPDATE users
        SET is_protected = true
        WHERE id = (
            SELECT users.id
            FROM users
            JOIN user_roles ON user_roles.user_id = users.id
            WHERE user_roles.role_name = 'admin'
            ORDER BY users.created_at, users.id
            LIMIT 1
        )
        """
    )

    permissions = (
        (
            "users.disable",
            "Disable and enable users",
            "Temporarily suspend and re-enable local user accounts.",
        ),
        (
            "users.remove",
            "Remove users",
            "Safely deprovision local users while preserving historical identity records.",
        ),
        (
            "users.restore",
            "Restore removed users",
            "Restore removed identities to a disabled, unassigned state for explicit review.",
        ),
    )
    for code, label, description in permissions:
        op.execute(
            sa.text(
                "INSERT INTO permissions "
                "(code, group_name, label, description, high_risk) "
                "VALUES (:code, 'Administration', :label, :description, true)"
            ).bindparams(code=code, label=label, description=description)
        )
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', :code)"
            ).bindparams(code=code)
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE permission_code IN "
        "('users.disable','users.remove','users.restore')"
    )
    op.execute(
        "DELETE FROM permissions WHERE code IN ('users.disable','users.remove','users.restore')"
    )
    op.drop_index("ix_users_removed_at", table_name="users")
    op.drop_index("ix_users_lifecycle_state", table_name="users")
    op.drop_constraint("user_lifecycle_state", "users", type_="check")
    op.drop_column("users", "removed_all_sites")
    op.drop_column("users", "removed_site_ids")
    op.drop_column("users", "removed_role_ids")
    op.drop_column("users", "restored_by")
    op.drop_column("users", "restored_at")
    op.drop_column("users", "removal_reason")
    op.drop_column("users", "removed_by")
    op.drop_column("users", "removed_at")
    op.drop_column("users", "is_protected")
    op.drop_column("users", "lifecycle_state")
