from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


def new_uuid() -> str:
    return str(uuid.uuid4())


def default_site_code(context: Any) -> str:
    name = str(context.get_current_parameters().get("name") or "site").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:60] or "site"
    return f"{slug}-{uuid.uuid4().hex[:8]}"


class Role(Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    __table_args__ = (Index("uq_roles_display_name_lower", func.lower(display_name), unique=True),)


class Permission(Base):
    __tablename__ = "permissions"
    code: Mapped[str] = mapped_column(String(80), primary_key=True)
    group_name: Mapped[str] = mapped_column(String(80), index=True)
    label: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(500))
    high_risk: Mapped[bool] = mapped_column(Boolean, default=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"
    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permissions.code", ondelete="RESTRICT"), primary_key=True
    )


class RoleRevision(Base):
    __tablename__ = "role_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="RESTRICT"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String(255))
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (UniqueConstraint("role_name", "revision", name="uq_role_revision"),)


class User(TimestampMixin, Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    lifecycle_state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    is_protected: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    all_sites: Mapped[bool] = mapped_column(Boolean, default=True)
    access_revision: Mapped[int] = mapped_column(Integer, default=1)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    removed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removal_reason: Mapped[str | None] = mapped_column(String(500))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removed_role_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    removed_site_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    removed_all_sites: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state IN ('active','disabled','removed')",
            name="user_lifecycle_state",
        ),
    )


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_name: Mapped[str] = mapped_column(
        ForeignKey("roles.name", ondelete="RESTRICT"), primary_key=True
    )


class UserSite(Base):
    __tablename__ = "user_sites"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    site_id: Mapped[str] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), primary_key=True
    )


class BrowserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    reauthenticated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TotpCredential(TimestampMixin, Base):
    __tablename__ = "totp_credentials"
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_counter: Mapped[int | None] = mapped_column(Integer)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_type: Mapped[str] = mapped_column(String(24))
    actor_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    object_type: Mapped[str | None] = mapped_column(String(80))
    object_id: Mapped[str | None] = mapped_column(String(80))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(24), default="success")
    correlation_id: Mapped[str | None] = mapped_column(String(128), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class InterfaceTextRevision(Base):
    __tablename__ = "interface_text_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    revision: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    values: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(500))
    restored_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("interface_text_revisions.id", ondelete="SET NULL")
    )


class InterfaceTextDraft(Base):
    __tablename__ = "interface_text_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="current")
    base_revision: Mapped[int] = mapped_column(Integer, default=0)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    previewed_revision: Mapped[int | None] = mapped_column(Integer)
    values: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    edited_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class InterfaceTextState(Base):
    __tablename__ = "interface_text_state"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="current")
    current_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("interface_text_revisions.id", ondelete="RESTRICT")
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StatusLayoutRevision(Base):
    __tablename__ = "status_layout_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    revision: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    registry_version: Mapped[str] = mapped_column(String(64))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reason: Mapped[str | None] = mapped_column(String(500))
    restored_from_id: Mapped[str | None] = mapped_column(
        ForeignKey("status_layout_revisions.id", ondelete="SET NULL")
    )


class StatusLayoutDraft(Base):
    __tablename__ = "status_layout_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="current")
    base_revision: Mapped[int] = mapped_column(Integer, default=1)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    previewed_revision: Mapped[int | None] = mapped_column(Integer)
    registry_version: Mapped[str] = mapped_column(String(64))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    edited_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class StatusLayoutState(Base):
    __tablename__ = "status_layout_state"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="current")
    current_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey("status_layout_revisions.id", ondelete="RESTRICT")
    )
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Site(TimestampMixin, Base):
    __tablename__ = "sites"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    code: Mapped[str] = mapped_column(
        String(80), unique=True, index=True, default=default_site_code
    )
    description: Mapped[str | None] = mapped_column(Text)
    location_label: Mapped[str | None] = mapped_column(String(160))
    organization: Mapped[str | None] = mapped_column(String(160))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    locale: Mapped[str] = mapped_column(String(32), default="en-US")
    unit_system: Mapped[str] = mapped_column(String(16), default="imperial")
    allowed_cidrs: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_domains: Mapped[list[str]] = mapped_column(JSON, default=list)
    allow_public_polling: Mapped[bool] = mapped_column(Boolean, default=False)
    lifecycle_state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    disabled_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    removed_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    removal_reason: Mapped[str | None] = mapped_column(String(500))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (
        CheckConstraint(
            "lifecycle_state IN ('active','disabled','removed')",
            name="site_lifecycle_state",
        ),
        CheckConstraint("length(currency) = 3", name="site_currency"),
        CheckConstraint("unit_system IN ('imperial','metric')", name="site_unit_system"),
        CheckConstraint("revision > 0", name="site_revision_positive"),
    )


class Utility(TimestampMixin, Base):
    __tablename__ = "utilities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    website: Mapped[str | None] = mapped_column(String(500))


class UtilityAccount(TimestampMixin, Base):
    __tablename__ = "utility_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), index=True)
    utility_id: Mapped[str] = mapped_column(
        ForeignKey("utilities.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    nickname: Mapped[str | None] = mapped_column(String(160))
    account_number_suffix: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    service_class: Mapped[str | None] = mapped_column(String(80))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    billing_cycle_start_day: Mapped[int] = mapped_column(Integer, default=1)
    baseline_allocation_kwh: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    generation_provider: Mapped[str] = mapped_column(String(32), default="sce")
    provider_mode: Mapped[str] = mapped_column(String(32), default="sce_bundled")
    cost_scope_default: Mapped[str] = mapped_column(String(40), default="energy_only")
    allocation_method: Mapped[str | None] = mapped_column(String(80))
    full_account_override: Mapped[bool] = mapped_column(Boolean, default=False)
    adjustment_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    active_rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="SET NULL", use_alter=True)
    )
    __table_args__ = (
        CheckConstraint(
            "billing_cycle_start_day >= 1 AND billing_cycle_start_day <= 31",
            name="billing_cycle_start_day",
        ),
        CheckConstraint("status IN ('active','archived')", name="utility_account_status"),
        CheckConstraint(
            "cost_scope_default IN "
            "('energy_only','allocated_account_estimate','full_account_estimate')",
            name="utility_account_cost_scope",
        ),
    )


