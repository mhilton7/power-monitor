"""add existing-device-trust OTA v2 releases and deployments

Revision ID: 20260802_0026
Revises: 20260731_0025
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260802_0026"
down_revision = "20260731_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "firmware_releases",
        sa.Column("trust_mode", sa.String(40), nullable=False, server_default="ed25519_legacy"),
    )
    op.add_column("firmware_releases", sa.Column("project_name", sa.String(120)))
    op.add_column("firmware_releases", sa.Column("artifact_path", sa.String(500)))
    op.add_column("firmware_releases", sa.Column("build_hash", sa.String(128)))
    op.add_column("firmware_releases", sa.Column("git_commit", sa.String(64)))
    op.add_column("firmware_releases", sa.Column("build_timestamp", sa.DateTime(timezone=True)))
    op.add_column("firmware_releases", sa.Column("original_filename", sa.String(255)))
    op.add_column(
        "firmware_releases",
        sa.Column("uploaded_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.add_column(
        "firmware_releases",
        sa.Column("verification_status", sa.String(32), nullable=False, server_default="verified"),
    )
    op.add_column(
        "firmware_releases",
        sa.Column(
            "verification_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column(
        "firmware_releases", sa.Column("artifact_verified_at", sa.DateTime(timezone=True))
    )
    op.alter_column("firmware_releases", "file_path", existing_type=sa.String(500), nullable=True)
    op.alter_column("firmware_releases", "signature", existing_type=sa.Text(), nullable=True)
    op.alter_column(
        "firmware_releases", "signing_key_id", existing_type=sa.String(128), nullable=True
    )
    op.create_index("ix_firmware_releases_uploaded_by", "firmware_releases", ["uploaded_by"])
    op.create_index(
        "uq_firmware_release_v2_version_target",
        "firmware_releases",
        ["version", "hardware_target"],
        unique=True,
        postgresql_where=sa.text("trust_mode = 'existing_device_hmac'"),
        sqlite_where=sa.text("trust_mode = 'existing_device_hmac'"),
    )
    op.create_check_constraint(
        "firmware_release_trust_mode",
        "firmware_releases",
        "trust_mode IN ('existing_device_hmac','ed25519_legacy')",
    )
    op.create_check_constraint(
        "firmware_release_verification_status",
        "firmware_releases",
        "verification_status IN ('verified','rejected','quarantined')",
    )
    op.create_check_constraint(
        "firmware_release_size_positive", "firmware_releases", "size_bytes > 0"
    )

    op.add_column(
        "firmware_deployments",
        sa.Column("state", sa.String(32), nullable=False, server_default="scheduled"),
    )
    op.add_column("firmware_deployments", sa.Column("idempotency_key", sa.String(180)))
    op.add_column("firmware_deployments", sa.Column("rollout_group_id", sa.String(36)))
    op.add_column(
        "firmware_deployments",
        sa.Column("rollout_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("firmware_deployments", sa.Column("promoted_at", sa.DateTime(timezone=True)))
    op.add_column(
        "firmware_deployments",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "firmware_deployments",
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "firmware_deployments",
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "firmware_deployments",
        sa.Column("bytes_received", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column("firmware_deployments", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column(
        "firmware_deployments",
        sa.Column("allow_downgrade", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("firmware_deployments", sa.Column("failure_code", sa.String(80)))
    op.add_column("firmware_deployments", sa.Column("failure_summary", sa.String(500)))
    op.add_column("firmware_deployments", sa.Column("last_report_at", sa.DateTime(timezone=True)))
    op.add_column(
        "firmware_deployments",
        sa.Column("last_report_payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("firmware_deployments", sa.Column("validated_version", sa.String(80)))
    op.add_column("firmware_deployments", sa.Column("validated_build_hash", sa.String(128)))
    op.add_column("firmware_deployments", sa.Column("rollback_version", sa.String(80)))
    op.add_column("firmware_deployments", sa.Column("rollback_build_hash", sa.String(128)))
    op.add_column("firmware_deployments", sa.Column("last_boot_id", sa.String(80)))
    op.add_column(
        "firmware_deployments",
        sa.Column("verification_heartbeats", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "firmware_deployments",
        sa.Column("stabilization_started_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "firmware_deployments", sa.Column("reading_confirmed_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "firmware_deployments",
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(
        "UPDATE firmware_deployments SET state = CASE status "
        "WHEN 'available' THEN 'offered' "
        "WHEN 'downloaded' THEN 'binary_verified' "
        "WHEN 'installed' THEN 'awaiting_heartbeat' "
        "WHEN 'validated' THEN 'completed' "
        "WHEN 'failed' THEN 'failed' "
        "WHEN 'rolled_back' THEN 'rolled_back' "
        "ELSE 'scheduled' END"
    )
    op.execute(
        "WITH ranked AS ("
        "SELECT id, ROW_NUMBER() OVER (PARTITION BY device_id "
        "ORDER BY scheduled_at DESC, id DESC) AS active_rank "
        "FROM firmware_deployments WHERE state NOT IN "
        "('completed','failed','cancelled','rolled_back')) "
        "UPDATE firmware_deployments SET state = 'failed', status = 'failed', "
        "failure_code = 'migration_active_deployment_conflict', "
        "failure_summary = 'Superseded while enforcing one active deployment per sensor' "
        "WHERE id IN (SELECT id FROM ranked WHERE active_rank > 1)"
    )
    op.create_index("ix_firmware_deployments_state", "firmware_deployments", ["state"])
    op.create_index(
        "ix_firmware_deployments_rollout_group_id",
        "firmware_deployments",
        ["rollout_group_id"],
    )
    op.create_unique_constraint(
        "uq_firmware_deployment_device_idempotency",
        "firmware_deployments",
        ["device_id", "idempotency_key"],
    )
    op.create_index(
        "uq_firmware_deployment_active_device",
        "firmware_deployments",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("state NOT IN ('completed','failed','cancelled','rolled_back')"),
        sqlite_where=sa.text("state NOT IN ('completed','failed','cancelled','rolled_back')"),
    )
    op.create_check_constraint(
        "firmware_deployment_state",
        "firmware_deployments",
        "state IN ('waiting_canary','scheduled','offered','manifest_authenticated',"
        "'download_started',"
        "'downloading','binary_verified','partition_written','rebooting',"
        "'post_boot_validation','validated','awaiting_heartbeat','completed','failed',"
        "'cancelled','rollback_detected','rolled_back')",
    )
    op.create_check_constraint(
        "firmware_deployment_revision_positive", "firmware_deployments", "revision >= 1"
    )
    op.create_check_constraint(
        "firmware_deployment_rollout_order", "firmware_deployments", "rollout_order >= 0"
    )
    op.create_check_constraint(
        "firmware_deployment_attempt_positive", "firmware_deployments", "attempt >= 1"
    )
    op.create_check_constraint(
        "firmware_deployment_progress",
        "firmware_deployments",
        "progress >= 0 AND progress <= 100",
    )
    op.create_check_constraint(
        "firmware_deployment_bytes_nonnegative",
        "firmware_deployments",
        "bytes_received >= 0",
    )

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
                "code": "firmware.deploy",
                "group_name": "Sites and devices",
                "label": "Deploy firmware",
                "description": "Install, cancel, retry, and intentionally downgrade firmware.",
                "high_risk": True,
            }
        ],
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_name", sa.String()),
        sa.column("permission_code", sa.String()),
    )
    op.bulk_insert(
        role_permissions,
        [{"role_name": "admin", "permission_code": "firmware.deploy"}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE permission_code = 'firmware.deploy'")
    op.execute("DELETE FROM permissions WHERE code = 'firmware.deploy'")

    op.drop_constraint(
        "firmware_deployment_bytes_nonnegative", "firmware_deployments", type_="check"
    )
    op.drop_constraint("firmware_deployment_progress", "firmware_deployments", type_="check")
    op.drop_constraint(
        "firmware_deployment_attempt_positive", "firmware_deployments", type_="check"
    )
    op.drop_constraint(
        "firmware_deployment_revision_positive", "firmware_deployments", type_="check"
    )
    op.drop_constraint("firmware_deployment_rollout_order", "firmware_deployments", type_="check")
    op.drop_constraint("firmware_deployment_state", "firmware_deployments", type_="check")
    op.drop_index("uq_firmware_deployment_active_device", table_name="firmware_deployments")
    op.drop_index("ix_firmware_deployments_rollout_group_id", table_name="firmware_deployments")
    op.drop_constraint(
        "uq_firmware_deployment_device_idempotency",
        "firmware_deployments",
        type_="unique",
    )
    op.drop_index("ix_firmware_deployments_state", table_name="firmware_deployments")
    for column in (
        "created_at",
        "reading_confirmed_at",
        "stabilization_started_at",
        "verification_heartbeats",
        "last_boot_id",
        "rollback_build_hash",
        "rollback_version",
        "validated_build_hash",
        "validated_version",
        "last_report_payload",
        "last_report_at",
        "failure_summary",
        "failure_code",
        "allow_downgrade",
        "expires_at",
        "bytes_received",
        "progress",
        "attempt",
        "revision",
        "idempotency_key",
        "promoted_at",
        "rollout_order",
        "rollout_group_id",
        "state",
    ):
        op.drop_column("firmware_deployments", column)

    op.drop_constraint("firmware_release_size_positive", "firmware_releases", type_="check")
    op.drop_constraint("firmware_release_verification_status", "firmware_releases", type_="check")
    op.drop_constraint("firmware_release_trust_mode", "firmware_releases", type_="check")
    op.drop_index("uq_firmware_release_v2_version_target", table_name="firmware_releases")
    op.drop_index("ix_firmware_releases_uploaded_by", table_name="firmware_releases")
    op.execute(
        "UPDATE firmware_releases SET file_path = COALESCE(file_path, artifact_path), "
        "signature = COALESCE(signature, 'legacy-unavailable'), "
        "signing_key_id = COALESCE(signing_key_id, 'legacy-unavailable')"
    )
    op.alter_column(
        "firmware_releases", "signing_key_id", existing_type=sa.String(128), nullable=False
    )
    op.alter_column("firmware_releases", "signature", existing_type=sa.Text(), nullable=False)
    op.alter_column("firmware_releases", "file_path", existing_type=sa.String(500), nullable=False)
    for column in (
        "artifact_verified_at",
        "verification_evidence",
        "verification_status",
        "uploaded_by",
        "original_filename",
        "build_timestamp",
        "git_commit",
        "build_hash",
        "artifact_path",
        "project_name",
        "trust_mode",
    ):
        op.drop_column("firmware_releases", column)
