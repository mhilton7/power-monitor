"""make the built-in Viewer strictly read-only

Revision ID: 20260731_0022
Revises: 20260730_0021
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260731_0022"
down_revision = "20260730_0021"
branch_labels = None
depends_on = None

VIEWER_PERMISSIONS = [
    "alerts.view",
    "costs.view",
    "devices.view",
    "history.view",
    "overview.view",
    "rates.view",
    "sites.view",
    "status_indicators.view",
    "topology.view",
    "usage.view",
    "utility_accounts.view",
]


def _uuid() -> str:
    return str(uuid.uuid4())


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions "
            "WHERE role_name = 'viewer' "
            "AND permission_code IN ('history.export', 'costs.export')"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE roles SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE name = 'viewer'"
        )
    )
    role = (
        connection.execute(
            sa.text("SELECT revision, display_name, description FROM roles WHERE name = 'viewer'")
        )
        .mappings()
        .one()
    )
    connection.execute(
        sa.text(
            "INSERT INTO role_revisions "
            "(id, role_name, revision, display_name, description, permissions, "
            "created_by, created_at, reason) "
            "VALUES (:id, 'viewer', :revision, :display_name, :description, "
            "CAST(:permissions AS JSON), NULL, CURRENT_TIMESTAMP, :reason)"
        ),
        {
            "id": _uuid(),
            "revision": role["revision"],
            "display_name": role["display_name"],
            "description": role["description"],
            "permissions": json.dumps(VIEWER_PERMISSIONS),
            "reason": "Built-in Viewer restricted to safe Home, History, and Billing reads",
        },
    )
    connection.execute(
        sa.text(
            "UPDATE users SET access_revision = access_revision + 1 "
            "WHERE id IN (SELECT user_id FROM user_roles WHERE role_name = 'viewer')"
        )
    )
    connection.execute(
        sa.text(
            "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP "
            "WHERE revoked_at IS NULL "
            "AND user_id IN (SELECT user_id FROM user_roles WHERE role_name = 'viewer')"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO audit_events "
            "(id, occurred_at, actor_type, actor_id, action, object_type, object_id, "
            "source_ip, outcome, correlation_id, details) "
            "VALUES (:id, CURRENT_TIMESTAMP, 'system', NULL, 'role.builtin_migrated', "
            "'role', 'viewer', NULL, 'success', NULL, CAST(:details AS JSON))"
        ),
        {
            "id": _uuid(),
            "details": json.dumps(
                {
                    "removed_permissions": ["history.export", "costs.export"],
                    "sessions_revoked": True,
                }
            ),
        },
    )


def downgrade() -> None:
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_name", sa.String()),
        sa.column("permission_code", sa.String()),
    )
    op.bulk_insert(
        role_permissions,
        [
            {"role_name": "viewer", "permission_code": "history.export"},
            {"role_name": "viewer", "permission_code": "costs.export"},
        ],
    )
    op.execute(
        "UPDATE roles SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP "
        "WHERE name = 'viewer'"
    )