class UtilityAccountSiteAssignment(Base):
    __tablename__ = "utility_account_site_assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="utility_account_site_assignment_window",
        ),
        Index(
            "uq_utility_account_site_assignment_open",
            "utility_account_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
            sqlite_where=text("effective_to IS NULL"),
        ),
    )


class UtilityAccountAdjustment(Base):
    __tablename__ = "utility_account_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    component: Mapped[str] = mapped_column(String(48))
    value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    unit: Mapped[str] = mapped_column(String(24))
    provenance: Mapped[str] = mapped_column(String(240))
    reason: Mapped[str | None] = mapped_column(String(500))
    evidence_reference: Mapped[str | None] = mapped_column(String(500))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "component IN ('cca_generation','direct_access','baseline_credit',"
            "'service_charge','tax_fee','custom_fixed','custom_per_kwh')",
            name="utility_adjustment_component",
        ),
        CheckConstraint("unit IN ('per_kwh','fixed','percent','included')", name="adjustment_unit"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="adjustment_effective_window",
        ),
        CheckConstraint(
            "status IN ('active','removed')",
            name="utility_adjustment_status",
        ),
        CheckConstraint("revision > 0", name="utility_adjustment_revision"),
    )


class SensorNetworkPolicy(TimestampMixin, Base):
    __tablename__ = "sensor_network_policies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(24))
    mode: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    migration_notice_pending: Mapped[bool] = mapped_column(Boolean, default=False)
    migrated_from_legacy: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (
        UniqueConstraint("site_id", "direction", name="uq_sensor_policy_site_direction"),
        CheckConstraint(
            "direction IN ('device_ingress','server_pull')", name="sensor_policy_direction"
        ),
        CheckConstraint(
            "mode IN ('allow_listed_private','allow_all_private','deny_all',"
            "'legacy_authenticated_any','legacy_public_and_listed')",
            name="sensor_policy_mode",
        ),
    )


class SensorNetworkCidr(TimestampMixin, Base):
    __tablename__ = "sensor_network_cidrs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("sensor_network_policies.id", ondelete="CASCADE"), index=True
    )
    network: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    __table_args__ = (UniqueConstraint("policy_id", "network", name="uq_policy_network"),)


class NetworkPolicyRevision(Base):
    __tablename__ = "network_policy_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    policy_id: Mapped[str] = mapped_column(
        ForeignKey("sensor_network_policies.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(40))
    cidrs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    changed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (UniqueConstraint("policy_id", "revision", name="uq_policy_revision"),)


class Circuit(TimestampMixin, Base):
    __tablename__ = "circuits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("circuits.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(160))
    measurement_role: Mapped[str] = mapped_column(String(32), default="branch")
    split_phase_group: Mapped[str | None] = mapped_column(String(80))
    __table_args__ = (
        UniqueConstraint("site_id", "name", name="uq_circuit_site_name"),
        CheckConstraint(
            "measurement_role IN ('main','service-leg','branch','submeter','informational')",
            name="measurement_role",
        ),
    )


class AggregateSet(TimestampMixin, Base):
    __tablename__ = "aggregate_sets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), index=True)
    utility_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(160))
    cost_scope: Mapped[str] = mapped_column(String(32), default="energy_only")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    overlap_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "cost_scope IN ('energy_only','allocated_account','full_account')", name="cost_scope"
        ),
    )


class AggregateMember(Base):
    __tablename__ = "aggregate_members"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    aggregate_set_id: Mapped[str] = mapped_column(
        ForeignKey("aggregate_sets.id", ondelete="CASCADE"), index=True
    )
    circuit_id: Mapped[str | None] = mapped_column(ForeignKey("circuits.id", ondelete="RESTRICT"))
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT", use_alter=True)
    )
    allocation_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal("100"))
    __table_args__ = (
        CheckConstraint(
            "(circuit_id IS NOT NULL) <> (device_id IS NOT NULL)", name="single_member_target"
        ),
        CheckConstraint(
            "allocation_percent > 0 AND allocation_percent <= 100", name="allocation_percent"
        ),
    )


class Device(TimestampMixin, Base):
    __tablename__ = "devices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), index=True)
    utility_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="SET NULL")
    )
    circuit_id: Mapped[str | None] = mapped_column(ForeignKey("circuits.id", ondelete="SET NULL"))
    hardware_id: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    connection_mode: Mapped[str] = mapped_column(String(16), default="push")
    measurement_role: Mapped[str] = mapped_column(String(32), default="submeter")
    cost_scope: Mapped[str] = mapped_column(String(32), default="energy_only")
    include_in_default_site_total: Mapped[bool] = mapped_column(Boolean, default=False)
    ct_rating_amps: Mapped[Decimal] = mapped_column(Numeric(8, 3), default=Decimal("100"))
    protocol_version: Mapped[str] = mapped_column(String(40), default="pm-protocol/1.0.0")
    firmware_version: Mapped[str | None] = mapped_column(String(80))
    firmware_build_hash: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(48), default="offline_last_known", index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    lifecycle_generation: Mapped[int] = mapped_column(Integer, default=0)
    decommissioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    decommissioned_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    decommission_reason: Mapped[str | None] = mapped_column(String(64))
    maintenance_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    desired_config_version: Mapped[int] = mapped_column(Integer, default=1)
    effective_config_version: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("connection_mode IN ('pull','push','hybrid')", name="connection_mode"),
        CheckConstraint(
            "cost_scope IN ('energy_only','allocated_account','full_account')", name="cost_scope"
        ),
        CheckConstraint("ct_rating_amps > 0", name="ct_rating"),
    )


class DeviceSiteAssignment(Base):
    __tablename__ = "device_site_assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), index=True
    )
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"), index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="device_site_assignment_window",
        ),
        Index(
            "uq_device_site_assignment_open",
            "device_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
            sqlite_where=text("effective_to IS NULL"),
        ),
    )


