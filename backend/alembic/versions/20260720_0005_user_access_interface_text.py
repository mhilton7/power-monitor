"""Add granular user access and revisioned interface text.

Revision ID: 20260720_0005
Revises: 20260720_0004
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260720_0005"
down_revision: str | None = "20260720_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_CODES = (
    "overview.view",
    "usage.view",
    "history.view",
    "history.export",
    "costs.view",
    "costs.export",
    "sites.view",
    "sites.manage",
    "topology.view",
    "topology.manage",
    "devices.view",
    "devices.manage",
    "devices.remove",
    "enrollment.view",
    "enrollment.manage",
    "firmware.view",
    "firmware.manage",
    "rates.view",
    "rates.manage_custom",
    "rates.manage_sources",
    "rates.check_sources",
    "rates.review_candidates",
    "rates.approve_candidates",
    "rates.assign",
    "alerts.view",
    "alerts.acknowledge",
    "alerts.manage_rules",
    "alerts.manage_delivery",
    "backups.view",
    "backups.create",
    "backups.restore",
    "logs.export",
    "users.view",
    "users.manage",
    "users.manage_protected",
    "roles.view",
    "roles.manage",
    "audit.view",
    "settings.view",
    "settings.manage",
    "interface_text.view",
    "interface_text.manage",
)

VIEWER_PERMISSIONS = {
    "overview.view",
    "usage.view",
    "history.view",
    "history.export",
    "costs.view",
    "costs.export",
    "sites.view",
    "topology.view",
    "devices.view",
    "rates.view",
    "alerts.view",
}
OPERATOR_PERMISSIONS = VIEWER_PERMISSIONS | {
    "topology.manage",
    "devices.manage",
    "enrollment.view",
    "enrollment.manage",
    "firmware.view",
    "alerts.acknowledge",
    "alerts.manage_rules",
}
RATE_MANAGER_PERMISSIONS = VIEWER_PERMISSIONS | {
    "rates.manage_custom",
    "rates.manage_sources",
    "rates.check_sources",
    "rates.review_candidates",
    "rates.approve_candidates",
    "rates.assign",
}


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True)))
    op.add_column(
        "users", sa.Column("all_sites", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "users", sa.Column("access_revision", sa.Integer(), nullable=False, server_default="1")
    )
    op.add_column("sessions", sa.Column("reauthenticated_at", sa.DateTime(timezone=True)))

    op.add_column(
        "roles", sa.Column("display_name", sa.String(120), nullable=False, server_default="")
    )
    op.add_column(
        "roles", sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "roles", sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("roles", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("roles", sa.Column("created_by", sa.String(36)))
    op.add_column("roles", sa.Column("updated_by", sa.String(36)))
    op.add_column(
        "roles",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column(
        "roles",
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_foreign_key(
        "fk_roles_created_by_users", "roles", "users", ["created_by"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_roles_updated_by_users", "roles", "users", ["updated_by"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_roles_is_builtin", "roles", ["is_builtin"])
    op.create_index("ix_roles_is_archived", "roles", ["is_archived"])
    role_labels = {
        "admin": "Administrator",
        "operator": "Operator",
        "rate-manager": "Rate Manager",
        "viewer": "Regular User / Read-Only Viewer",
    }
    for role_name in role_labels:
        op.execute(
            sa.text(
                "INSERT INTO roles (name, description) "
                "SELECT :role_name, :description "
                "WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = :role_name)"
            ).bindparams(
                role_name=role_name,
                description={
                    "admin": "Full application administration",
                    "operator": "Assigned-site device and alert operations",
                    "rate-manager": "Rate plan and source administration",
                    "viewer": "Read-only assigned-site dashboard access",
                }[role_name],
            )
        )
    op.execute("UPDATE roles SET display_name = name WHERE display_name = ''")
    for role_name, display_name in role_labels.items():
        op.execute(
            sa.text(
                "UPDATE roles SET display_name = :display_name, is_builtin = true "
                "WHERE name = :role_name"
            ).bindparams(display_name=display_name, role_name=role_name)
        )
    op.create_index(
        "uq_roles_display_name_lower",
        "roles",
        [sa.text("lower(display_name)")],
        unique=True,
    )

    permissions = op.create_table(
        "permissions",
        sa.Column("code", sa.String(80), primary_key=True),
        sa.Column("group_name", sa.String(80), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("high_risk", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_permissions_group_name", "permissions", ["group_name"])
    op.bulk_insert(
        permissions,
        [
            {
                "code": code,
                "group_name": code.split(".", 1)[0],
                "label": code,
                "description": code,
                "high_risk": code.endswith((".manage", ".restore", ".remove")),
            }
            for code in PERMISSION_CODES
        ],
    )
    role_permissions = op.create_table(
        "role_permissions",
        sa.Column(
            "role_name",
            sa.String(32),
            sa.ForeignKey("roles.name", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_code",
            sa.String(80),
            sa.ForeignKey("permissions.code", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    role_map = {
        "admin": set(PERMISSION_CODES),
        "operator": OPERATOR_PERMISSIONS,
        "rate-manager": RATE_MANAGER_PERMISSIONS,
        "viewer": VIEWER_PERMISSIONS,
    }
    op.bulk_insert(
        role_permissions,
        [
            {"role_name": role_name, "permission_code": permission}
            for role_name, assigned in role_map.items()
            for permission in sorted(assigned)
        ],
    )
    op.create_table(
        "user_sites",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "site_id",
            sa.String(36),
            sa.ForeignKey("sites.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "role_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "role_name",
            sa.String(32),
            sa.ForeignKey("roles.name", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(255), nullable=False),
        sa.Column("permissions", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.UniqueConstraint("role_name", "revision", name="uq_role_revision"),
    )
    op.create_index("ix_role_revisions_role_name", "role_revisions", ["role_name"])

    op.create_table(
        "interface_text_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, unique=True),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column(
            "restored_from_id",
            sa.String(36),
            sa.ForeignKey("interface_text_revisions.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_interface_text_revisions_revision", "interface_text_revisions", ["revision"]
    )
    op.create_index(
        "ix_interface_text_revisions_created_by", "interface_text_revisions", ["created_by"]
    )
    op.create_table(
        "interface_text_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("base_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("previewed_revision", sa.Integer()),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("edited_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_interface_text_drafts_edited_by", "interface_text_drafts", ["edited_by"])
    op.create_table(
        "interface_text_state",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "current_revision_id",
            sa.String(36),
            sa.ForeignKey("interface_text_revisions.id", ondelete="RESTRICT"),
        ),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO interface_text_state (id, current_revision_id, current_revision, updated_at) "
        "VALUES ('current', NULL, 0, CURRENT_TIMESTAMP)"
    )


def downgrade() -> None:
    op.drop_table("interface_text_state")
    op.drop_index("ix_interface_text_drafts_edited_by", table_name="interface_text_drafts")
    op.drop_table("interface_text_drafts")
    op.drop_index("ix_interface_text_revisions_created_by", table_name="interface_text_revisions")
    op.drop_index("ix_interface_text_revisions_revision", table_name="interface_text_revisions")
    op.drop_table("interface_text_revisions")
    op.drop_index("ix_role_revisions_role_name", table_name="role_revisions")
    op.drop_table("role_revisions")
    op.drop_table("user_sites")
    op.drop_table("role_permissions")
    op.drop_index("ix_permissions_group_name", table_name="permissions")
    op.drop_table("permissions")
    op.drop_index("ix_roles_is_archived", table_name="roles")
    op.drop_index("ix_roles_is_builtin", table_name="roles")
    op.drop_index("uq_roles_display_name_lower", table_name="roles")
    op.drop_constraint("fk_roles_updated_by_users", "roles", type_="foreignkey")
    op.drop_constraint("fk_roles_created_by_users", "roles", type_="foreignkey")
    op.drop_column("roles", "updated_at")
    op.drop_column("roles", "created_at")
    op.drop_column("roles", "updated_by")
    op.drop_column("roles", "created_by")
    op.drop_column("roles", "revision")
    op.drop_column("roles", "is_archived")
    op.drop_column("roles", "is_builtin")
    op.drop_column("roles", "display_name")
    op.drop_column("sessions", "reauthenticated_at")
    op.drop_column("users", "access_revision")
    op.drop_column("users", "all_sites")
    op.drop_column("users", "last_login_at")
