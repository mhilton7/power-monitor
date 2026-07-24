from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AlertInstance,
    AlertRule,
    Device,
    DeviceHeartbeat,
    RateAssignment,
    RatePlan,
    RateVersion,
    Site,
    Utility,
    UtilityAccount,
    WorkerState,
)
from app.problem import ProblemError
from app.status_indicators import (
    INDICATOR_DEFINITIONS,
    INDICATOR_REGISTRY,
    PAGES,
    REGISTRY_VERSION,
    ZONES,
    compiled_configuration,
    materialize_configuration,
    repair_configuration,
    resolve_layout,
    validate_configuration,
)

KNOWN_CONFIGURABLE_SURFACES = {
    "alerts.active_count",
    "alerts.critical_count",
    "alerts.disconnect_rule_state",
    "alerts.enabled_rule_count",
    "alerts.warning_count",
    "backup.last_result",
    "backup.verification",
    "cost.billing_cycle_estimate",
    "cost.today",
    "data.aggregate_coverage",
    "data.current_power",
    "data.energy_today",
    "data.live_connection",
    "data.recent_peak",
    "device.heartbeat_freshness",
    "device.offline_count",
    "device.online_count",
    "device.pzem_health",
    "device.sd_health",
    "device.sync_backlog",
    "device.synchronized_count",
    "device.time_sync",
    "device.wifi_signal",
    "enrollment.availability",
    "energy.billing_cycle",
    "firmware.update_state",
    "notifications.delivery_health",
    "rate.current_period",
    "rate.current_plan",
    "rate.current_price",
    "rate.current_context",
    "rate.last_successful_check",
    "rate.next_scheduled_check",
    "rate.review_policy",
    "rate.source_health",
    "rate.update_pending",
    "site.current",
    "system.api_health",
    "system.database_health",
    "system.worker_health",
    "topology.aggregate_overlap",
}


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get("pm_csrf")
    assert token
    return {"X-CSRF-Token": token}