class DeviceLifecycleEvent(Base):
    __tablename__ = "device_lifecycle_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), index=True
    )
    generation: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str | None] = mapped_column(String(64))
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    circuit_id: Mapped[str | None] = mapped_column(ForeignKey("circuits.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (
        UniqueConstraint(
            "device_id",
            "generation",
            "event_type",
            name="uq_device_lifecycle_generation_event",
        ),
    )


class DeviceCredential(Base):
    __tablename__ = "device_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    encrypted_secret: Mapped[bytes] = mapped_column(LargeBinary)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceAddress(Base):
    __tablename__ = "device_addresses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=443)
    scheme: Mapped[str] = mapped_column(String(8), default="https")
    source: Mapped[str] = mapped_column(String(24))
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    validation_error: Mapped[str | None] = mapped_column(String(500))


class DeviceCapability(Base):
    __tablename__ = "device_capabilities"
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    hardware_target: Mapped[str] = mapped_column(String(120))
    pzem_model: Mapped[str] = mapped_column(String(120))
    sd_required: Mapped[bool] = mapped_column(Boolean, default=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceConfigVersion(Base):
    __tablename__ = "device_config_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    desired_config: Mapped[dict[str, Any]] = mapped_column(JSON)
    config_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    report: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("device_id", "version", name="uq_device_config_version"),)


class DeviceStatusSnapshot(Base):
    __tablename__ = "device_status_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(48))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON)


class DeviceHeartbeat(Base):
    __tablename__ = "device_heartbeats"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    boot_id: Mapped[str] = mapped_column(String(36))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    device_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_ip: Mapped[str | None] = mapped_column(String(64))
    current_watts: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    rssi_dbm: Mapped[int | None] = mapped_column(Integer)
    pzem_ok: Mapped[bool] = mapped_column(Boolean)
    sd_ok: Mapped[bool] = mapped_column(Boolean)
    time_trusted: Mapped[bool] = mapped_column(Boolean)
    newest_sequence: Mapped[int] = mapped_column(Integer)
    backlog_estimate: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DeviceEvent(Base):
    __tablename__ = "device_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[str] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("device_id", "event_id", name="uq_device_event"),)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    preassignment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SyncCursor(Base):
    __tablename__ = "sync_cursors"
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    highest_contiguous_sequence: Mapped[int] = mapped_column(Integer, default=0)
    maximum_seen_sequence: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SequenceGap(Base):
    __tablename__ = "sequence_gaps"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    start_sequence: Mapped[int] = mapped_column(Integer)
    end_sequence: Mapped[int] = mapped_column(Integer)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    permanent_loss: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (
        CheckConstraint("start_sequence > 0 AND end_sequence >= start_sequence", name="gap_bounds"),
        UniqueConstraint("device_id", "start_sequence", "end_sequence", name="uq_sequence_gap"),
    )


class DeviceNonce(Base):
    __tablename__ = "device_nonces"
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    direction: Mapped[str] = mapped_column(String(24), primary_key=True)
    nonce_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RawReading(Base):
    __tablename__ = "raw_readings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.id", ondelete="RESTRICT"))
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id", ondelete="RESTRICT"))
    sequence: Mapped[int] = mapped_column(Integer)
    boot_id: Mapped[str] = mapped_column(String(36))
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    interval_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_trusted: Mapped[bool] = mapped_column(Boolean)
    voltage_avg: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    voltage_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    voltage_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    current_avg: Mapped[Decimal | None] = mapped_column(Numeric(12, 5))
    current_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 5))
    current_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 5))
    power_avg: Mapped[Decimal | None] = mapped_column(Numeric(16, 5))
    power_min: Mapped[Decimal | None] = mapped_column(Numeric(16, 5))
    power_max: Mapped[Decimal | None] = mapped_column(Numeric(16, 5))
    power_factor: Mapped[Decimal | None] = mapped_column(Numeric(7, 5))
    frequency_hz: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    pzem_energy_start_wh: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    pzem_energy_end_wh: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    device_lifetime_energy_wh: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    device_interval_energy_wh: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    energy_method: Mapped[str] = mapped_column(String(40))
    ct_rating_amps: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    firmware_version: Mapped[str] = mapped_column(String(80))
    record_hash: Mapped[str] = mapped_column(String(64))
    original_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ingestion_source: Mapped[str] = mapped_column(String(8))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        UniqueConstraint("device_id", "sequence", name="uq_raw_device_sequence"),
        CheckConstraint("sequence > 0", name="positive_sequence"),
        CheckConstraint("interval_end > interval_start", name="valid_interval"),
        CheckConstraint("ingestion_source IN ('pull','push')", name="ingestion_source"),
        CheckConstraint(
            "power_factor IS NULL OR (power_factor >= 0 AND power_factor <= 1)", name="power_factor"
        ),
        Index("ix_raw_device_time", "device_id", "interval_start"),
        Index("ix_raw_site_time", "site_id", "interval_start"),
    )


class NormalizedInterval(Base):
    __tablename__ = "normalized_intervals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    raw_reading_id: Mapped[str] = mapped_column(
        ForeignKey("raw_readings.id", ondelete="RESTRICT"), unique=True
    )
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), index=True
    )
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    device_energy_wh: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    server_energy_wh: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    selected_energy_wh: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    selected_method: Mapped[str] = mapped_column(String(40))
    validation_result: Mapped[str] = mapped_column(String(32))
    validation_reason: Mapped[str] = mapped_column(String(500))
    algorithm_version: Mapped[str] = mapped_column(String(32), default="energy-normalizer/1")


class DailyDeviceRollup(Base):
    __tablename__ = "daily_device_rollups"
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), primary_key=True
    )
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    timezone: Mapped[str] = mapped_column(String(64))
    energy_wh: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    peak_watts: Mapped[Decimal] = mapped_column(Numeric(18, 5))
    coverage_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MonthlyDeviceRollup(Base):
    __tablename__ = "monthly_device_rollups"
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), primary_key=True
    )
    month_start: Mapped[date] = mapped_column(Date, primary_key=True)
    energy_wh: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    peak_watts: Mapped[Decimal] = mapped_column(Numeric(18, 5))
    coverage_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SiteRollup(Base):
    __tablename__ = "site_rollups"
    aggregate_set_id: Mapped[str] = mapped_column(
        ForeignKey("aggregate_sets.id", ondelete="RESTRICT"), primary_key=True
    )
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    resolution: Mapped[str] = mapped_column(String(16), primary_key=True)
    energy_wh: Mapped[Decimal] = mapped_column(Numeric(24, 6))
    peak_watts: Mapped[Decimal] = mapped_column(Numeric(18, 5))
    coverage_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4))


