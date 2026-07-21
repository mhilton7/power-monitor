"""Add immutable status-indicator layout revisions and drafts.

Revision ID: 20260720_0006
Revises: 20260720_0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260720_0006"
down_revision = "20260720_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "status_layout_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False, unique=True),
        sa.Column("registry_version", sa.String(64), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column(
            "restored_from_id",
            sa.String(36),
            sa.ForeignKey("status_layout_revisions.id", ondelete="SET NULL"),
        ),
    )
    op.create_index(
        "ix_status_layout_revisions_revision", "status_layout_revisions", ["revision"], unique=True
    )
    op.create_index(
        "ix_status_layout_revisions_created_by", "status_layout_revisions", ["created_by"]
    )
    op.create_index(
        "ix_status_layout_revisions_created_at", "status_layout_revisions", ["created_at"]
    )

    op.create_table(
        "status_layout_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("base_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("previewed_revision", sa.Integer()),
        sa.Column("registry_version", sa.String(64), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("edited_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_status_layout_drafts_edited_by", "status_layout_drafts", ["edited_by"])
    op.create_index("ix_status_layout_drafts_updated_at", "status_layout_drafts", ["updated_at"])

    op.create_table(
        "status_layout_state",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "current_revision_id",
            sa.String(36),
            sa.ForeignKey("status_layout_revisions.id", ondelete="RESTRICT"),
        ),
        sa.Column("current_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Static SQL keeps this seed renderable by Alembic's mandatory ``--sql`` gate;
    # SQLAlchemy cannot literal-render a Python dict for a PostgreSQL JSON column.
    op.execute(
        "INSERT INTO status_layout_revisions "
        "(id, revision, registry_version, configuration, created_by, created_at, "
        "reason, restored_from_id) VALUES ("
        "'00000000-0000-4000-8000-000000000006', 1, 'status-indicators/1.0', "
        "json_build_object("
        "'schema_version', 'power-monitor-status-layout/1.0', "
        "'registry_version', 'status-indicators/1.0', "
        "'personalization_enabled', false, 'items', json_build_array()), "
        "NULL, CURRENT_TIMESTAMP, "
        "'Compiled dashboard layout captured during migration', NULL)"
    )
    op.execute(
        "INSERT INTO status_layout_state "
        "(id, current_revision_id, current_revision, updated_at) VALUES "
        "('current', '00000000-0000-4000-8000-000000000006', 1, CURRENT_TIMESTAMP)"
    )

    permissions = sa.table(
        "permissions",
        sa.column("code", sa.String(80)),
        sa.column("group_name", sa.String(80)),
        sa.column("label", sa.String(120)),
        sa.column("description", sa.String(500)),
        sa.column("high_risk", sa.Boolean()),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "code": "status_indicators.view",
                "group_name": "Administration",
                "label": "View status layouts",
                "description": "View registered indicators and the effective published layout.",
                "high_risk": False,
            },
            {
                "code": "status_indicators.manage",
                "group_name": "Administration",
                "label": "Manage status layouts",
                "description": (
                    "Draft, preview, publish, import, reset, and restore status layouts."
                ),
                "high_risk": True,
            },
        ],
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_name", sa.String(32)),
        sa.column("permission_code", sa.String(80)),
    )
    op.bulk_insert(
        role_permissions,
        [
            {"role_name": role, "permission_code": "status_indicators.view"}
            for role in ("admin", "operator", "rate-manager", "viewer")
        ]
        + [{"role_name": "admin", "permission_code": "status_indicators.manage"}],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_code IN "
            "('status_indicators.view', 'status_indicators.manage')"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM permissions WHERE code IN "
            "('status_indicators.view', 'status_indicators.manage')"
        )
    )
    op.drop_table("status_layout_state")
    op.drop_index("ix_status_layout_drafts_updated_at", table_name="status_layout_drafts")
    op.drop_index("ix_status_layout_drafts_edited_by", table_name="status_layout_drafts")
    op.drop_table("status_layout_drafts")
    op.drop_index("ix_status_layout_revisions_created_at", table_name="status_layout_revisions")
    op.drop_index("ix_status_layout_revisions_created_by", table_name="status_layout_revisions")
    op.drop_index("ix_status_layout_revisions_revision", table_name="status_layout_revisions")
    op.drop_table("status_layout_revisions")
