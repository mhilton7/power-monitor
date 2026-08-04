"""add covering indexes for exact coarse history reads

Revision ID: 20260803_0029
Revises: 20260803_0028
"""

from __future__ import annotations

from alembic import op

revision = "20260803_0029"
down_revision = "20260803_0028"
branch_labels = None
depends_on = None

INDEXES = (
    (
        "ix_raw_device_time_end",
        "raw_readings",
        ["device_id", "interval_end", "interval_start"],
    ),
    (
        "ix_normalized_device_time_end",
        "normalized_intervals",
        ["device_id", "interval_end", "interval_start"],
    ),
    (
        "ix_tier_segment_account_time_recalc",
        "tier_allocation_segments",
        [
            "utility_account_id",
            "interval_start",
            "interval_end",
            "recalculation_version",
        ],
    ),
    (
        "ix_tier_segment_version_time",
        "tier_allocation_segments",
        ["rate_version_id", "interval_start", "interval_end"],
    ),
)

TIER_HISTORY_COVER_INDEX = "ix_tier_segment_history_cover"
TIER_HISTORY_COVER_COLUMNS = [
    "utility_account_id",
    "rate_version_id",
    "interval_start",
    "interval_end",
]
TIER_HISTORY_INCLUDED_COLUMNS = [
    "billing_cycle_id",
    "normalized_interval_id",
    "recalculation_version",
    "tier_stable_id",
    "tier_name",
    "tou_period",
    "cumulative_start_kwh",
    "cumulative_end_kwh",
    "segment_energy_kwh",
    "price_per_kwh",
    "unrounded_energy_charge",
    "usage_authority_type",
]


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # These are high-volume ingestion tables. PostgreSQL must build their
        # release indexes without holding a write-blocking table lock.
        with op.get_context().autocommit_block():
            for name, table, columns in INDEXES:
                op.create_index(
                    name,
                    table,
                    columns,
                    postgresql_concurrently=True,
                )
            op.create_index(
                TIER_HISTORY_COVER_INDEX,
                "tier_allocation_segments",
                TIER_HISTORY_COVER_COLUMNS,
                postgresql_include=TIER_HISTORY_INCLUDED_COLUMNS,
                postgresql_concurrently=True,
            )
        return
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
    op.create_index(
        TIER_HISTORY_COVER_INDEX,
        "tier_allocation_segments",
        TIER_HISTORY_COVER_COLUMNS,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.drop_index(
                TIER_HISTORY_COVER_INDEX,
                table_name="tier_allocation_segments",
                postgresql_concurrently=True,
            )
            for name, table, _columns in reversed(INDEXES):
                op.drop_index(
                    name,
                    table_name=table,
                    postgresql_concurrently=True,
                )
        return
    op.drop_index(TIER_HISTORY_COVER_INDEX, table_name="tier_allocation_segments")
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