class RatePlan(TimestampMixin, Base):
    __tablename__ = "rate_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_id: Mapped[str] = mapped_column(
        ForeignKey("utilities.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    plan_kind: Mapped[str] = mapped_column(String(32), default="official_sce")
    ownership_scope: Mapped[str] = mapped_column(String(32), default="global")
    owner_site_id: Mapped[str | None] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True
    )
    owner_utility_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    status: Mapped[str] = mapped_column(String(24), default="active")
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=1)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    removed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removal_reason: Mapped[str | None] = mapped_column(String(500))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cloned_from_rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="SET NULL", use_alter=True)
    )
    __table_args__ = (
        UniqueConstraint("utility_id", "code", name="uq_rate_plan_utility_code"),
        CheckConstraint(
            "status IN ('draft','active','retired','removed')",
            name="rate_plan_lifecycle_status",
        ),
        CheckConstraint("lifecycle_revision > 0", name="rate_plan_lifecycle_revision"),
    )


class RateVersion(Base):
    __tablename__ = "rate_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_plan_id: Mapped[str] = mapped_column(
        ForeignKey("rate_plans.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    pricing_model: Mapped[str] = mapped_column(String(32), default="time_of_use", index=True)
    source_url: Mapped[str] = mapped_column(String(500))
    source_checked_on: Mapped[date] = mapped_column(Date)
    source_notes: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    immutable_after_use: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    source_kind: Mapped[str] = mapped_column(String(32), default="custom")
    source_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_label: Mapped[str | None] = mapped_column(String(240))
    change_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    normalized_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    automatically_activated: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="SET NULL"), index=True
    )
    lifecycle_revision: Mapped[int] = mapped_column(Integer, default=1)
    status_before_removal: Mapped[str | None] = mapped_column(String(24))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    removed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    removal_reason: Mapped[str | None] = mapped_column(String(500))
    restored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restored_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (
        UniqueConstraint("rate_plan_id", "version", name="uq_rate_version_number"),
        CheckConstraint(
            "pricing_model IN ('flat','time_of_use','tiered','time_of_use_tiered')",
            name="rate_version_pricing_model",
        ),
    )


class RateSeason(Base):
    __tablename__ = "rate_seasons"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(32))
    start_month: Mapped[int] = mapped_column(Integer)
    start_day: Mapped[int] = mapped_column(Integer)
    end_month: Mapped[int] = mapped_column(Integer)
    end_day: Mapped[int] = mapped_column(Integer)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    leap_day_behavior: Mapped[str] = mapped_column(String(32), default="include")


class RateDayType(Base):
    __tablename__ = "rate_day_types"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(32))
    weekdays: Mapped[list[int]] = mapped_column(JSON, default=list)
    holiday_behavior: Mapped[str] = mapped_column(String(32), default="weekday")
    holiday_source: Mapped[str | None] = mapped_column(String(500))


class RatePeriod(Base):
    __tablename__ = "rate_periods"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    season_name: Mapped[str] = mapped_column(String(32))
    day_type: Mapped[str] = mapped_column(String(32))
    start_minute: Mapped[int] = mapped_column(Integer)
    end_minute: Mapped[int] = mapped_column(Integer)
    bucket: Mapped[str] = mapped_column(String(40))
    price_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    delivery_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal("0"))
    generation_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal("0"))
    adjustment_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8), default=Decimal("0"))
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        CheckConstraint("start_minute >= 0 AND start_minute < 1440", name="period_start"),
        CheckConstraint("end_minute > 0 AND end_minute <= 1440", name="period_end"),
        CheckConstraint("end_minute > start_minute", name="period_order"),
    )


class RateTierDefinition(Base):
    __tablename__ = "rate_tier_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    stable_tier_id: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(120))
    display_order: Mapped[int] = mapped_column(Integer)
    lower_bound_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 9), default=Decimal("0"))
    upper_bound_kwh: Mapped[Decimal | None] = mapped_column(Numeric(20, 9))
    lower_bound_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    upper_bound_multiplier: Mapped[Decimal | None] = mapped_column(Numeric(16, 8))
    price_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    tou_prices: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    season_name: Mapped[str | None] = mapped_column(String(80))
    source_citation: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        UniqueConstraint("rate_version_id", "stable_tier_id", name="uq_rate_tier_stable_id"),
        UniqueConstraint("rate_version_id", "display_order", name="uq_rate_tier_order"),
        CheckConstraint("display_order >= 0", name="rate_tier_order_nonnegative"),
        CheckConstraint("lower_bound_kwh >= 0", name="rate_tier_lower_nonnegative"),
        CheckConstraint(
            "upper_bound_kwh IS NULL OR upper_bound_kwh > lower_bound_kwh",
            name="rate_tier_bounds",
        ),
        CheckConstraint("price_per_kwh >= 0", name="rate_tier_price_nonnegative"),
    )


class RateThresholdRule(Base):
    __tablename__ = "rate_threshold_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), unique=True, index=True
    )
    basis: Mapped[str] = mapped_column(String(32), default="fixed_cycle_kwh")
    daily_baseline_kwh: Mapped[Decimal | None] = mapped_column(Numeric(18, 9))
    baseline_region: Mapped[str | None] = mapped_column(String(120))
    baseline_category: Mapped[str | None] = mapped_column(String(120))
    rounding_policy: Mapped[str] = mapped_column(String(32), default="none")
    expected_cycle_start_day: Mapped[int] = mapped_column(Integer, default=1)
    source_citation: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        CheckConstraint(
            "basis IN ('fixed_cycle_kwh','daily_baseline_kwh')",
            name="rate_threshold_basis",
        ),
        CheckConstraint(
            "rounding_policy IN ('none','nearest_kwh','floor_kwh','ceil_kwh')",
            name="rate_threshold_rounding",
        ),
        CheckConstraint(
            "expected_cycle_start_day >= 1 AND expected_cycle_start_day <= 31",
            name="rate_threshold_cycle_day",
        ),
    )


