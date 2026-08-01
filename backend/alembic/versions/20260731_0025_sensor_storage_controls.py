"""add sensor storage control permissions and graduated alert rules

Revision ID: 20260731_0025
Revises: 20260731_0024
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "20260731_0025"
down_revision = "20260731_0024"
branch_labels = None
depends_on = None


STORAGE_ALERTS = (
    ("Storage pressure notice", "storage_pressure_notice", "info"),
    ("Storage pressure warning", "storage_pressure_warning", "warning"),
    ("Storage pressure critical", "storage_pressure_critical", "critical"),
    ("Storage pressure emergency", "storage_pressure_emergency", "critical"),
    ("Storage cleanup blocked", "storage_cleanup_blocked", "critical"),
    (
        "Storage write reserve unavailable",
        "storage_write_reserve_unavailable",
        "critical",
    ),
    ("Durable storage interval dropped", "storage_interval_dropped", "critical"),
)


def _uuid() -> str:
    return str(uuid.uuid4())


def upgrade() -> None:
    op.add_column("device_events", sa.Column("event_sequence", sa.Integer()))
    op.create_index("ix_device_events_event_sequence", "device_events", ["event_sequence"])
    op.create_unique_constraint(
        "uq_device_event_sequence", "device_events", ["device_id", "event_sequence"]
    )
    op.create_table(
        "device_event_sync_cursors",
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("highest_contiguous_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("maximum_seen_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    connection = op.get_bind()
    permissions = sa.table(
        "permissions",
        sa.column("code", sa.String()),
        sa.column("group_name", sa.String()),
        sa.column("label", sa.String()),
        sa.column("description", sa.String()),
        sa.column("high_risk", sa.Boolean()),
    )
    op.bulk_insert(
        permissions,
        [
            {
                "code": "storage.view",
                "group_name": "Sites and devices",
                "label": "View sensor storage",
                "description": (
                    "View capacity, pressure, acknowledgement, retention, and cleanup evidence."
                ),
                "high_risk": False,
            },
            {
                "code": "storage.manage",
                "group_name": "Sites and devices",
                "label": "Manage sensor storage",
                "description": (
                    "Change protected retention, request cleanup, and prepare cards for removal."
                ),
                "high_risk": True,
            },
        ],
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_name", sa.String()),
        sa.column("permission_code", sa.String()),
    )
    op.bulk_insert(
        role_permissions,
        [
            {"role_name": role, "permission_code": "storage.view"}
            for role in ("admin", "operator", "rate-manager", "viewer")
        ]
        + [
            {"role_name": "admin", "permission_code": "storage.manage"},
            {"role_name": "operator", "permission_code": "storage.manage"},
        ],
    )
    for name, rule_type, severity in STORAGE_ALERTS:
        connection.execute(
            sa.text(
                "INSERT INTO alert_rules "
                "(id, name, rule_type, severity, enabled, debounce_seconds, "
                "resolve_seconds, configuration, created_at, updated_at) "
                "VALUES (:id, :name, :rule_type, :severity, TRUE, 0, 0, "
                "CAST(:configuration AS JSON), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": _uuid(),
                "name": name,
                "rule_type": rule_type,
                "severity": severity,
                "configuration": "{}",
            },
        )


def downgrade() -> None:
    rule_types = tuple(item[1] for item in STORAGE_ALERTS)
    connection = op.get_bind()
    connection.execute(
        sa.text("DELETE FROM alert_rules WHERE rule_type IN :rule_types").bindparams(
            sa.bindparam("rule_types", expanding=True)
        ),
        {"rule_types": rule_types},
    )
    op.execute(
        "DELETE FROM role_permissions WHERE permission_code IN ('storage.view','storage.manage')"
    )
    op.execute("DELETE FROM permissions WHERE code IN ('storage.view','storage.manage')")
    op.drop_table("device_event_sync_cursors")
    op.drop_constraint("uq_device_event_sequence", "device_events", type_="unique")
    op.drop_index("ix_device_events_event_sequence", table_name="device_events")
    op.drop_column("device_events", "event_sequence")
