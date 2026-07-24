"""Add modern workspace site lifecycle, transfers, and granular permissions.

Revision ID: 20260724_0012
Revises: 20260724_0011
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260724_0012"
down_revision = "20260724_0011"
branch_labels = None
depends_on = None

SITE_PERMISSIONS = (
    (
        "sites.create",
        "Create sites",
        "Create a physical site and its initial network-policy boundary.",
    ),
    (
        "sites.edit",
        "Edit sites",
        "Change assigned-site identity, locale, timezone, and policy assignment.",
    ),
    (
        "sites.set_default",
        "Set default site",
        "Transactionally change the active default site.",
    ),
    (
        "sites.disable",
        "Disable and enable sites",
        "Temporarily suspend ordinary access and new assignments for a site.",
    ),
    (
        "sites.remove",
        "Remove sites",
        "Soft-remove a site after reviewing and resolving active dependencies.",
    ),
    (
        "sites.restore",
        "Restore sites",
        "Restore a removed site to a disabled state for explicit review.",
    ),
    (
        "sites.transfer_resources",
        "Transfer site resources",
        "Transfer or archive active site resources before removal.",
    ),
    (
        "sites.view_audit",
        "View site audit history",
        "View lifecycle and configuration audit evidence for assigned sites.",
    ),
)


def upgrade() -> None:
    op.add_column("sites", sa.Column("code", sa.String(80)))
    op.add_column("sites", sa.Column("description", sa.Text()))
    op.add_column("sites", sa.Column("location_label", sa.String(160)))
    op.add_column("sites", sa.Column("organization", sa.String(160)))
    op.add_column(
        "sites",
        sa.Column("currency", sa.String(3), nullable=False, server_default="USD"),
    )
    op.add_column(
        "sites",
        sa.Column("locale", sa.String(32), nullable=False, server_default="en-US"),
    )
    op.add_column(
        "sites",
        sa.Column("unit_system", sa.String(16), nullable=False, server_default="imperial"),
    )
    op.add_column(
        "sites",
        sa.Column("lifecycle_state", sa.String(16), nullable=False, server_default="active"),
    )
    op.add_column(
        "sites",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("sites", sa.Column("disabled_at", sa.DateTime(timezone=True)))
    op.add_column(
        "sites",
        sa.Column(
            "disabled_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("sites", sa.Column("removed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "sites",
        sa.Column(
            "removed_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("sites", sa.Column("removal_reason", sa.String(500)))
    op.add_column("sites", sa.Column("restored_at", sa.DateTime(timezone=True)))
    op.add_column(
        "sites",
        sa.Column(
            "restored_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("sites", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.execute(
        """
        UPDATE sites
        SET code =
            COALESCE(
                NULLIF(
                    trim(BOTH '-' FROM regexp_replace(lower(name), '[^a-z0-9]+', '-', 'g')),
                    ''
                ),
                'site'
            ) || '-' || substring(replace(id, '-', '') FROM 1 FOR 8)
        """
    )
    op.alter_column("sites", "code", nullable=False)
    op.execute(
        """
        UPDATE sites
        SET is_default = true
        WHERE id = (
            SELECT id FROM sites
            WHERE lifecycle_state = 'active'
            ORDER BY created_at, id
            LIMIT 1
        )
        """
    )
    op.create_check_constraint(
        "site_lifecycle_state",
        "sites",
        "lifecycle_state IN ('active','disabled','removed')",
    )
    op.create_check_constraint("site_currency", "sites", "length(currency) = 3")
    op.create_check_constraint("site_unit_system", "sites", "unit_system IN ('imperial','metric')")
    op.create_check_constraint("site_revision_positive", "sites", "revision > 0")
    op.create_index("ix_sites_code", "sites", ["code"], unique=True)
    op.create_index("ix_sites_lifecycle_state", "sites", ["lifecycle_state"])
    op.create_index("ix_sites_is_default", "sites", ["is_default"])
    op.create_index("ix_sites_disabled_at", "sites", ["disabled_at"])
    op.create_index("ix_sites_removed_at", "sites", ["removed_at"])
    op.create_index("ix_sites_disabled_by", "sites", ["disabled_by"])
    op.create_index("ix_sites_removed_by", "sites", ["removed_by"])
    op.create_index(
        "uq_sites_single_active_default",
        "sites",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default = true AND lifecycle_state = 'active'"),
    )

    op.create_table(
        "device_site_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(36),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            sa.String(36),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column(
            "assigned_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="device_site_assignment_window",
        ),
    )
    op.create_index(
        "ix_device_site_assignments_device_id",
        "device_site_assignments",
        ["device_id"],
    )
    op.create_index("ix_device_site_assignments_site_id", "device_site_assignments", ["site_id"])
    op.create_index(
        "ix_device_site_assignments_effective_from",
        "device_site_assignments",
        ["effective_from"],
    )
    op.create_index(
        "ix_device_site_assignments_effective_to",
        "device_site_assignments",
        ["effective_to"],
    )
    op.create_index(
        "uq_device_site_assignment_open",
        "device_site_assignments",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.execute(
        """
        INSERT INTO device_site_assignments
            (id, device_id, site_id, effective_from, effective_to,
             assigned_by, reason, created_at)
        SELECT
            id,
            id,
            site_id,
            created_at,
            NULL,
            NULL,
            'System migration: existing device site',
            CURRENT_TIMESTAMP
        FROM devices
        """
    )

    op.create_table(
        "utility_account_site_assignments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            sa.String(36),
            sa.ForeignKey("sites.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column(
            "assigned_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("reason", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="utility_account_site_assignment_window",
        ),
    )
    op.create_index(
        "ix_utility_account_site_assignments_utility_account_id",
        "utility_account_site_assignments",
        ["utility_account_id"],
    )
    op.create_index(
        "ix_utility_account_site_assignments_site_id",
        "utility_account_site_assignments",
        ["site_id"],
    )
    op.create_index(
        "ix_utility_account_site_assignments_effective_from",
        "utility_account_site_assignments",
        ["effective_from"],
    )
    op.create_index(
        "ix_utility_account_site_assignments_effective_to",
        "utility_account_site_assignments",
        ["effective_to"],
    )
    op.create_index(
        "uq_utility_account_site_assignment_open",
        "utility_account_site_assignments",
        ["utility_account_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    op.execute(
        """
        INSERT INTO utility_account_site_assignments
            (id, utility_account_id, site_id, effective_from, effective_to,
             assigned_by, reason, created_at)
        SELECT
            id,
            id,
            site_id,
            created_at,
            NULL,
            NULL,
            'System migration: existing utility-account site',
            CURRENT_TIMESTAMP
        FROM utility_accounts
        """
    )

    for code, label, description in SITE_PERMISSIONS:
        op.execute(
            sa.text(
                "INSERT INTO permissions "
                "(code, group_name, label, description, high_risk) "
                "VALUES (:code, 'Sites and devices', :label, :description, true)"
            ).bindparams(code=code, label=label, description=description)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO role_permissions (role_name, permission_code)
                SELECT DISTINCT role_name, :code
                FROM role_permissions
                WHERE permission_code = 'sites.manage'
                ON CONFLICT DO NOTHING
                """
            ).bindparams(code=code)
        )
        op.execute(
            sa.text(
                """
                INSERT INTO role_permissions (role_name, permission_code)
                VALUES ('admin', :code)
                ON CONFLICT DO NOTHING
                """
            ).bindparams(code=code)
        )

    # Published user revisions remain immutable. This system revision relocates only
    # shell placement zones and leaves every unrelated visibility/density choice intact.
    op.execute(
        """
        INSERT INTO status_layout_revisions
            (id, revision, registry_version, configuration, created_by, created_at,
             reason, restored_from_id)
        SELECT
            '00000000-0000-4000-8000-000000000012',
            state.current_revision + 1,
            current.registry_version,
            jsonb_set(
                current.configuration::jsonb,
                '{items}',
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            item || jsonb_build_object(
                                'zone',
                                CASE item->>'zone'
                                    WHEN 'global_header_left' THEN 'top_bar'
                                    WHEN 'global_header_center' THEN 'top_bar'
                                    WHEN 'global_header_right' THEN 'top_bar'
                                    WHEN 'sidebar_upper' THEN 'mobile_status_drawer'
                                    WHEN 'sidebar_lower' THEN 'mobile_status_drawer'
                                    WHEN 'global_footer' THEN 'page_summary'
                                    WHEN 'page_header_primary' THEN 'workspace_header'
                                    WHEN 'page_header_secondary' THEN 'workspace_header'
                                    WHEN 'page_status_row' THEN 'page_summary'
                                    WHEN 'page_summary_strip' THEN 'page_summary'
                                    WHEN 'page_footer' THEN 'page_summary'
                                    WHEN 'overview_site_state' THEN 'overview_summary'
                                    WHEN 'overview_site_summary' THEN 'overview_summary'
                                    WHEN 'history_context' THEN 'page_summary'
                                    WHEN 'diagnostics_summary'
                                        THEN 'administration_diagnostics'
                                    WHEN 'mobile_header' THEN 'mobile_status_drawer'
                                    WHEN 'mobile_status_strip' THEN 'mobile_status_drawer'
                                    ELSE item->>'zone'
                                END
                            )
                        )
                        FROM jsonb_array_elements(
                            COALESCE(current.configuration::jsonb->'items', '[]'::jsonb)
                        ) AS item
                    ),
                    '[]'::jsonb
                ),
                true
            )::json,
            NULL,
            CURRENT_TIMESTAMP,
            'System migration: six-workspace shell and semantic status zones',
            state.current_revision_id
        FROM status_layout_state AS state
        JOIN status_layout_revisions AS current ON current.id = state.current_revision_id
        WHERE state.id = 'current'
        """
    )
    op.execute(
        """
        UPDATE status_layout_state
        SET current_revision_id = '00000000-0000-4000-8000-000000000012',
            current_revision = current_revision + 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = 'current'
          AND EXISTS (
              SELECT 1 FROM status_layout_revisions
              WHERE id = '00000000-0000-4000-8000-000000000012'
          )
        """
    )
    op.execute(
        """
        INSERT INTO audit_events
            (id, occurred_at, actor_type, actor_id, action, object_type, object_id,
             source_ip, outcome, correlation_id, details)
        VALUES
            ('00000000-0000-4000-9000-000000000012', CURRENT_TIMESTAMP, 'system', NULL,
             'site_lifecycle.modern_workspace_migrated', 'status_layout',
             '00000000-0000-4000-8000-000000000012', NULL, 'success',
             'migration:20260724_0012',
             json_build_object(
                 'summary', 'Added site lifecycle and six-workspace semantic zones',
                 'previous_revision_preserved', true,
                 'existing_sites_preserved', true,
                 'raw_readings_rewritten', false
             ))
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM audit_events WHERE id = '00000000-0000-4000-9000-000000000012'")
    op.execute(
        """
        UPDATE status_layout_state AS state
        SET current_revision_id = migrated.restored_from_id,
            current_revision = previous.revision,
            updated_at = CURRENT_TIMESTAMP
        FROM status_layout_revisions AS migrated
        JOIN status_layout_revisions AS previous
          ON previous.id = migrated.restored_from_id
        WHERE state.id = 'current'
          AND state.current_revision_id = '00000000-0000-4000-8000-000000000012'
          AND migrated.id = '00000000-0000-4000-8000-000000000012'
        """
    )
    op.execute(
        """
        DELETE FROM status_layout_revisions
        WHERE id = '00000000-0000-4000-8000-000000000012'
          AND NOT EXISTS (
              SELECT 1 FROM status_layout_state
              WHERE current_revision_id = '00000000-0000-4000-8000-000000000012'
          )
        """
    )
    for code, _label, _description in SITE_PERMISSIONS:
        op.execute(
            sa.text(
                "DELETE FROM role_permissions WHERE permission_code = :permission_code"
            ).bindparams(permission_code=code)
        )
        op.execute(
            sa.text("DELETE FROM permissions WHERE code = :permission_code").bindparams(
                permission_code=code
            )
        )

    op.drop_index(
        "uq_utility_account_site_assignment_open",
        table_name="utility_account_site_assignments",
    )
    op.drop_index(
        "ix_utility_account_site_assignments_effective_to",
        table_name="utility_account_site_assignments",
    )
    op.drop_index(
        "ix_utility_account_site_assignments_effective_from",
        table_name="utility_account_site_assignments",
    )
    op.drop_index(
        "ix_utility_account_site_assignments_site_id",
        table_name="utility_account_site_assignments",
    )
    op.drop_index(
        "ix_utility_account_site_assignments_utility_account_id",
        table_name="utility_account_site_assignments",
    )
    op.drop_table("utility_account_site_assignments")
    op.drop_index("uq_device_site_assignment_open", table_name="device_site_assignments")
    op.drop_index("ix_device_site_assignments_effective_to", table_name="device_site_assignments")
    op.drop_index("ix_device_site_assignments_effective_from", table_name="device_site_assignments")
    op.drop_index("ix_device_site_assignments_site_id", table_name="device_site_assignments")
    op.drop_index("ix_device_site_assignments_device_id", table_name="device_site_assignments")
    op.drop_table("device_site_assignments")
    op.drop_index("uq_sites_single_active_default", table_name="sites")
    op.drop_index("ix_sites_removed_by", table_name="sites")
    op.drop_index("ix_sites_disabled_by", table_name="sites")
    op.drop_index("ix_sites_removed_at", table_name="sites")
    op.drop_index("ix_sites_disabled_at", table_name="sites")
    op.drop_index("ix_sites_is_default", table_name="sites")
    op.drop_index("ix_sites_lifecycle_state", table_name="sites")
    op.drop_index("ix_sites_code", table_name="sites")
    op.drop_constraint("site_revision_positive", "sites", type_="check")
    op.drop_constraint("site_unit_system", "sites", type_="check")
    op.drop_constraint("site_currency", "sites", type_="check")
    op.drop_constraint("site_lifecycle_state", "sites", type_="check")
    op.drop_column("sites", "revision")
    op.drop_column("sites", "restored_by")
    op.drop_column("sites", "restored_at")
    op.drop_column("sites", "removal_reason")
    op.drop_column("sites", "removed_by")
    op.drop_column("sites", "removed_at")
    op.drop_column("sites", "disabled_by")
    op.drop_column("sites", "disabled_at")
    op.drop_column("sites", "is_default")
    op.drop_column("sites", "lifecycle_state")
    op.drop_column("sites", "unit_system")
    op.drop_column("sites", "locale")
    op.drop_column("sites", "currency")
    op.drop_column("sites", "organization")
    op.drop_column("sites", "location_label")
    op.drop_column("sites", "description")
    op.drop_column("sites", "code")