class RateSeasonalBaseline(Base):
    __tablename__ = "rate_seasonal_baselines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    start_month: Mapped[int] = mapped_column(Integer)
    start_day: Mapped[int] = mapped_column(Integer)
    end_month: Mapped[int] = mapped_column(Integer)
    end_day: Mapped[int] = mapped_column(Integer)
    daily_kwh: Mapped[Decimal] = mapped_column(Numeric(18, 9))
    source_citation: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (
        UniqueConstraint("rate_version_id", "name", name="uq_rate_seasonal_baseline_name"),
        CheckConstraint("daily_kwh > 0", name="rate_seasonal_baseline_positive"),
    )


class BaselineRule(Base):
    __tablename__ = "baseline_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    credit_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    requires_full_account: Mapped[bool] = mapped_column(Boolean, default=True)
    allocation_source: Mapped[str] = mapped_column(String(80), default="user_configured")


class FixedChargeRule(Base):
    __tablename__ = "fixed_charge_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    amount_per_day: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    account_once: Mapped[bool] = mapped_column(Boolean, default=True)


class RateAdjustment(Base):
    __tablename__ = "rate_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    component: Mapped[str] = mapped_column(String(40))
    operation: Mapped[str] = mapped_column(String(24))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 8))
    unit: Mapped[str] = mapped_column(String(32), default="per_kwh")
    scope: Mapped[str] = mapped_column(String(40), default="all_energy")
    eligibility: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RateSyncConfiguration(Base):
    __tablename__ = "rate_sync_configuration"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default="default")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    schedule_cron: Mapped[str] = mapped_column(String(64), default="15 3 * * 0")
    timezone: Mapped[str] = mapped_column(String(64), default="America/Los_Angeles")
    jitter_minutes: Mapped[int] = mapped_column(Integer, default=20)
    approval_mode: Mapped[str] = mapped_column(String(32), default="manual_review")
    auto_activate_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    last_scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_scheduled_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempted_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_source_change: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_candidate_created: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_approved_version: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class RateSource(Base):
    __tablename__ = "rate_sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(String(500), unique=True)
    parser_id: Mapped[str] = mapped_column(String(80))
    effective_from_hint: Mapped[date | None] = mapped_column(Date)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BackgroundJob(Base):
    __tablename__ = "background_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    requested_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(160), index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(160), index=True)
    trigger_type: Mapped[str] = mapped_column(String(24), default="manual")
    progress: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        Index(
            "uq_background_jobs_active_dedupe",
            "job_type",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL AND status IN ('queued','running')"),
            sqlite_where=text("dedupe_key IS NOT NULL AND status IN ('queued','running')"),
        ),
    )


class RateSourceCheckRun(Base):
    __tablename__ = "rate_source_checks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="CASCADE"), index=True
    )
    rate_source_id: Mapped[str] = mapped_column(
        ForeignKey("rate_sources.id", ondelete="RESTRICT"), index=True
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32), index=True)
    final_url: Mapped[str | None] = mapped_column(String(500))
    etag: Mapped[str | None] = mapped_column(String(500))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    response_bytes: Mapped[int | None] = mapped_column(Integer)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    artifact_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)


class RateSourceArtifact(Base):
    __tablename__ = "rate_source_artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_check_id: Mapped[str] = mapped_column(
        ForeignKey("rate_source_checks.id", ondelete="CASCADE"), index=True
    )
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    content_type: Mapped[str] = mapped_column(String(160))
    byte_size: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(1000))
    original_filename: Mapped[str | None] = mapped_column(String(255))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RateExtractionResult(Base):
    __tablename__ = "rate_extraction_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("rate_source_artifacts.id", ondelete="CASCADE"), index=True
    )
    parser_id: Mapped[str] = mapped_column(String(80))
    parser_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), index=True)
    normalized_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RateChangeCandidate(Base):
    __tablename__ = "rate_change_candidates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rate_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_plans.id", ondelete="SET NULL"), index=True
    )
    extraction_result_id: Mapped[str] = mapped_column(
        ForeignKey("rate_extraction_results.id", ondelete="RESTRICT"), index=True
    )
    base_rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="SET NULL")
    )
    candidate_rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default="pending_review", index=True)
    risk_level: Mapped[str] = mapped_column(String(24), default="manual_review")
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class RateCandidateDifference(Base):
    __tablename__ = "rate_candidate_differences"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("rate_change_candidates.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(500))
    change_type: Mapped[str] = mapped_column(String(24))
    before_value: Mapped[Any | None] = mapped_column(JSON)
    after_value: Mapped[Any | None] = mapped_column(JSON)
    material: Mapped[bool] = mapped_column(Boolean, default=True)


class RateApprovalDecision(Base):
    __tablename__ = "rate_approval_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("rate_change_candidates.id", ondelete="CASCADE"), index=True
    )
    decision: Mapped[str] = mapped_column(String(24))
    comment: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RateVersionSource(Base):
    __tablename__ = "rate_version_sources"
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="CASCADE"), primary_key=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("rate_source_artifacts.id", ondelete="RESTRICT"), primary_key=True
    )
    extraction_result_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_extraction_results.id", ondelete="SET NULL")
    )
    relationship: Mapped[str] = mapped_column(String(32), default="primary")


class UtilityBillImport(Base):
    __tablename__ = "utility_bill_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    job_id: Mapped[str] = mapped_column(
        ForeignKey("background_jobs.id", ondelete="RESTRICT"), unique=True, index=True
    )
    utility_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("rate_source_artifacts.id", ondelete="RESTRICT"), unique=True, index=True
    )
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="review_required", index=True)
    source_role: Mapped[str] = mapped_column(String(40), default="supporting")
    extraction_method: Mapped[str] = mapped_column(String(16))
    parser_version: Mapped[str] = mapped_column(String(40))
    page_count: Mapped[int] = mapped_column(Integer)
    retention_mode: Mapped[str] = mapped_column(String(32), default="retain")
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    history_cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    history_cleared_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    sanitized_evidence_path: Mapped[str] = mapped_column(String(1000))
    rate_plan_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_plans.id", ondelete="SET NULL"), index=True
    )
    rate_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="SET NULL"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    blocking_warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    extraction_warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "utility_account_id", "content_sha256", name="uq_utility_bill_import_account_hash"
        ),
        CheckConstraint(
            "status IN ('processing','review_required','ready_to_publish','published',"
            "'rejected','failed')",
            name="utility_bill_import_status",
        ),
        CheckConstraint(
            "source_role IN ('supporting','authoritative_account_specific','reference_only')",
            name="utility_bill_import_source_role",
        ),
        CheckConstraint(
            "extraction_method IN ('text','ocr','mixed')",
            name="utility_bill_import_extraction_method",
        ),
        CheckConstraint(
            "retention_mode IN ('retain','retain_until','delete_after_approval')",
            name="utility_bill_import_retention",
        ),
        CheckConstraint("page_count > 0", name="utility_bill_import_page_count"),
        CheckConstraint("revision > 0", name="utility_bill_import_revision"),
    )


