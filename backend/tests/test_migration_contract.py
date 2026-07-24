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
        "status_layout_revisions",
        "status_layout_drafts",
        "status_layout_state",
        "utility_account_adjustments",
        "sensor_network_policies",
        "sensor_network_cidrs",
        "network_policy_revisions",
        "rate_tier_definitions",
        "rate_threshold_rules",
        "rate_seasonal_baselines",
        "account_usage_authorities",
        "manual_account_usage",
        "utility_usage_imports",
        "tier_allocation_segments",
        "cycle_tier_summaries",
        "tier_projection_snapshots",
        "account_reconciliation_adjustments",
        "utility_bill_imports",
        "utility_bill_extraction_revisions",
        "utility_bill_extracted_fields",
        "utility_bill_field_conflicts",
        "utility_bill_cycle_drafts",
        "device_site_assignments",
        "utility_account_site_assignments",
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


def test_status_indicator_layout_migration_is_append_only() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (
        root / "alembic" / "versions" / "20260720_0006_status_indicators_layout.py"
    ).read_text()
    assert 'down_revision = "20260720_0005"' in revision
    assert '"status_layout_revisions"' in revision
    assert '"status_layout_drafts"' in revision
    assert '"status_layout_state"' in revision
    assert '"status_indicators.view"' in revision
    assert '"status_indicators.manage"' in revision
    assert "DROP SCHEMA" not in revision
    assert "def downgrade()" in revision


def test_dashboard_information_architecture_migration_preserves_layout_history() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (
        root / "alembic" / "versions" / "20260721_0007_dashboard_information_architecture.py"
    ).read_text()
    assert 'down_revision = "20260720_0006"' in revision
    assert "INSERT INTO status_layout_revisions" in revision
    assert "restored_from_id" in revision
    assert "diagnostics_summary" in revision
    assert "status_layout.information_architecture_migrated" in revision
    assert "DELETE FROM status_layout_revisions" in revision
    assert "DROP TABLE" not in revision


def test_utility_account_network_policy_migration_preserves_legacy_behavior() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (
        root / "alembic" / "versions" / "20260721_0008_utility_accounts_network_policy.py"
    ).read_text()
    assert 'down_revision = "20260721_0007"' in revision
    assert "sensor_network_policies" in revision
    assert "sensor_network_cidrs" in revision
    assert "network_policy_revisions" in revision
    assert "utility_account_adjustments" in revision
    assert "legacy_authenticated_any" in revision
    assert "legacy_public_and_listed" in revision
    assert "WHEN json_array_length(sites.allowed_cidrs) > 0" in revision
    assert "ELSE 'deny_all'" in revision
    assert "behavior_preserved" in revision
    assert "utility_accounts.manage" in revision
    assert "network.manage" in revision


def test_utility_bill_import_migration_is_additive_private_and_indexed() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (root / "alembic" / "versions" / "20260724_0010_utility_bill_imports.py").read_text()
    assert 'down_revision = "20260723_0009"' in revision
    assert "utility_bill_imports" in revision
    assert "utility_bill_extraction_revisions" in revision
    assert "utility_bill_extracted_fields" in revision
    assert "utility_bill_field_conflicts" in revision
    assert "utility_bill_cycle_drafts" in revision
    assert "utility_bills.view" in revision
    assert "utility_bills.manage" in revision
    assert "sa.ForeignKey" in revision
    assert "op.create_index" in revision
    assert "DROP SCHEMA" not in revision
    assert "def downgrade()" in revision


def test_user_lifecycle_cleanup_migration_is_additive_and_preserves_identity() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (
        root / "alembic" / "versions" / "20260724_0011_user_lifecycle_cleanup.py"
    ).read_text()
    assert 'down_revision = "20260724_0010"' in revision
    assert 'op.add_column(\n        "users"' in revision
    assert "lifecycle_state" in revision
    assert "is_protected" in revision
    assert "removed_role_ids" in revision
    assert "removed_site_ids" in revision
    assert "users.disable" in revision
    assert "users.remove" in revision
    assert "users.restore" in revision
    assert "DROP TABLE" not in revision
    assert "def downgrade()" in revision


def test_modern_workspace_site_lifecycle_migration_preserves_history() -> None:
    root = Path(__file__).resolve().parents[1]
    revision = (
        root / "alembic" / "versions" / "20260724_0012_modern_workspaces_site_lifecycle.py"
    ).read_text()
    upgrade = revision.split("def downgrade()", maxsplit=1)[0]
    assert 'down_revision = "20260724_0011"' in revision
    assert '"device_site_assignments"' in upgrade
    assert '"utility_account_site_assignments"' in upgrade
    assert '"lifecycle_state"' in upgrade
    assert '"sites.set_default"' in upgrade
    assert '"sites.transfer_resources"' in upgrade
    assert "INSERT INTO status_layout_revisions" in upgrade
    assert "WHEN 'global_header_left' THEN 'top_bar'" in upgrade
    assert "WHEN 'page_header_primary' THEN 'workspace_header'" in upgrade
    assert "WHEN 'mobile_status_strip' THEN 'mobile_status_drawer'" in upgrade
    assert "THEN 'administration_diagnostics'" in upgrade
    assert "'raw_readings_rewritten', false" in upgrade
    assert "UPDATE raw_readings" not in upgrade
    assert "DELETE FROM status_layout_revisions" not in upgrade
    assert "DROP TABLE" not in upgrade
    assert "def downgrade()" in revision
