from __future__ import annotations

import re
from pathlib import Path

from app.db import models  # noqa: F401
from app.db.base import Base


def test_initial_migration_is_frozen_and_covers_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (root / "alembic" / "versions" / "20260720_0001_initial.py").read_text()
    schema = (root / "alembic" / "versions" / "20260720_0001_schema.sql").read_text()
    assert "Base.metadata" not in revision
    migrated_tables = set(re.findall(r"CREATE TABLE ([a-z_]+)", schema))
    assert migrated_tables == set(Base.metadata.tables) - {
        "device_lifecycle_events",
        "log_export_jobs",
        "rate_sync_configuration",
        "rate_sources",
        "background_jobs",
        "rate_source_checks",
        "rate_source_artifacts",
        "rate_extraction_results",
        "rate_change_candidates",
        "rate_candidate_differences",
        "rate_approval_decisions",
        "rate_version_sources",
        "rate_assignments",
        "permissions",
        "role_permissions",
        "role_revisions",
        "user_sites",
        "interface_text_revisions",
        "interface_text_drafts",
        "interface_text_state",
    }
    assert "CREATE UNIQUE INDEX" in schema
    assert "ix_raw_site_time" in schema
    assert "TIMESTAMP WITH TIME ZONE" in schema


def test_dashboard_correction_migration_is_additive() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (
        root / "alembic" / "versions" / "20260720_0002_dashboard_corrections.py"
    ).read_text()
    assert 'down_revision: str | None = "20260720_0001"' in revision
    assert 'op.add_column(\n        "devices"' in revision
    assert '"device_lifecycle_events"' in revision
    assert '"log_export_jobs"' in revision
    assert "UPDATE devices SET lifecycle_status = 'decommissioned'" in revision
    assert "def downgrade()" in revision


def test_weekly_rate_migration_is_append_only() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (root / "alembic" / "versions" / "20260720_0003_weekly_rates.py").read_text()
    assert 'down_revision: str | None = "20260720_0002"' in revision
    assert '"rate_source_artifacts"' in revision
    assert '"rate_change_candidates"' in revision
    assert '"rate_assignments"' in revision
    assert "DROP SCHEMA" not in revision
    assert "def downgrade()" in revision


def test_managed_rate_source_migration_is_append_only() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (root / "alembic" / "versions" / "20260720_0004_managed_rate_sources.py").read_text()
    assert 'down_revision: str | None = "20260720_0003"' in revision
    assert 'op.add_column("rate_sources"' in revision
    assert '"effective_from_hint"' in revision
    assert '"created_by"' in revision
    assert "DROP SCHEMA" not in revision
    assert "def downgrade()" in revision


def test_user_access_and_interface_text_migration_is_append_only() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (
        root / "alembic" / "versions" / "20260720_0005_user_access_interface_text.py"
    ).read_text()
    assert 'down_revision: str | None = "20260720_0004"' in revision
    assert 'op.add_column("users"' in revision
    assert '"role_permissions"' in revision
    assert '"user_sites"' in revision
    assert '"interface_text_revisions"' in revision
    assert '"interface_text_drafts"' in revision
    assert '"previewed_revision"' in revision
    assert '"uq_roles_display_name_lower"' in revision
    assert "DROP SCHEMA" not in revision
    assert "def downgrade()" in revision