class UtilityBillExtractionRevision(Base):
    __tablename__ = "utility_bill_extraction_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    bill_import_id: Mapped[str] = mapped_column(
        ForeignKey("utility_bill_imports.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="review_required")
    parser_version: Mapped[str] = mapped_column(String(40))
    ocr_version: Mapped[str | None] = mapped_column(String(80))
    normalized_account_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normalized_rate_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normalized_cycle_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    normalized_artifact: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_text_sha256: Mapped[str] = mapped_column(String(64))
    normalized_text_sha256: Mapped[str] = mapped_column(String(64))
    sanitized_text_path: Mapped[str] = mapped_column(String(1000))
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        UniqueConstraint("bill_import_id", "revision", name="uq_utility_bill_extraction_revision"),
        CheckConstraint(
            "status IN ('review_required','approved','superseded','failed')",
            name="utility_bill_extraction_status",
        ),
        CheckConstraint("revision > 0", name="utility_bill_extraction_revision_positive"),
    )


class UtilityBillExtractedField(Base):
    __tablename__ = "utility_bill_extracted_fields"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    extraction_revision_id: Mapped[str] = mapped_column(
        ForeignKey("utility_bill_extraction_revisions.id", ondelete="CASCADE"), index=True
    )
    output_kind: Mapped[str] = mapped_column(String(24), index=True)
    field_key: Mapped[str] = mapped_column(String(240))
    raw_value: Mapped[Any | None] = mapped_column(JSON)
    normalized_value: Mapped[Any | None] = mapped_column(JSON)
    corrected_value: Mapped[Any | None] = mapped_column(JSON)
    page_number: Mapped[int | None] = mapped_column(Integer)
    text_region: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    extraction_method: Mapped[str] = mapped_column(String(16))
    parser_version: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[str] = mapped_column(String(32), index=True)
    review_state: Mapped[str] = mapped_column(String(24), default="unreviewed")
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    normalization_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    parser_rule: Mapped[str | None] = mapped_column(String(160))
    validation_result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    confirmed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "extraction_revision_id",
            "output_kind",
            "field_key",
            name="uq_utility_bill_extracted_field",
        ),
        CheckConstraint(
            "output_kind IN ('account','rate_plan','billing_cycle')",
            name="utility_bill_field_output_kind",
        ),
        CheckConstraint(
            "extraction_method IN ('text','ocr','mixed','administrator')",
            name="utility_bill_field_method",
        ),
        CheckConstraint(
            "confidence IN ('parser_confirmed','arithmetic_confirmed','high','medium','low',"
            "'manual_confirmed','missing','conflict','not_applicable')",
            name="utility_bill_field_confidence",
        ),
        CheckConstraint(
            "review_state IN ('unreviewed','confirmed','corrected','rejected')",
            name="utility_bill_field_review_state",
        ),
    )


class UtilityBillFieldConflict(Base):
    __tablename__ = "utility_bill_field_conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    bill_import_id: Mapped[str] = mapped_column(
        ForeignKey("utility_bill_imports.id", ondelete="CASCADE"), index=True
    )
    field_key: Mapped[str] = mapped_column(String(240))
    extracted_value: Mapped[Any | None] = mapped_column(JSON)
    configured_value: Mapped[Any | None] = mapped_column(JSON)
    comparison_source: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="unresolved", index=True)
    blocking: Mapped[bool] = mapped_column(Boolean, default=True)
    resolution_note: Mapped[str | None] = mapped_column(String(1000))
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "bill_import_id",
            "field_key",
            "comparison_source",
            name="uq_utility_bill_field_conflict",
        ),
        CheckConstraint(
            "status IN ('unresolved','accepted_bill','accepted_configured','dismissed')",
            name="utility_bill_conflict_status",
        ),
    )


class UtilityBillCycleDraft(Base):
    __tablename__ = "utility_bill_cycle_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    bill_import_id: Mapped[str] = mapped_column(
        ForeignKey("utility_bill_imports.id", ondelete="CASCADE"), unique=True, index=True
    )
    extraction_revision_id: Mapped[str] = mapped_column(
        ForeignKey("utility_bill_extraction_revisions.id", ondelete="RESTRICT"), index=True
    )
    utility_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="draft", index=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cycle_days: Mapped[int | None] = mapped_column(Integer)
    meter_read_date: Mapped[date | None] = mapped_column(Date)
    total_usage_kwh: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    usage_by_tier: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    usage_by_tou: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    meter_records: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    current_tier: Mapped[str | None] = mapped_column(String(120))
    projected_tier: Mapped[str | None] = mapped_column(String(120))
    energy_subtotal: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    full_bill_total: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    fixed_charges: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    taxes_fees: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    credits: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    adjustments: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    threshold_interpretation: Mapped[str] = mapped_column(String(40), default="unknown")
    reconciliation_status: Mapped[str] = mapped_column(String(32), default="not_compared")
    billing_cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("billing_cycles.id", ondelete="SET NULL"), index=True
    )
    utility_usage_import_id: Mapped[str | None] = mapped_column(
        ForeignKey("utility_usage_imports.id", ondelete="SET NULL"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','approved','imported','rejected')",
            name="utility_bill_cycle_draft_status",
        ),
        CheckConstraint(
            "threshold_interpretation IN ('fixed_cycle_threshold','daily_baseline',"
            "'baseline_multiplier','unknown')",
            name="utility_bill_cycle_threshold_interpretation",
        ),
        CheckConstraint(
            "reconciliation_status IN ('not_compared','matched','difference','adjusted')",
            name="utility_bill_cycle_reconciliation",
        ),
        CheckConstraint(
            "starts_at IS NULL OR ends_at IS NULL OR ends_at > starts_at",
            name="utility_bill_cycle_window",
        ),
        CheckConstraint(
            "total_usage_kwh IS NULL OR total_usage_kwh >= 0",
            name="utility_bill_cycle_usage_nonnegative",
        ),
        CheckConstraint("revision > 0", name="utility_bill_cycle_revision"),
    )


