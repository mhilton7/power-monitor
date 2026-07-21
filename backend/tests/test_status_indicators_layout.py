from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AlertInstance, AlertRule, Device, DeviceHeartbeat
from app.status_indicators import (
    INDICATOR_DEFINITIONS,
    INDICATOR_REGISTRY,
    PAGES,
    REGISTRY_VERSION,
    ZONES,
    compiled_configuration,
    materialize_configuration,
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
    "firmware.update_state",
    "notifications.delivery_health",
    "rate.current_period",
    "rate.current_plan",
    "rate.current_price",
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
            "money-rate",
            "percent",
            "power",
            "signal",
            "text",
        }
        assert definition.presentations == ("compact", "standard", "detailed")
    config = compiled_configuration()
    normalized, warnings = validate_configuration(config)
    assert len(normalized["items"]) == len(INDICATOR_DEFINITIONS)
    assert not warnings


def test_layout_engine_collapses_empty_zones_and_handles_responsive_counts() -> None:
    permissions = {definition.permission_required for definition in INDICATOR_DEFINITIONS}
    for visible_count in (0, 1, 2, 3, 4, 12, len(INDICATOR_DEFINITIONS)):
        configuration = compiled_configuration()
        eligible = [
            item
            for item in configuration["items"]
            if INDICATOR_REGISTRY[item["indicator_key"]].global_shell_support
            or "overview" in INDICATOR_REGISTRY[item["indicator_key"]].supported_pages
        ]
        for index, item in enumerate(eligible):
            item["visible"] = index < visible_count
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
        assert len(rendered_keys) == min(visible_count, len(eligible))
        assert len(rendered_keys) == len(set(rendered_keys))

    configuration = compiled_configuration()
    mobile = resolve_layout(
        configuration,
        page="overview",
        roles={"viewer"},
        permissions=permissions,
        breakpoint="mobile",
        revision=1,
    )
    assert {zone["key"] for zone in mobile["zones"]} <= {
        "mobile_header",
        "mobile_status_strip",
        "mobile_status_drawer",
    }
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
    offline["zone"] = "page_summary_strip"
    offline["order"] = 5
    configuration["items"].append(
        {
            **global_item(configuration, "device.online_count"),
            "page": "overview",
            "role": "viewer",
            "breakpoint": "mobile",
            "density": "compact",
            "zone": "mobile_status_strip",
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
    assert rendered["device.offline_count"][0] == "page_summary_strip"
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