async def bootstrap_admin(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "email": "status-admin@example.com",
            "display_name": "Status Administrator",
            "password": "Production-Status-Password-42!",
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def global_item(configuration: dict[str, Any], key: str) -> dict[str, Any]:
    return next(
        item
        for item in configuration["items"]
        if item["indicator_key"] == key
        and item["page"] == "*"
        and item["role"] == "*"
        and item["breakpoint"] == "default"
    )


def current_rate_document() -> dict[str, Any]:
    return {
        "schema_version": "power-monitor-rate-plan/1.0",
        "plan_name": "Live homepage rate plan",
        "plan_code": "STATUS-LIVE-RATE",
        "utility": "Status test utility",
        "description": "Deterministic current-rate indicator fixture",
        "currency": "USD",
        "timezone": "UTC",
        "ownership_scope": "global",
        "owner_id": None,
        "effective_from": "2020-01-01",
        "effective_through": None,
        "cost_scope_default": "energy_only",
        "source_label": "Status indicator fixture",
        "source_note": "Not a production rate source",
        "provider_mode": "custom_combined",
        "seasons": [
            {
                "name": "all-year",
                "start": "01-01",
                "end": "12-31",
                "priority": 0,
                "leap_day_behavior": "include",
                "schedules": [
                    {
                        "day_type": "all-days",
                        "dates": [],
                        "periods": [
                            {
                                "label": "on-peak",
                                "start_minute": 0,
                                "end_minute": 1440,
                                "price_per_kwh": "0.58347000",
                                "delivery_per_kwh": "0",
                                "generation_per_kwh": "0",
                                "adjustment_per_kwh": "0",
                                "display_order": 0,
                            }
                        ],
                    }
                ],
            }
        ],
        "adjustments": [],
        "custom_notes": "",
        "cloned_from_rate_version_id": None,
    }


def test_registry_is_complete_unique_and_self_consistent() -> None:
    assert set(INDICATOR_REGISTRY) == KNOWN_CONFIGURABLE_SURFACES
    assert len(INDICATOR_DEFINITIONS) >= 30
    assert len(INDICATOR_REGISTRY) == len(INDICATOR_DEFINITIONS)
    for definition in INDICATOR_DEFINITIONS:
        assert definition.registry_version == REGISTRY_VERSION
        assert definition.default_zone in definition.allowed_zones
        assert definition.default_zone in ZONES
        assert definition.supported_pages
        assert set(definition.supported_pages) <= set(PAGES)
        assert definition.permission_required
        assert definition.renderer in {
            "count",
            "energy",
            "fraction",
            "freshness",
            "health",
            "money",
            "money-rate",
            "percent",
            "power",
            "signal",
            "text",
        }
        assert definition.presentations == ("compact", "standard", "detailed")
        assert definition.metric_identity
        assert definition.canonical_priority >= 0
        assert definition.allow_duplicate is False
    config = compiled_configuration()
    normalized, warnings = validate_configuration(config)
    assert len(normalized["items"]) >= len(INDICATOR_DEFINITIONS)
    assert not warnings


def test_layout_engine_collapses_empty_zones_and_handles_responsive_counts() -> None:
    permissions = {definition.permission_required for definition in INDICATOR_DEFINITIONS}
    eligible_definitions = [
        definition
        for definition in INDICATOR_DEFINITIONS
        if "overview" in definition.supported_pages
        and not definition.diagnostics_only
        and definition.metric_identity not in {"site.current", "power.current"}
    ]
    for visible_count in (0, 1, 2, 3, 4, 12, len(eligible_definitions)):
        configuration = compiled_configuration()
        for item in configuration["items"]:
            item["visible"] = False
        for definition in eligible_definitions[:visible_count]:
            configuration["items"].append(
                {
                    "indicator_key": definition.key,
                    "page": "overview",
                    "role": "*",
                    "breakpoint": "default",
                    "visible": True,
                    "zone": definition.default_zone,
                    "order": definition.default_order,
                }
            )
        layout = resolve_layout(
            configuration,
            page="overview",
            roles={"admin"},
            permissions=permissions,
            breakpoint="desktop",
            revision=1,
        )
        assert all(zone["items"] for zone in layout["zones"])
        rendered_keys = [
            item["indicator_key"] for zone in layout["zones"] for item in zone["items"]
        ]
        assert len(rendered_keys) == min(visible_count, len(eligible_definitions))
        assert len(rendered_keys) == len(set(rendered_keys))
        rendered_metrics = [
            item["definition"]["metric_identity"]
            for zone in layout["zones"]
            for item in zone["items"]
        ]
        assert len(rendered_metrics) == len(set(rendered_metrics))

    configuration = compiled_configuration()
    mobile = resolve_layout(
        configuration,
        page="overview",
        roles={"viewer"},
        permissions=permissions,
        breakpoint="mobile",
        revision=1,
    )
    assert {zone["key"] for zone in mobile["zones"]} <= {"mobile_status_drawer"}
    assert any(zone["key"] == "mobile_status_drawer" for zone in mobile["zones"])

    # A retired key stays in raw revision content but is ignored without a placeholder.
    configuration["items"].append(
        {
            "indicator_key": "retired.example",
            "page": "*",
            "role": "*",
            "breakpoint": "default",
            "visible": True,
            "zone": "global_status_row",
        }
    )
    resolved = resolve_layout(
        configuration,
        page="overview",
        roles={"admin"},
        permissions=permissions,
        breakpoint="desktop",
        revision=2,
    )
    assert "retired.example" not in {
        item["indicator_key"] for zone in resolved["zones"] for item in zone["items"]
    }
    assert any(warning["code"] == "retired_indicator" for warning in resolved["warnings"])


def test_system_health_is_diagnostics_only_and_duplicate_metrics_repair() -> None:
    permissions = {definition.permission_required for definition in INDICATOR_DEFINITIONS}
    normal = resolve_layout(
        compiled_configuration(),
        page="enrollment",
        roles={"admin"},
        permissions=permissions,
        breakpoint="desktop",
        revision=2,
    )
    normal_keys = {item["indicator_key"] for zone in normal["zones"] for item in zone["items"]}
    assert not {"system.api_health", "system.database_health", "system.worker_health"} & normal_keys
    assert "global_status_row" not in {zone["key"] for zone in normal["zones"]}

    diagnostics = resolve_layout(
        compiled_configuration(),
        page="system_health",
        roles={"admin"},
        permissions=permissions,
        breakpoint="desktop",
        revision=2,
    )
    diagnostic_zone = next(
        zone for zone in diagnostics["zones"] if zone["key"] == "administration_diagnostics"
    )
    assert {item["indicator_key"] for item in diagnostic_zone["items"]} == {
        "system.api_health",
        "system.database_health",
        "system.worker_health",
    }

    duplicate = compiled_configuration()
    duplicate["items"].append(
        {
            "indicator_key": "data.aggregate_coverage",
            "page": "history",
            "role": "*",
            "breakpoint": "default",
            "visible": True,
            "zone": "page_summary",
            "order": 10,
        }
    )
    with pytest.raises(ProblemError) as error:
        validate_configuration(duplicate)
    assert error.value.code == "status_metric_duplicate"
    assert error.value.extra and error.value.extra["metric_identity"] == "data.coverage"

    repaired, repairs = repair_configuration(duplicate)
    normalized, _warnings = validate_configuration(repaired)
    assert repairs
    history = resolve_layout(
        normalized,
        page="history",
        roles={"admin"},
        permissions=permissions,
        breakpoint="desktop",
        revision=2,
    )
    history_metrics = [
        item["definition"]["metric_identity"] for zone in history["zones"] for item in zone["items"]
    ]
    assert "data.coverage" not in history_metrics
    assert len(history_metrics) == len(set(history_metrics))


@pytest.mark.asyncio
async def test_admin_draft_preview_publish_resolution_and_monitoring_integrity(
    api_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    bootstrap = await bootstrap_admin(api_client)
    assert "status_indicators.manage" in bootstrap["user"]["permissions"]
    site_id = (await api_client.get("/api/v1/sites")).json()[0]["id"]
    rule = AlertRule(
        name="Hidden-indicator integrity",
        rule_type="heartbeat_stale",
        severity="warning",
        enabled=True,
        site_id=site_id,
        device_id=None,
        debounce_seconds=0,
        resolve_seconds=0,
        configuration={},
    )
    session.add(rule)
    await session.flush()
    selected_device = Device(
        site_id=site_id,
        hardware_id="status-selected-device",
        name="Selected status device",
        status="online_synchronized",
        last_seen_at=datetime.now(UTC),
    )
    other_device = Device(
        site_id=site_id,
        hardware_id="status-other-device",
        name="Other status device",
        status="online_synchronized",
        last_seen_at=datetime.now(UTC),
    )
    session.add_all([selected_device, other_device])
    await session.flush()
    session.add_all(
        [
            DeviceHeartbeat(
                device_id=selected_device.id,
                boot_id="123e4567-e89b-12d3-a456-426614174010",
                received_at=datetime.now(UTC),
                current_watts=Decimal("321"),
                rssi_dbm=-44,
                pzem_ok=True,
                sd_ok=True,
                time_trusted=True,
                newest_sequence=1,
                backlog_estimate=0,
                payload={},
            ),
            DeviceHeartbeat(
                device_id=other_device.id,
                boot_id="123e4567-e89b-12d3-a456-426614174011",
                received_at=datetime.now(UTC),
                current_watts=Decimal("900"),
                rssi_dbm=-70,
                pzem_ok=False,
                sd_ok=False,
                time_trusted=False,
                newest_sequence=1,
                backlog_estimate=8,
                payload={},
            ),
        ]
    )
    session.add(
        AlertInstance(
            rule_id=rule.id,
            device_id=None,
            site_id=site_id,
            status="active",
            severity="warning",
            opened_at=datetime.now(UTC),
            acknowledged_at=None,
            acknowledged_by=None,
            resolved_at=None,
            silenced_until=None,
            evidence={"source": "deterministic-test"},
        )
    )
    session.add(
        WorkerState(
            worker_name="main",
            instance_id="status-layout-worker",
            last_loop_at=datetime.now(UTC) - timedelta(minutes=2),
            last_success_at=datetime.now(UTC) - timedelta(minutes=2),
            status="failed",
            details={"error_type": "PermissionError"},
        )
    )
    await session.commit()

    registry = await api_client.get("/api/v1/status-indicators/registry")
    assert registry.status_code == 200
    assert set(item["key"] for item in registry.json()["indicators"]) == KNOWN_CONFIGURABLE_SURFACES
    assert len(registry.json()["indicators"]) >= 30
    values = await api_client.get("/api/v1/status-indicators/values")
    assert values.status_code == 200
    assert values.json()["values"]["alerts.active_count"]["display_value"] == "1"
    assert values.json()["values"]["alerts.warning_count"]["display_value"] == "1"
    assert int(values.json()["values"]["alerts.enabled_rule_count"]["display_value"]) >= 1
    assert values.json()["values"]["alerts.disconnect_rule_state"]["display_value"] == "On"
    assert values.json()["values"]["rate.current_plan"]["display_value"] == "Not configured"
    assert values.json()["values"]["rate.current_period"]["display_value"] == "Not configured"
    assert values.json()["values"]["rate.current_price"]["display_value"] == "Not configured"
    assert values.json()["values"]["system.worker_health"]["display_value"] == "Stale"
    assert values.json()["values"]["system.worker_health"]["severity"] == "critical"
    selected_values = await api_client.get(
        f"/api/v1/status-indicators/values?site_id={site_id}&device_id={selected_device.id}"
    )
    assert selected_values.status_code == 200, selected_values.text
    assert selected_values.json()["values"]["data.current_power"]["display_value"] == "321 W"
    assert selected_values.json()["values"]["device.pzem_health"]["display_value"] == "Healthy"
    missing_device = await api_client.get(
        "/api/v1/status-indicators/values?device_id=missing-device"
    )
    assert missing_device.status_code == 404

    draft = (await api_client.get("/api/v1/admin/status-indicators/draft")).json()
    configuration = materialize_configuration(draft["configuration"])
    global_item(configuration, "data.energy_today")["visible"] = False
    offline = global_item(configuration, "device.offline_count")
    offline["zone"] = "page_summary"
    offline["order"] = 5
    configuration["items"].append(
        {
            **global_item(configuration, "device.online_count"),
            "page": "overview",
            "role": "viewer",
            "breakpoint": "mobile",
            "density": "compact",
            "zone": "mobile_status_drawer",
        }
    )
    saved = await api_client.put(
        "/api/v1/admin/status-indicators/draft",
        headers=csrf(api_client),
        json={
            "base_revision": draft["base_revision"],
            "draft_revision": 0,
            "configuration": configuration,
            "reason": "Exercise visibility, placement, role, page, and mobile precedence",
        },
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    validation = await api_client.post(
        "/api/v1/admin/status-indicators/validate",
        headers=csrf(api_client),
        json={},
    )
    assert validation.status_code == 200, validation.text
    preview = await api_client.post(
        "/api/v1/admin/status-indicators/preview",
        headers=csrf(api_client),
        json={
            "page": "overview",
            "role": "admin",
            "breakpoint": "desktop",
            "scenario": "all_defaults",
        },
    )
    assert preview.status_code == 200, preview.text
    assert all(zone["items"] for zone in preview.json()["layout"]["zones"])
    published = await api_client.post(
        "/api/v1/admin/status-indicators/publish",
        headers=csrf(api_client),
        json={
            "base_revision": saved_body["base_revision"],
            "draft_revision": saved_body["draft_revision"],
            "reason": "Publish deterministic layout",
            "confirm": True,
        },
    )
    assert published.status_code == 201, published.text
    resolved = (
        await api_client.get("/api/v1/status-indicators/layout?page=overview&breakpoint=desktop")
    ).json()
    rendered = {
        item["indicator_key"]: (zone["key"], item)
        for zone in resolved["zones"]
        for item in zone["items"]
    }
    assert "data.energy_today" not in rendered
    # The semantic Overview summary placement takes precedence over a global move,
    # preserving the canonical Overview information hierarchy.
    assert rendered["device.offline_count"][0] == "overview_summary"
    assert all(zone["items"] for zone in resolved["zones"])
    # Hiding is presentation-only: the simulated alert remains queryable and counted.
    alerts = await api_client.get("/api/v1/alerts")
    assert alerts.status_code == 200
    assert any(item["evidence"].get("source") == "deterministic-test" for item in alerts.json())
    assert (await api_client.get("/api/v1/status-indicators/values")).json()["values"][
        "alerts.active_count"
    ]["display_value"] == "1"

    audit = (await api_client.get("/api/v1/audit-events")).json()
    actions = {item["action"] for item in audit}
    assert {
        "status_layout.draft_saved",
        "status_layout.indicator_disabled",
        "status_layout.indicator_moved",
        "status_layout.draft_published",
    } <= actions


@pytest.mark.asyncio
async def test_current_rate_indicators_use_effective_account_plan_period_price_and_time(
    api_client: httpx.AsyncClient,
    session: AsyncSession,
) -> None:
    await bootstrap_admin(api_client)
    site = await session.scalar(select(Site).order_by(Site.name))
    utility = await session.scalar(select(Utility).order_by(Utility.name))
    assert site and utility

    account = UtilityAccount(
        site_id=site.id,
        utility_id=utility.id,
        name="Home SCE account",
        timezone="UTC",
        currency="USD",
    )
    plan = RatePlan(
        utility_id=utility.id,
        code="STATUS-LIVE-RATE",
        name="Live homepage rate plan",
        description="Current status indicator fixture",
        plan_kind="custom",
        ownership_scope="global",
        currency="USD",
        timezone="UTC",
        status="active",
    )
    session.add_all([account, plan])
    await session.flush()
    version = RateVersion(
        rate_plan_id=plan.id,
        version=1,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        timezone="UTC",
        currency="USD",
        source_url="https://example.test/status-live-rate",
        source_checked_on=date(2026, 7, 20),
        source_notes="Deterministic current-rate fixture",
        content_hash="9" * 64,
        immutable_after_use=True,
        is_active=True,
        status="active",
        source_kind="custom",
        normalized_payload=current_rate_document(),
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(version)
    await session.flush()
    account.active_rate_version_id = version.id
    sensor = Device(
        site_id=site.id,
        utility_account_id=account.id,
        hardware_id="status-current-rate-sensor",
        name="Current rate sensor",
        lifecycle_status="active",
    )
    session.add_all(
        [
            sensor,
            RateAssignment(
                utility_account_id=account.id,
                rate_version_id=version.id,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                effective_to=None,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            ),
        ]
    )
    await session.commit()

    response = await api_client.get(f"/api/v1/status-indicators/values?site_id={site.id}")
    assert response.status_code == 200, response.text
    values = response.json()["values"]
    assert values["rate.current_plan"]["display_value"] == "Home SCE account"
    assert "Live homepage rate plan v1" in values["rate.current_plan"]["detail"]
    assert values["rate.current_period"]["display_value"] == "On-peak"
    assert "Home SCE account: On-peak" in values["rate.current_period"]["detail"]
    assert " UTC)" in values["rate.current_period"]["detail"]
    assert values["rate.current_price"]["display_value"] == "$0.58347/kWh"
    assert "during On-peak" in values["rate.current_price"]["detail"]
    assert values["rate.current_price"]["freshness_at"] is not None

    device_response = await api_client.get(
        f"/api/v1/status-indicators/values?site_id={site.id}&device_id={sensor.id}"
    )
    assert device_response.status_code == 200, device_response.text
    assert (
        device_response.json()["values"]["rate.current_plan"]["display_value"] == "Home SCE account"
    )

    second_document = current_rate_document()
    second_document["plan_name"] = "Second live rate plan"
    second_document["plan_code"] = "STATUS-SECOND-LIVE-RATE"
    second_period = second_document["seasons"][0]["schedules"][0]["periods"][0]
    second_period["label"] = "off-peak"
    second_period["price_per_kwh"] = "0.21000000"
    second_account = UtilityAccount(
        site_id=site.id,
        utility_id=utility.id,
        name="Second utility account",
        timezone="UTC",
        currency="USD",
    )
    second_plan = RatePlan(
        utility_id=utility.id,
        code="STATUS-SECOND-LIVE-RATE",
        name="Second live rate plan",
        description="Second current status indicator fixture",
        plan_kind="custom",
        ownership_scope="global",
        currency="USD",
        timezone="UTC",
        status="active",
    )
    session.add_all([second_account, second_plan])
    await session.flush()
    second_version = RateVersion(
        rate_plan_id=second_plan.id,
        version=1,
        effective_from=date(2020, 1, 1),
        effective_to=None,
        timezone="UTC",
        currency="USD",
        source_url="https://example.test/status-second-live-rate",
        source_checked_on=date(2026, 7, 20),
        source_notes="Second deterministic current-rate fixture",
        content_hash="8" * 64,
        immutable_after_use=True,
        is_active=True,
        status="active",
        source_kind="custom",
        normalized_payload=second_document,
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    session.add(second_version)
    await session.flush()
    second_account.active_rate_version_id = second_version.id
    session.add_all(
        [
            Device(
                site_id=site.id,
                utility_account_id=second_account.id,
                hardware_id="status-second-current-rate-sensor",
                name="Second current rate sensor",
                lifecycle_status="active",
            ),
            RateAssignment(
                utility_account_id=second_account.id,
                rate_version_id=second_version.id,
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                effective_to=None,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
            ),
        ]
    )
    await session.commit()

    multi_response = await api_client.get(f"/api/v1/status-indicators/values?site_id={site.id}")
    assert multi_response.status_code == 200, multi_response.text
    multi_values = multi_response.json()["values"]
    assert multi_values["rate.current_plan"]["display_value"] == "Home SCE account + 1 more"
    assert (
        "Second utility account: Second live rate plan v1"
        in multi_values["rate.current_plan"]["detail"]
    )
    assert multi_values["rate.current_period"]["display_value"] == "Multiple periods"
    assert "Second utility account: Off-peak" in multi_values["rate.current_period"]["detail"]
    assert multi_values["rate.current_price"]["display_value"] == "Multiple rates"
    assert "Second utility account: $0.21/kWh" in multi_values["rate.current_price"]["detail"]


@pytest.mark.asyncio
async def test_layout_validation_conflicts_critical_confirmation_import_export_and_restore(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap_admin(api_client)
    initial = (await api_client.get("/api/v1/admin/status-indicators/draft")).json()
    configuration = initial["configuration"]

    unknown = materialize_configuration(configuration)
    unknown["items"].append(
        {
            "indicator_key": "unknown.status",
            "page": "*",
            "role": "*",
            "breakpoint": "default",
            "visible": True,
            "zone": "global_status_row",
        }
    )
    invalid = await api_client.put(
        "/api/v1/admin/status-indicators/draft",
        headers=csrf(api_client),
        json={"base_revision": initial["base_revision"], "configuration": unknown},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "status_indicator_unknown"
    no_csrf = await api_client.put(
        "/api/v1/admin/status-indicators/draft",
        json={"base_revision": initial["base_revision"], "configuration": configuration},
    )
    assert no_csrf.status_code == 403

    configuration["items"].append(
        {
            **global_item(configuration, "alerts.active_count"),
            "page": "alerts",
            "visible": False,
        }
    )
    saved = await api_client.put(
        "/api/v1/admin/status-indicators/draft",
        headers=csrf(api_client),
        json={
            "base_revision": initial["base_revision"],
            "draft_revision": 0,
            "configuration": configuration,
        },
    )
    assert saved.status_code == 200, saved.text
    saved_body = saved.json()
    assert saved_body["critical_hidden"][0]["page"] == "alerts"
    preview = await api_client.post(
        "/api/v1/admin/status-indicators/preview",
        headers=csrf(api_client),
        json={"page": "overview", "role": "admin", "breakpoint": "desktop"},
    )
    assert preview.status_code == 200
    confirmation = await api_client.post(
        "/api/v1/admin/status-indicators/publish",
        headers=csrf(api_client),
        json={
            "base_revision": saved_body["base_revision"],
            "draft_revision": saved_body["draft_revision"],
            "confirm": True,
            "confirm_critical": False,
        },
    )
    assert confirmation.status_code == 409
    assert confirmation.json()["code"] == "status_layout_critical_confirmation_required"
    publish = await api_client.post(
        "/api/v1/admin/status-indicators/publish",
        headers=csrf(api_client),
        json={
            "base_revision": saved_body["base_revision"],
            "draft_revision": saved_body["draft_revision"],
            "confirm": True,
            "confirm_critical": True,
        },
    )
    assert publish.status_code == 201, publish.text
    revision_two = publish.json()
    stale = await api_client.post(
        "/api/v1/admin/status-indicators/revisions/missing/restore",
        headers=csrf(api_client),
        json={"base_revision": 0, "confirm": True},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "status_layout_revision_conflict"

    exported = await api_client.get("/api/v1/admin/status-indicators/export")
    assert exported.status_code == 200
    document = exported.json()
    assert document["registry_version"] == REGISTRY_VERSION
    assert "values" not in document
    imported = await api_client.post(
        "/api/v1/admin/status-indicators/import",
        headers=csrf(api_client),
        json={
            "schema_version": document["schema_version"],
            "registry_version": document["registry_version"],
            "base_revision": revision_two["revision"],
            "configuration": document["configuration"],
            "reason": "Round-trip profile",
        },
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["requires_preview"] is True
    assert imported.json()["previewed_revision"] is None
    await api_client.delete("/api/v1/admin/status-indicators/draft", headers=csrf(api_client))
    revisions = (await api_client.get("/api/v1/admin/status-indicators/revisions")).json()[
        "revisions"
    ]
    source = next(item for item in revisions if item["revision"] == revision_two["revision"])
    restored = await api_client.post(
        f"/api/v1/admin/status-indicators/revisions/{source['id']}/restore",
        headers=csrf(api_client),
        json={
            "base_revision": revision_two["revision"],
            "reason": "Verify immutable rollback",
            "confirm": True,
            "confirm_critical": True,
        },
    )
    assert restored.status_code == 201, restored.text
    assert restored.json()["revision"] == revision_two["revision"] + 1
    assert restored.json()["restored_from_id"] == source["id"]