class RateAssignment(Base):
    __tablename__ = "rate_assignments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="CASCADE"), index=True
    )
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="RESTRICT"), index=True
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assignment_reason: Mapped[str | None] = mapped_column(String(500))
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    cancelled_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str | None] = mapped_column(String(160), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="rate_assignment_effective_window",
        ),
        CheckConstraint("revision > 0", name="rate_assignment_revision"),
    )


class BillingCycle(Base):
    __tablename__ = "billing_cycles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    explicit_meter_dates: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(24), default="expected", index=True)
    boundary_source: Mapped[str] = mapped_column(String(32), default="generated")
    override_revision: Mapped[int] = mapped_column(Integer, default=0)
    recalculation_version: Mapped[int] = mapped_column(Integer, default=0)
    locked_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "utility_account_id", "starts_at", "ends_at", name="uq_billing_cycle_account_window"
        ),
        CheckConstraint("ends_at > starts_at", name="billing_cycle_window"),
        CheckConstraint(
            "status IN ('expected','confirmed','recalculating','finalized')",
            name="billing_cycle_status",
        ),
        CheckConstraint(
            "boundary_source IN ('generated','manual_override','utility_import','external_feed')",
            name="billing_cycle_boundary_source",
        ),
    )


class AccountUsageAuthority(Base):
    __tablename__ = "account_usage_authorities"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="CASCADE"), unique=True, index=True
    )
    authority_type: Mapped[str] = mapped_column(String(48))
    aggregate_set_id: Mapped[str | None] = mapped_column(
        ForeignKey("aggregate_sets.id", ondelete="SET NULL")
    )
    device_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_reference: Mapped[str | None] = mapped_column(String(500))
    confidence: Mapped[str] = mapped_column(String(24), default="unverified")
    complete_account: Mapped[bool] = mapped_column(Boolean, default=False)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "authority_type IN ('complete_site_aggregate','service_leg_pair',"
            "'whole_account_meter','utility_interval_import','manual_cycle_usage',"
            "'external_feed','partial_monitored_circuits')",
            name="account_usage_authority_type",
        ),
        CheckConstraint(
            "confidence IN ('unverified','low','medium','high','utility_verified')",
            name="account_usage_authority_confidence",
        ),
    )


class ManualAccountUsage(Base):
    __tablename__ = "manual_account_usage"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    billing_cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("billing_cycles.id", ondelete="RESTRICT"), index=True
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    cumulative_kwh: Mapped[Decimal] = mapped_column(Numeric(24, 9))
    source_note: Mapped[str] = mapped_column(String(500))
    evidence_reference: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    verification_status: Mapped[str] = mapped_column(String(24), default="unverified")
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "utility_account_id", "idempotency_key", name="uq_manual_usage_idempotency"
        ),
        CheckConstraint("cumulative_kwh >= 0", name="manual_account_usage_nonnegative"),
        CheckConstraint(
            "verification_status IN ('unverified','verified','reconciled')",
            name="manual_account_usage_verification",
        ),
    )


class UtilityUsageImport(Base):
    __tablename__ = "utility_usage_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    import_kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default="preview")
    timezone: Mapped[str] = mapped_column(String(64))
    source_name: Mapped[str] = mapped_column(String(240))
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    field_mapping: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    conflict_count: Mapped[int] = mapped_column(Integer, default=0)
    normalized_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "utility_account_id", "content_sha256", name="uq_utility_usage_import_content"
        ),
        CheckConstraint(
            "import_kind IN ('interval','daily','cycle_cumulative','cycle_dates','bill_total')",
            name="utility_usage_import_kind",
        ),
        CheckConstraint(
            "status IN ('preview','committed','rejected','reversed')",
            name="utility_usage_import_status",
        ),
    )


class CostCalculationRun(Base):
    __tablename__ = "cost_calculation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    aggregate_set_id: Mapped[str] = mapped_column(
        ForeignKey("aggregate_sets.id", ondelete="RESTRICT")
    )
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="RESTRICT")
    )
    input_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    algorithm_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24))
    coverage_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CostIntervalResult(Base):
    __tablename__ = "cost_interval_results"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("cost_calculation_runs.id", ondelete="CASCADE"), index=True
    )
    normalized_interval_id: Mapped[str | None] = mapped_column(
        ForeignKey("normalized_intervals.id", ondelete="RESTRICT")
    )
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bucket: Mapped[str] = mapped_column(String(40))
    energy_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    price_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    unrounded_cost: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    component: Mapped[str] = mapped_column(String(40), default="energy")
    adjustment_breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    calculation_version: Mapped[str] = mapped_column(String(40), default="rate-engine/1")


class TierAllocationSegment(Base):
    __tablename__ = "tier_allocation_segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    billing_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("billing_cycles.id", ondelete="CASCADE"), index=True
    )
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    normalized_interval_id: Mapped[str | None] = mapped_column(
        ForeignKey("normalized_intervals.id", ondelete="RESTRICT"), index=True
    )
    import_id: Mapped[str | None] = mapped_column(
        ForeignKey("utility_usage_imports.id", ondelete="RESTRICT")
    )
    segment_order: Mapped[int] = mapped_column(Integer)
    interval_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    interval_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rate_version_id: Mapped[str] = mapped_column(
        ForeignKey("rate_versions.id", ondelete="RESTRICT"), index=True
    )
    tier_definition_id: Mapped[str] = mapped_column(
        ForeignKey("rate_tier_definitions.id", ondelete="RESTRICT")
    )
    tier_stable_id: Mapped[str] = mapped_column(String(80))
    tier_name: Mapped[str] = mapped_column(String(120))
    tou_period: Mapped[str | None] = mapped_column(String(80))
    cumulative_start_kwh: Mapped[Decimal] = mapped_column(Numeric(24, 9))
    cumulative_end_kwh: Mapped[Decimal] = mapped_column(Numeric(24, 9))
    segment_energy_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    price_per_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 8))
    unrounded_energy_charge: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    derived_threshold_kwh: Mapped[Decimal | None] = mapped_column(Numeric(20, 9))
    usage_authority_type: Mapped[str] = mapped_column(String(48))
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    recalculation_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "billing_cycle_id",
            "normalized_interval_id",
            "segment_order",
            "recalculation_version",
            name="uq_tier_segment_interval_recalc",
        ),
        CheckConstraint("segment_energy_kwh >= 0", name="tier_segment_energy_nonnegative"),
        CheckConstraint(
            "cumulative_end_kwh >= cumulative_start_kwh",
            name="tier_segment_cumulative_order",
        ),
    )


