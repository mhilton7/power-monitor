"""Add guided utility accounts and explicit sensor network policies.

Revision ID: 20260721_0008
Revises: 20260721_0007
"""
# ruff: noqa: S608 -- migration SQL interpolates only fixed expressions defined in this file.

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260721_0008"
down_revision = "20260721_0007"
branch_labels = None
depends_on = None


def _stable_id(expression: str) -> str:
    digest = f"md5({expression})"
    return (
        f"substr({digest},1,8)||'-'||substr({digest},9,4)||'-4'||substr({digest},14,3)||"
        f"'-8'||substr({digest},18,3)||'-'||substr({digest},21,12)"
    )


def upgrade() -> None:
    op.add_column("utility_accounts", sa.Column("nickname", sa.String(160)))
    op.add_column("utility_accounts", sa.Column("account_number_suffix", sa.String(8)))
    op.add_column(
        "utility_accounts",
        sa.Column("status", sa.String(24), nullable=False, server_default="active"),
    )
    op.add_column("utility_accounts", sa.Column("service_class", sa.String(80)))
    op.add_column("utility_accounts", sa.Column("allocation_method", sa.String(80)))
    op.add_column(
        "utility_accounts",
        sa.Column("full_account_override", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "utility_accounts",
        sa.Column("adjustment_config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "utility_accounts",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("utility_accounts", sa.Column("archived_at", sa.DateTime(timezone=True)))
    op.add_column("utility_accounts", sa.Column("archived_by", sa.String(36)))
    op.create_foreign_key(
        "fk_utility_accounts_archived_by_users",
        "utility_accounts",
        "users",
        ["archived_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_utility_accounts_status", "utility_accounts", ["status"])
    op.create_index("ix_utility_accounts_archived_at", "utility_accounts", ["archived_at"])
    op.create_check_constraint(
        "utility_account_status", "utility_accounts", "status IN ('active','archived')"
    )
    op.create_check_constraint(
        "utility_account_cost_scope",
        "utility_accounts",
        "cost_scope_default IN "
        "('energy_only','allocated_account_estimate','full_account_estimate')",
    )
    op.add_column("rate_assignments", sa.Column("assignment_reason", sa.String(500)))
    op.create_check_constraint(
        "rate_assignment_effective_window",
        "rate_assignments",
        "effective_to IS NULL OR effective_to > effective_from",
    )
    op.create_index(
        "ix_rate_assignments_account_window",
        "rate_assignments",
        ["utility_account_id", "effective_from", "effective_to"],
    )

    op.create_table(
        "utility_account_adjustments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "utility_account_id",
            sa.String(36),
            sa.ForeignKey("utility_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("component", sa.String(48), nullable=False),
        sa.Column("value", sa.Numeric(18, 8), nullable=False),
        sa.Column("unit", sa.String(24), nullable=False),
        sa.Column("provenance", sa.String(240), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "component IN ('cca_generation','direct_access','baseline_credit',"
            "'service_charge','tax_fee','custom_fixed','custom_per_kwh')",
            name="utility_adjustment_component",
        ),
        sa.CheckConstraint(
            "unit IN ('per_kwh','fixed','percent','included')", name="adjustment_unit"
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="adjustment_effective_window",
        ),
    )
    op.create_index(
        "ix_utility_account_adjustments_utility_account_id",
        "utility_account_adjustments",
        ["utility_account_id"],
    )
    op.create_index(
        "ix_utility_account_adjustments_effective_from",
        "utility_account_adjustments",
        ["effective_from"],
    )

    op.create_table(
        "sensor_network_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "site_id", sa.String(36), sa.ForeignKey("sites.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("direction", sa.String(24), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "migration_notice_pending", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("migrated_from_legacy", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("site_id", "direction", name="uq_sensor_policy_site_direction"),
        sa.CheckConstraint(
            "direction IN ('device_ingress','server_pull')", name="sensor_policy_direction"
        ),
        sa.CheckConstraint(
            "mode IN ('allow_listed_private','allow_all_private','deny_all',"
            "'legacy_authenticated_any','legacy_public_and_listed')",
            name="sensor_policy_mode",
        ),
    )
    op.create_index("ix_sensor_network_policies_site_id", "sensor_network_policies", ["site_id"])

    op.create_table(
        "sensor_network_cidrs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_id",
            sa.String(36),
            sa.ForeignKey("sensor_network_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("network", sa.String(80), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("policy_id", "network", name="uq_policy_network"),
    )
    op.create_index("ix_sensor_network_cidrs_policy_id", "sensor_network_cidrs", ["policy_id"])

    op.create_table(
        "network_policy_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "policy_id",
            sa.String(36),
            sa.ForeignKey("sensor_network_policies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(40), nullable=False),
        sa.Column("cidrs", sa.JSON(), nullable=False),
        sa.Column("changed_by", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.UniqueConstraint("policy_id", "revision", name="uq_policy_revision"),
    )
    op.create_index(
        "ix_network_policy_revisions_policy_id", "network_policy_revisions", ["policy_id"]
    )

    ingress_id = _stable_id("sites.id || chr(58) || 'device_ingress'")
    pull_id = _stable_id("sites.id || chr(58) || 'server_pull'")
    op.execute(
        f"""
        INSERT INTO sensor_network_policies
            (id, site_id, direction, mode, revision, migration_notice_pending,
             migrated_from_legacy, created_at, updated_at)
        SELECT {ingress_id}, sites.id, 'device_ingress', 'legacy_authenticated_any', 1,
               true, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM sites
        """
    )
    op.execute(
        f"""
        INSERT INTO sensor_network_policies
            (id, site_id, direction, mode, revision, migration_notice_pending,
             migrated_from_legacy, created_at, updated_at)
        SELECT {pull_id}, sites.id, 'server_pull',
               CASE
                   WHEN sites.allow_public_polling THEN 'legacy_public_and_listed'
                   WHEN json_array_length(sites.allowed_cidrs) > 0 THEN 'allow_listed_private'
                   ELSE 'deny_all'
               END,
               1, true, true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM sites
        """
    )
    cidr_id = _stable_id("policy.id || ':' || cidr.value")
    op.execute(
        f"""
        INSERT INTO sensor_network_cidrs
            (id, policy_id, network, label, enabled, revision, created_at, updated_at)
        SELECT {cidr_id}, policy.id, cidr.value, 'Migrated site CIDR', true, 1,
               CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        FROM sites
        JOIN sensor_network_policies AS policy
          ON policy.site_id = sites.id AND policy.direction = 'server_pull'
        CROSS JOIN LATERAL json_array_elements_text(sites.allowed_cidrs) AS cidr(value)
        """
    )
    op.execute(
        """
        INSERT INTO network_policy_revisions
            (id, policy_id, revision, mode, cidrs, changed_by, changed_at, reason)
        SELECT policy.id, policy.id, 1, policy.mode, COALESCE(
            (SELECT json_agg(json_build_object('network', cidr.network, 'label', cidr.label,
                                               'enabled', cidr.enabled))
             FROM sensor_network_cidrs AS cidr WHERE cidr.policy_id = policy.id),
            '[]'::json
        ), NULL, CURRENT_TIMESTAMP,
        'System migration preserved the previously effective network behavior.'
        FROM sensor_network_policies AS policy
        """
    )

    network_alert_id = _stable_id("'device_address_outside_policy'")
    op.execute(
        f"""
        INSERT INTO alert_rules
            (id, name, rule_type, severity, enabled, site_id, device_id,
             debounce_seconds, resolve_seconds, configuration, created_at, updated_at)
        SELECT {network_alert_id}, 'Device address outside server-pull policy',
               'device_address_outside_policy', 'warning', true, NULL, NULL,
               0, 0, '{{}}'::json, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        WHERE NOT EXISTS (
            SELECT 1 FROM alert_rules
            WHERE rule_type = 'device_address_outside_policy'
              AND site_id IS NULL AND device_id IS NULL
        )
        """
    )

    permissions = (
        (
            "utility_accounts.view",
            "View utility accounts",
            "View assigned-site utility accounts.",
            False,
        ),
        (
            "utility_accounts.manage",
            "Manage utility accounts",
            "Create, revise, and archive utility accounts.",
            True,
        ),
        (
            "network.view",
            "View sensor network policy",
            "View assigned-site network policy and observed addresses.",
            False,
        ),
        (
            "network.manage",
            "Manage sensor network policy",
            "Change sensor network policies and CIDRs.",
            True,
        ),
    )
    for code, label, description, high_risk in permissions:
        op.execute(
            sa.text(
                "INSERT INTO permissions (code, group_name, label, description, high_risk) "
                "VALUES (:code, :group_name, :label, :description, :high_risk)"
            ).bindparams(
                code=code,
                group_name="Sites and devices",
                label=label,
                description=description,
                high_risk=high_risk,
            )
        )
        op.execute(
            sa.text(
                "INSERT INTO role_permissions (role_name, permission_code) VALUES ('admin', :code)"
            ).bindparams(code=code)
        )

    op.execute(
        """
        INSERT INTO audit_events
            (id, occurred_at, actor_type, actor_id, action, object_type, object_id,
             source_ip, outcome, correlation_id, details)
        SELECT policy.id, CURRENT_TIMESTAMP, 'system', NULL,
               'network_policy.legacy_behavior_migrated', 'sensor_network_policy', policy.id,
               NULL, 'success', 'migration:20260721_0008',
               json_build_object('direction', policy.direction, 'mode', policy.mode,
                                 'behavior_preserved', true, 'review_required', true)
        FROM sensor_network_policies AS policy
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM alert_rules WHERE id = "
        + _stable_id("'device_address_outside_policy'")
        + " AND rule_type = 'device_address_outside_policy'"
    )
    op.execute("DELETE FROM audit_events WHERE correlation_id = 'migration:20260721_0008'")
    op.execute(
        "DELETE FROM role_permissions WHERE permission_code IN "
        "('utility_accounts.view','utility_accounts.manage','network.view','network.manage')"
    )
    op.execute(
        "DELETE FROM permissions WHERE code IN "
        "('utility_accounts.view','utility_accounts.manage','network.view','network.manage')"
    )
    op.drop_index("ix_network_policy_revisions_policy_id", table_name="network_policy_revisions")
    op.drop_table("network_policy_revisions")
    op.drop_index("ix_sensor_network_cidrs_policy_id", table_name="sensor_network_cidrs")
    op.drop_table("sensor_network_cidrs")
    op.drop_index("ix_sensor_network_policies_site_id", table_name="sensor_network_policies")
    op.drop_table("sensor_network_policies")
    op.drop_index(
        "ix_utility_account_adjustments_effective_from",
        table_name="utility_account_adjustments",
    )
    op.drop_index(
        "ix_utility_account_adjustments_utility_account_id",
        table_name="utility_account_adjustments",
    )
    op.drop_table("utility_account_adjustments")
    op.drop_index("ix_rate_assignments_account_window", table_name="rate_assignments")
    op.drop_constraint(
        op.f("ck_rate_assignments_rate_assignment_effective_window"),
        "rate_assignments",
        type_="check",
    )
    op.drop_column("rate_assignments", "assignment_reason")
    op.drop_constraint(
        op.f("ck_utility_accounts_utility_account_cost_scope"),
        "utility_accounts",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_utility_accounts_utility_account_status"),
        "utility_accounts",
        type_="check",
    )
    op.drop_index("ix_utility_accounts_archived_at", table_name="utility_accounts")
    op.drop_index("ix_utility_accounts_status", table_name="utility_accounts")
    op.drop_constraint(
        "fk_utility_accounts_archived_by_users", "utility_accounts", type_="foreignkey"
    )
    for column in (
        "archived_by",
        "archived_at",
        "revision",
        "adjustment_config",
        "full_account_override",
        "allocation_method",
        "service_class",
        "status",
        "account_number_suffix",
        "nickname",
    ):
        op.drop_column("utility_accounts", column)
