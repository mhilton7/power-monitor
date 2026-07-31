"""add detailed notification lifecycle, suppressions, and immutable history

Revision ID: 20260731_0023
Revises: 20260731_0022
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260731_0023"
down_revision = "20260731_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_instances",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE alert_instances SET last_seen_at = opened_at WHERE last_seen_at IS NULL")
    op.alter_column("alert_instances", "last_seen_at", nullable=False)
    op.create_index("ix_alert_instances_last_seen_at", "alert_instances", ["last_seen_at"])
    op.add_column(
        "alert_instances",
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("alert_instances", sa.Column("silenced_at", sa.DateTime(timezone=True)))
    op.add_column("alert_instances", sa.Column("silenced_by", sa.String(36)))
    op.add_column("alert_instances", sa.Column("silence_note", sa.String(500)))
    op.create_foreign_key(
        "fk_alert_instances_silenced_by_users",
        "alert_instances",
        "users",
        ["silenced_by"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "notification_attempts",
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE notification_attempts SET queued_at = attempted_at WHERE queued_at IS NULL")
    op.alter_column("notification_attempts", "queued_at", nullable=False)
    op.add_column("notification_attempts", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("notification_attempts", sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.add_column("notification_attempts", sa.Column("safe_error_code", sa.String(80)))
    op.add_column("notification_attempts", sa.Column("safe_error_summary", sa.String(500)))

    op.create_table(
        "notification_suppressions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("suppression_key", sa.String(160), nullable=False),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("scope_type", sa.String(16), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("site_id", sa.String(36), sa.ForeignKey("sites.id", ondelete="RESTRICT")),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("source_notification_id", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("restored_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("restored_at", sa.DateTime(timezone=True)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("scope_type IN ('user','home')", name="notification_suppression_scope"),
        sa.CheckConstraint(
            "(scope_type = 'user' AND user_id IS NOT NULL AND site_id IS NULL) OR "
            "(scope_type = 'home' AND site_id IS NOT NULL AND user_id IS NULL)",
            name="notification_suppression_target",
        ),
    )
    op.create_index(
        "ix_notification_suppressions_suppression_key",
        "notification_suppressions",
        ["suppression_key"],
    )
    op.create_index(
        "ix_notification_suppressions_user_id", "notification_suppressions", ["user_id"]
    )
    op.create_index(
        "ix_notification_suppressions_site_id", "notification_suppressions", ["site_id"]
    )
    op.create_index(
        "ix_notification_suppressions_created_at", "notification_suppressions", ["created_at"]
    )
    op.create_index("ix_notification_suppressions_active", "notification_suppressions", ["active"])
    op.create_index(
        "uq_notification_suppression_active_user",
        "notification_suppressions",
        ["suppression_key", "user_id"],
        unique=True,
        postgresql_where=sa.text("active AND user_id IS NOT NULL"),
        sqlite_where=sa.text("active = 1 AND user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_notification_suppression_active_home",
        "notification_suppressions",
        ["suppression_key", "site_id"],
        unique=True,
        postgresql_where=sa.text("active AND site_id IS NOT NULL"),
        sqlite_where=sa.text("active = 1 AND site_id IS NOT NULL"),
    )

    op.create_table(
        "notification_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("notification_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("site_id", sa.String(36), sa.ForeignKey("sites.id", ondelete="SET NULL")),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("resource_type", sa.String(40)),
        sa.Column("resource_id", sa.String(80)),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    for column in (
        "notification_id",
        "event_type",
        "occurred_at",
        "actor_id",
        "site_id",
        "category",
        "severity",
    ):
        op.create_index(f"ix_notification_events_{column}", "notification_events", [column])


def downgrade() -> None:
    op.drop_table("notification_events")
    op.drop_table("notification_suppressions")
    op.drop_constraint(
        "fk_alert_instances_silenced_by_users", "alert_instances", type_="foreignkey"
    )
    for column in ("silence_note", "silenced_by", "silenced_at", "occurrence_count"):
        op.drop_column("alert_instances", column)
    op.drop_index("ix_alert_instances_last_seen_at", table_name="alert_instances")
    op.drop_column("alert_instances", "last_seen_at")
    for column in (
        "safe_error_summary",
        "safe_error_code",
        "completed_at",
        "started_at",
        "queued_at",
    ):
        op.drop_column("notification_attempts", column)
