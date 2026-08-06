"""add durable coordinated data-only reset state

Revision ID: 20260806_0031
Revises: 20260803_0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

revision = "20260806_0031"
down_revision = "20260803_0030"
branch_labels = None
depends_on = None

_ACTIVE_OPERATION_PREDICATE = "state NOT IN ('completed','cancelled','failed_before_commit')"
_PROTOCOL_SEQUENCE_COLUMNS = (
    ("raw_readings", "sequence", False),
    ("sync_cursors", "highest_contiguous_sequence", False),
    ("sync_cursors", "maximum_seen_sequence", False),
    ("device_heartbeats", "newest_sequence", False),
    ("sequence_gaps", "start_sequence", False),
    ("sequence_gaps", "end_sequence", False),
    ("device_events", "event_sequence", True),
    ("device_event_sync_cursors", "highest_contiguous_sequence", False),
    ("device_event_sync_cursors", "maximum_seen_sequence", False),
)
_SEQUENCE_DOWNGRADE_PREDICATES = (
    "EXISTS (SELECT 1 FROM raw_readings WHERE sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM sync_cursors "
    "WHERE highest_contiguous_sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM sync_cursors "
    "WHERE maximum_seen_sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM device_heartbeats "
    "WHERE newest_sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM sequence_gaps "
    "WHERE start_sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM sequence_gaps "
    "WHERE end_sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM device_events "
    "WHERE event_sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM device_event_sync_cursors "
    "WHERE highest_contiguous_sequence NOT BETWEEN -2147483648 AND 2147483647)",
    "EXISTS (SELECT 1 FROM device_event_sync_cursors "
    "WHERE maximum_seen_sequence NOT BETWEEN -2147483648 AND 2147483647)",
)


def _alter_protocol_sequence_columns(*, widening: bool) -> None:
    existing_type = sa.Integer() if widening else sa.BigInteger()
    target_type = sa.BigInteger() if widening else sa.Integer()
    for table_name, column_name, nullable in _PROTOCOL_SEQUENCE_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=existing_type,
            type_=target_type,
            existing_nullable=nullable,
        )


def _sequence_downgrade_predicates() -> str:
    return " OR ".join(_SEQUENCE_DOWNGRADE_PREDICATES)


def _offline_sequence_downgrade_preflight() -> None:
    op.execute(
        "DO $$ BEGIN IF "
        f"{_sequence_downgrade_predicates()} "
        "THEN RAISE EXCEPTION "
        "'Cannot downgrade protocol sequences: a value exceeds INTEGER range'; "
        "END IF; END; $$"
    )


def _online_sequence_downgrade_preflight() -> None:
    exceeds_integer = bool(
        op.get_bind().execute(sa.text(f"SELECT {_sequence_downgrade_predicates()}")).scalar()
    )
    if exceeds_integer:
        raise RuntimeError("Cannot downgrade protocol sequences: a value exceeds INTEGER range")


def upgrade() -> None:
    # Reset boundaries can approach signed BIGINT. Every protocol sequence that
    # can contribute to or advance beyond that boundary must use the same width.
    # PostgreSQL type widening preserves rows, indexes, and unique constraints;
    # this upgrade performs no protocol-history deletion or sequence rewrite.
    _alter_protocol_sequence_columns(widening=True)

    op.drop_constraint("billing_cycle_boundary_source", "billing_cycles", type_="check")
    op.create_check_constraint(
        "billing_cycle_boundary_source",
        "billing_cycles",
        "boundary_source IN "
        "('generated','manual_override','utility_import','external_feed','data_reset')",
    )

    op.create_table(
        "data_reset_plans",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("site_id", sa.String(36), nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=True),
        sa.Column(
            "requested_categories", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "delete_imported_bill_documents",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "disconnected_sensor_policy",
            sa.String(32),
            nullable=False,
            server_default="defer_until_reconnect",
        ),
        sa.Column("plan_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("plan_fingerprint", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidation_reason", sa.String(160), nullable=True),
        sa.CheckConstraint("revision > 0", name="data_reset_plan_revision_positive"),
        sa.CheckConstraint(
            "expires_at > created_at", name="data_reset_plan_expiration_after_creation"
        ),
        sa.CheckConstraint(
            "disconnected_sensor_policy IN ('block','defer_until_reconnect')",
            name="data_reset_plan_disconnected_policy",
        ),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "site_id",
        "requested_by",
        "plan_fingerprint",
        "created_at",
        "expires_at",
        "invalidated_at",
    ):
        op.create_index(f"ix_data_reset_plans_{column}", "data_reset_plans", [column])

    op.create_table(
        "data_reset_operations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("plan_id", sa.String(36), nullable=False),
        sa.Column("site_id", sa.String(36), nullable=False),
        sa.Column("requested_by", sa.String(36), nullable=True),
        sa.Column("state", sa.String(64), nullable=False, server_default="awaiting_confirmation"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reset_generation", sa.Integer(), nullable=False),
        sa.Column("reset_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "requested_categories", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "delete_imported_bill_documents",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "disconnected_sensor_policy",
            sa.String(32),
            nullable=False,
            server_default="defer_until_reconnect",
        ),
        sa.Column(
            "backup_mode",
            sa.String(32),
            nullable=False,
            server_default="verified_backup",
        ),
        sa.Column("backup_run_id", sa.String(36), nullable=True),
        sa.Column("backup_reference", sa.String(500), nullable=True),
        sa.Column("backup_checksum", sa.String(64), nullable=True),
        sa.Column("backup_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("central_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("failure_summary", sa.String(500), nullable=True),
        sa.Column("final_evidence", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("revision > 0", name="data_reset_operation_revision_positive"),
        sa.CheckConstraint("reset_generation > 0", name="data_reset_operation_generation_positive"),
        sa.CheckConstraint("plan_revision > 0", name="data_reset_operation_plan_revision_positive"),
        sa.CheckConstraint(
            "backup_mode IN ('verified_backup','permanent_without_backup')",
            name="data_reset_operation_backup_mode",
        ),
        sa.CheckConstraint(
            "disconnected_sensor_policy IN ('block','defer_until_reconnect')",
            name="data_reset_operation_disconnected_policy",
        ),
        sa.CheckConstraint(
            "state IN ('planning','awaiting_confirmation','preparing_sensors',"
            "'sensors_prepared','backup_running','backup_verified',"
            "'database_reset_running','database_reset_committed',"
            "'sensor_commit_running','verification_running','completed',"
            "'completed_with_resets_pending_on_reconnect','partial_failure',"
            "'attention_required','cancelled','failed_before_commit')",
            name="data_reset_operation_state",
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["data_reset_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["backup_run_id"], ["backup_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", name="uq_data_reset_operation_plan"),
        sa.UniqueConstraint(
            "site_id", "idempotency_key", name="uq_data_reset_operation_idempotency"
        ),
    )
    for column in (
        "site_id",
        "requested_by",
        "state",
        "reset_timestamp",
        "backup_run_id",
        "started_at",
        "central_commit_at",
        "completed_at",
    ):
        op.create_index(f"ix_data_reset_operations_{column}", "data_reset_operations", [column])
    op.create_index(
        "uq_data_reset_operation_active_site",
        "data_reset_operations",
        ["site_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_OPERATION_PREDICATE),
        sqlite_where=sa.text(_ACTIVE_OPERATION_PREDICATE),
    )

    op.create_table(
        "data_reset_participants",
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(48), nullable=False, server_default="pending"),
        sa.Column("planned_classification", sa.String(24), nullable=False),
        sa.Column("reset_generation", sa.Integer(), nullable=False),
        sa.Column("reset_boundary", sa.BigInteger(), nullable=False),
        sa.Column("old_sequence_floor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("old_next_sequence", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("new_sequence_floor", sa.BigInteger(), nullable=True),
        sa.Column("new_next_sequence", sa.BigInteger(), nullable=True),
        sa.Column("server_highest_contiguous", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("server_maximum_seen", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sensor_ack_sequence", sa.BigInteger(), nullable=True),
        sa.Column("sensor_newest_sequence", sa.BigInteger(), nullable=True),
        sa.Column("boot_id", sa.String(80), nullable=True),
        sa.Column("firmware_version", sa.String(80), nullable=True),
        sa.Column("firmware_build_hash", sa.String(128), nullable=True),
        sa.Column("card_generation", sa.String(128), nullable=True),
        sa.Column(
            "prepare_receipt_safe", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("commit_receipt_safe", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("prepare_receipt_digest", sa.String(64), nullable=True),
        sa.Column("commit_receipt_digest", sa.String(64), nullable=True),
        sa.Column("preservation_hash_before", sa.String(64), nullable=True),
        sa.Column("preservation_hash_after", sa.String(64), nullable=True),
        sa.Column("failure_code", sa.String(80), nullable=True),
        sa.Column("failure_summary", sa.String(500), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("commit_authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "reset_generation > 0", name="data_reset_participant_generation_positive"
        ),
        sa.CheckConstraint(
            "reset_boundary >= 0 AND old_sequence_floor >= 0 AND old_next_sequence > 0",
            name="data_reset_participant_old_sequence_bounds",
        ),
        sa.CheckConstraint(
            "new_sequence_floor IS NULL OR new_sequence_floor >= reset_boundary",
            name="data_reset_participant_new_floor_boundary",
        ),
        sa.CheckConstraint(
            "new_next_sequence IS NULL OR new_next_sequence > reset_boundary",
            name="data_reset_participant_new_next_boundary",
        ),
        sa.CheckConstraint(
            "state IN ('pending','unreachable','unsupported','prepare_requested',"
            "'prepared','commit_requested','committed','verified','pending_reconnect',"
            "'failed','attention_required','not_applicable')",
            name="data_reset_participant_state",
        ),
        sa.CheckConstraint(
            "planned_classification IN "
            "('connected','authentication_failed','disconnected','unsupported','revoked','removed')",
            name="data_reset_participant_planned_classification",
        ),
        sa.ForeignKeyConstraint(["operation_id"], ["data_reset_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("operation_id", "device_id"),
    )
    op.create_index("ix_data_reset_participants_state", "data_reset_participants", ["state"])
    op.create_index(
        "ix_data_reset_participant_device_state",
        "data_reset_participants",
        ["device_id", "state"],
    )

    op.create_table(
        "site_data_states",
        sa.Column("site_id", sa.String(36), nullable=False),
        sa.Column("data_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("history_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reset_operation_id", sa.String(36), nullable=True),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("data_generation >= 0", name="site_data_generation_nonnegative"),
        sa.CheckConstraint("history_revision >= 0", name="site_history_revision_nonnegative"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["last_reset_operation_id"], ["data_reset_operations.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("site_id"),
    )

    op.create_table(
        "device_data_states",
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("site_id", sa.String(36), nullable=False),
        sa.Column("data_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reset_boundary", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("ingestion_gate", sa.String(32), nullable=False, server_default="open"),
        sa.Column(
            "reset_required_on_reconnect",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("active_operation_id", sa.String(36), nullable=True),
        sa.Column("last_completed_operation_id", sa.String(36), nullable=True),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "generation_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("data_generation >= 0", name="device_data_generation_nonnegative"),
        sa.CheckConstraint("reset_boundary >= 0", name="device_reset_boundary_nonnegative"),
        sa.CheckConstraint(
            "ingestion_gate IN ('open','preparing','blocked','pending_reconnect',"
            "'committing','verifying','attention_required')",
            name="device_data_ingestion_gate",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_id"], ["sites.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["active_operation_id"], ["data_reset_operations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["last_completed_operation_id"],
            ["data_reset_operations.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("device_id"),
    )
    for column in (
        "site_id",
        "ingestion_gate",
        "reset_required_on_reconnect",
        "active_operation_id",
    ):
        op.create_index(f"ix_device_data_states_{column}", "device_data_states", [column])

    op.create_table(
        "data_reset_pricing_baselines",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("operation_id", sa.String(36), nullable=False),
        sa.Column("utility_account_id", sa.String(36), nullable=False),
        sa.Column("rate_plan_id", sa.String(36), nullable=False),
        sa.Column("rate_version_id", sa.String(36), nullable=False),
        sa.Column("rate_assignment_id", sa.String(36), nullable=False),
        sa.Column("billing_cycle_id", sa.String(36), nullable=True),
        sa.Column("data_generation", sa.Integer(), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pricing_configuration_hash", sa.String(64), nullable=False),
        sa.Column("pricing_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("data_generation > 0", name="data_reset_pricing_generation_positive"),
        sa.ForeignKeyConstraint(["operation_id"], ["data_reset_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["utility_account_id"], ["utility_accounts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["rate_plan_id"], ["rate_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["rate_version_id"], ["rate_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "utility_account_id",
            name="uq_data_reset_pricing_baseline_account",
        ),
    )
    for column in (
        "operation_id",
        "utility_account_id",
        "rate_plan_id",
        "rate_version_id",
        "rate_assignment_id",
        "billing_cycle_id",
        "effective_at",
    ):
        op.create_index(
            f"ix_data_reset_pricing_baselines_{column}",
            "data_reset_pricing_baselines",
            [column],
        )

    op.add_column(
        "raw_readings",
        sa.Column("data_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "raw_data_generation_nonnegative", "raw_readings", "data_generation >= 0"
    )
    op.create_index(
        "ix_raw_device_generation_time",
        "raw_readings",
        ["device_id", "data_generation", "interval_start"],
    )

    op.add_column(
        "sync_cursors",
        sa.Column("data_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sync_cursors",
        sa.Column("reset_boundary", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "sync_cursor_generation_nonnegative", "sync_cursors", "data_generation >= 0"
    )
    op.create_check_constraint(
        "sync_cursor_boundary_nonnegative", "sync_cursors", "reset_boundary >= 0"
    )

    op.add_column(
        "device_heartbeats",
        sa.Column("data_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_check_constraint(
        "device_heartbeat_generation_nonnegative",
        "device_heartbeats",
        "data_generation >= 0",
    )

    op.execute(
        "INSERT INTO site_data_states "
        "(site_id, data_generation, history_revision, updated_at) "
        "SELECT id, 0, 0, CURRENT_TIMESTAMP FROM sites"
    )
    op.execute(
        "INSERT INTO device_data_states "
        "(device_id, site_id, data_generation, reset_boundary, ingestion_gate, "
        "reset_required_on_reconnect, generation_updated_at, updated_at) "
        "SELECT id, site_id, 0, 0, 'open', false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
        "FROM devices"
    )

    op.execute(
        sa.text(
            "INSERT INTO permissions (code, group_name, label, description, high_risk) "
            "SELECT 'system.data_reset', 'Administration', "
            "'Reset readings and pricing history', "
            "'Coordinate a protected data-only reset across the server and assigned sensors.', "
            ":high_risk WHERE NOT EXISTS "
            "(SELECT 1 FROM permissions WHERE code = 'system.data_reset')"
        ).bindparams(high_risk=True)
    )
    op.execute(
        "INSERT INTO role_permissions (role_name, permission_code) "
        "SELECT 'admin', 'system.data_reset' WHERE EXISTS "
        "(SELECT 1 FROM roles WHERE name = 'admin') AND NOT EXISTS "
        "(SELECT 1 FROM role_permissions WHERE role_name = 'admin' "
        "AND permission_code = 'system.data_reset')"
    )


def downgrade() -> None:
    if context.is_offline_mode():
        _offline_sequence_downgrade_preflight()
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM data_reset_operations) OR "
            "EXISTS (SELECT 1 FROM raw_readings WHERE data_generation <> 0) OR "
            "EXISTS (SELECT 1 FROM sync_cursors WHERE data_generation <> 0 "
            "OR reset_boundary <> 0) OR "
            "EXISTS (SELECT 1 FROM device_heartbeats WHERE data_generation <> 0) "
            "THEN RAISE EXCEPTION 'Cannot downgrade data-reset state after use'; "
            "END IF; END; $$"
        )
    else:
        _online_sequence_downgrade_preflight()
        connection = op.get_bind()
        reset_operations = int(
            connection.execute(sa.text("SELECT COUNT(*) FROM data_reset_operations")).scalar() or 0
        )
        nonzero_generations = int(
            connection.execute(
                sa.text(
                    "SELECT "
                    "(SELECT COUNT(*) FROM raw_readings WHERE data_generation <> 0) + "
                    "(SELECT COUNT(*) FROM sync_cursors WHERE data_generation <> 0 "
                    "OR reset_boundary <> 0) + "
                    "(SELECT COUNT(*) FROM device_heartbeats WHERE data_generation <> 0)"
                )
            ).scalar()
            or 0
        )
        if reset_operations or nonzero_generations:
            raise RuntimeError(
                "Cannot downgrade data-reset state after an operation or nonzero generation exists"
            )

    op.drop_constraint("billing_cycle_boundary_source", "billing_cycles", type_="check")
    op.create_check_constraint(
        "billing_cycle_boundary_source",
        "billing_cycles",
        "boundary_source IN ('generated','manual_override','utility_import','external_feed')",
    )

    op.execute("DELETE FROM role_permissions WHERE permission_code = 'system.data_reset'")
    op.execute("DELETE FROM permissions WHERE code = 'system.data_reset'")

    op.drop_constraint(
        "device_heartbeat_generation_nonnegative", "device_heartbeats", type_="check"
    )
    op.drop_column("device_heartbeats", "data_generation")

    op.drop_constraint("sync_cursor_boundary_nonnegative", "sync_cursors", type_="check")
    op.drop_constraint("sync_cursor_generation_nonnegative", "sync_cursors", type_="check")
    op.drop_column("sync_cursors", "reset_boundary")
    op.drop_column("sync_cursors", "data_generation")

    op.drop_index("ix_raw_device_generation_time", table_name="raw_readings")
    op.drop_constraint("raw_data_generation_nonnegative", "raw_readings", type_="check")
    op.drop_column("raw_readings", "data_generation")

    op.drop_table("data_reset_pricing_baselines")
    op.drop_table("device_data_states")
    op.drop_table("site_data_states")
    op.drop_table("data_reset_participants")
    op.drop_table("data_reset_operations")
    op.drop_table("data_reset_plans")

    _alter_protocol_sequence_columns(widening=False)