class CycleTierSummary(Base):
    __tablename__ = "cycle_tier_summaries"
    billing_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("billing_cycles.id", ondelete="CASCADE"), primary_key=True
    )
    tier_stable_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recalculation_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier_name: Mapped[str] = mapped_column(String(120))
    lower_bound_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    upper_bound_kwh: Mapped[Decimal | None] = mapped_column(Numeric(20, 9))
    usage_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    energy_charge: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TierProjectionSnapshot(Base):
    __tablename__ = "tier_projection_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    billing_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("billing_cycles.id", ondelete="CASCADE"), index=True
    )
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    method: Mapped[str] = mapped_column(String(32))
    projected_usage_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    projected_energy_charge: Mapped[Decimal] = mapped_column(Numeric(24, 12))
    projected_tier_stable_id: Mapped[str | None] = mapped_column(String(80))
    confidence: Mapped[str] = mapped_column(String(24))
    coverage_percent: Mapped[Decimal] = mapped_column(Numeric(7, 4))


class AccountReconciliationAdjustment(Base):
    __tablename__ = "account_reconciliation_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    billing_cycle_id: Mapped[str] = mapped_column(
        ForeignKey("billing_cycles.id", ondelete="RESTRICT"), index=True
    )
    component: Mapped[str] = mapped_column(String(48))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    notes: Mapped[str] = mapped_column(String(1000))
    provenance: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DailyCostRollup(Base):
    __tablename__ = "daily_cost_rollups"
    run_id: Mapped[str] = mapped_column(
        ForeignKey("cost_calculation_runs.id", ondelete="CASCADE"), primary_key=True
    )
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(40), primary_key=True)
    component: Mapped[str] = mapped_column(String(40), primary_key=True)
    energy_kwh: Mapped[Decimal] = mapped_column(Numeric(20, 9))
    unrounded_cost: Mapped[Decimal] = mapped_column(Numeric(24, 12))


class ManualBillAdjustment(Base):
    __tablename__ = "manual_bill_adjustments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    utility_account_id: Mapped[str] = mapped_column(
        ForeignKey("utility_accounts.id", ondelete="RESTRICT"), index=True
    )
    billing_cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("billing_cycles.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(160))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AlertRule(TimestampMixin, Base):
    __tablename__ = "alert_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160))
    rule_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="CASCADE"))
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    debounce_seconds: Mapped[int] = mapped_column(Integer, default=0)
    resolve_seconds: Mapped[int] = mapped_column(Integer, default=0)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class AlertInstance(Base):
    __tablename__ = "alert_instances"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="RESTRICT"), index=True
    )
    device_id: Mapped[str | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), index=True
    )
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), index=True)
    severity: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    silenced_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class NotificationChannel(TimestampMixin, Base):
    __tablename__ = "notification_channels"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(120))
    channel_type: Mapped[str] = mapped_column(String(24))
    encrypted_config: Mapped[bytes] = mapped_column(LargeBinary)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationAttempt(Base):
    __tablename__ = "notification_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    alert_instance_id: Mapped[str | None] = mapped_column(
        ForeignKey("alert_instances.id", ondelete="CASCADE"), index=True
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="RESTRICT")
    )
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    attempt_number: Mapped[int] = mapped_column(Integer)
    response_summary: Mapped[str | None] = mapped_column(String(500))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)


class FirmwareRelease(TimestampMixin, Base):
    __tablename__ = "firmware_releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    version: Mapped[str] = mapped_column(String(80))
    channel: Mapped[str] = mapped_column(String(24))
    hardware_target: Mapped[str] = mapped_column(String(120))
    protocol_min: Mapped[str] = mapped_column(String(40))
    protocol_max: Mapped[str] = mapped_column(String(40))
    file_path: Mapped[str] = mapped_column(String(500))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    signature: Mapped[str] = mapped_column(Text)
    signing_key_id: Mapped[str] = mapped_column(String(128))
    release_notes: Mapped[str] = mapped_column(Text)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=False)


class FirmwareDeployment(Base):
    __tablename__ = "firmware_deployments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    firmware_release_id: Mapped[str] = mapped_column(
        ForeignKey("firmware_releases.id", ondelete="RESTRICT"), index=True
    )
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="scheduled")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(500))
    rollback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class ExportJob(Base):
    __tablename__ = "export_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    requested_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    format: Mapped[str] = mapped_column(String(8))
    query: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))
    file_path: Mapped[str | None] = mapped_column(String(500))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportDefinition(TimestampMixin, Base):
    __tablename__ = "report_definitions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(160))
    report_type: Mapped[str] = mapped_column(String(40))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class GeneratedReport(Base):
    __tablename__ = "generated_reports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("report_definitions.id", ondelete="SET NULL")
    )
    requested_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(24))
    file_path: Mapped[str | None] = mapped_column(String(500))
    data_coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackupRun(Base):
    __tablename__ = "backup_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    path: Mapped[str | None] = mapped_column(String(500))
    manifest_hash: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class LogExportJob(Base):
    __tablename__ = "log_export_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    requested_by: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    services: Mapped[list[str]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), index=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    correlation_id: Mapped[str] = mapped_column(String(128), index=True)


class WorkerState(Base):
    __tablename__ = "worker_state"
    worker_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(80))
    last_loop_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
