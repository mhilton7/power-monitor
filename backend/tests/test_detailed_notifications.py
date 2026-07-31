from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AlertInstance,
    AlertRule,
    Device,
    NotificationAttempt,
    NotificationChannel,
    NotificationEvent,
    NotificationSuppression,
    Site,
    User,
)
from app.notifications import NOTIFICATION_CATALOG, catalog_entry, load_notification_views

REQUIRED_OPERATIONAL_CODES = {
    "heartbeat_stale",
    "device_api_unreachable",
    "authentication_failure",
    "protocol_incompatible",
    "pzem_failure",
    "no_valid_reading",
    "sd_failure",
    "sync_backlog",
    "sequence_gap",
    "time_untrusted",
    "low_rssi",
    "power_surge",
    "ct_utilization",
    "voltage_frequency_range",
    "reboot_loop",
    "firmware_failure",
    "server_failure",
    "backup_failure",
    "worker_failure",
}


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


def test_notification_catalog_is_actionable_and_protects_operational_alerts() -> None:
    assert NOTIFICATION_CATALOG.keys() >= REQUIRED_OPERATIONAL_CODES
    for code in REQUIRED_OPERATIONAL_CODES:
        entry = catalog_entry(code)
        assert entry.title not in {"Something needs attention", "System issue"}
        assert len(entry.summary) >= 20
        assert len(entry.impact) >= 20
        assert entry.remediation_summary
        assert entry.remediation_steps
        assert entry.permanently_suppressible is False

    smtp = catalog_entry("recommendation.smtp_not_configured")
    assert smtp.permanently_suppressible is True
    assert smtp.severity == "info"
    assert smtp.action_permissions == ("alerts.manage_delivery",)


@pytest.mark.asyncio
async def test_structured_views_separate_operational_delivery_and_recommendation(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Notification Home", code="notification-home", is_default=True)
    user = User(
        email="notification-owner@example.com",
        display_name="Notification Owner",
        password_hash="not-used",
    )
    session.add_all([site, user])
    await session.flush()
    device = Device(
        site_id=site.id,
        hardware_id="notification-device",
        name="Indoor-AC",
    )
    rule = AlertRule(
        name="Sensor heartbeat",
        rule_type="heartbeat_stale",
        severity="error",
        site_id=site.id,
        debounce_seconds=60,
        resolve_seconds=30,
        configuration={"stale_after_seconds": 60},
    )
    session.add_all([device, rule])
    await session.flush()
    alert = AlertInstance(
        rule_id=rule.id,
        device_id=device.id,
        site_id=site.id,
        status="active",
        severity="error",
        opened_at=now - timedelta(minutes=4),
        last_seen_at=now,
        occurrence_count=3,
        evidence={
            "last_seen_at": (now - timedelta(seconds=92)).isoformat(),
            "last_known_power_watts": "842.6",
            "stale_after_seconds": 60,
            "smtp_password": "must-never-appear",
        },
    )
    channel = NotificationChannel(
        name="Home email",
        channel_type="smtp",
        encrypted_config=b"encrypted",
        enabled=False,
    )
    session.add_all([alert, channel])
    await session.flush()
    session.add(
        NotificationAttempt(
            alert_instance_id=alert.id,
            channel_id=channel.id,
            attempted_at=now,
            queued_at=now - timedelta(seconds=2),
            started_at=now - timedelta(seconds=1),
            completed_at=now,
            status="failed",
            attempt_number=2,
            safe_error_code="smtp_starttls_failed",
            safe_error_summary="STARTTLS negotiation failed",
            response_summary="STARTTLS negotiation failed",
            next_attempt_at=now + timedelta(minutes=5),
            is_test=False,
        )
    )
    await session.commit()

    views = await load_notification_views(
        session,
        user_id=user.id,
        permissions={"alerts.view", "alerts.manage_delivery", "devices.view"},
        all_sites=True,
        site_ids=set(),
        requested_site_id=site.id,
    )
    operational = next(item for item in views if item.kind == "operational_alert")
    delivery = next(item for item in views if item.kind == "delivery_issue")
    recommendation = next(item for item in views if item.kind == "setup_recommendation")

    assert operational.title == "Indoor-AC stopped reporting"
    assert operational.affected_resource and operational.affected_resource.name == "Indoor-AC"
    assert operational.occurrence_count == 3
    assert operational.observed and "T" in operational.observed.value
    assert operational.expected and operational.expected.value == "60"
    assert operational.impact
    assert operational.remediation.steps
    assert "must-never-appear" not in operational.model_dump_json()
    assert operational.suppression.permanently_suppressible is False

    assert delivery.title == "Email delivery failed"
    assert delivery.delivery and delivery.delivery.safe_error_code == "smtp_starttls_failed"
    assert delivery.affected_resource and delivery.affected_resource.name == "Home email"
    assert delivery.suppression.permanently_suppressible is False

    assert recommendation.code == "recommendation.smtp_not_configured"
    assert recommendation.suppression.permanently_suppressible is True
    assert recommendation.suppression.allowed_scopes == ["user", "home"]

    viewer_views = await load_notification_views(
        session,
        user_id=user.id,
        permissions={"alerts.view"},
        all_sites=True,
        site_ids=set(),
        requested_site_id=site.id,
    )
    assert all(item.kind != "setup_recommendation" for item in viewer_views)


@pytest.mark.asyncio
async def test_smtp_recommendation_suppression_is_persistent_reversible_and_audited(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "notifications@example.com",
            "display_name": "Notification Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    assert bootstrap.status_code == 201
    sites = (await client.get("/api/v1/sites")).json()
    site_id = sites[0]["id"]
    alerts = (await client.get(f"/api/v1/notifications?site_id={site_id}")).json()["items"]
    recommendation = next(
        item for item in alerts if item["code"] == "recommendation.smtp_not_configured"
    )
    assert recommendation["kind"] == "setup_recommendation"

    rejected = await client.post(
        "/api/v1/notifications/not-an-optional-recommendation/suppress",
        headers=csrf(client),
        json={"scope": "home", "reason": "No", "confirmed": True},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "notification_not_suppressible"

    suppressed = await client.post(
        f"/api/v1/notifications/{recommendation['id']}/suppress",
        headers=csrf(client),
        json={"scope": "home", "reason": "Dashboard alerts are sufficient", "confirmed": True},
    )
    assert suppressed.status_code == 201, suppressed.text
    repeated = await client.post(
        f"/api/v1/notifications/{recommendation['id']}/suppress",
        headers=csrf(client),
        json={"scope": "home", "reason": "Repeated request", "confirmed": True},
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == suppressed.json()["id"]

    active = (await client.get(f"/api/v1/notifications?site_id={site_id}")).json()["items"]
    assert all(item["code"] != "recommendation.smtp_not_configured" for item in active)
    ignored = (await client.get("/api/v1/notification-suppressions")).json()
    assert len(ignored) == 1
    assert ignored[0]["scope_type"] == "home"
    assert ignored[0]["reason"] == "Dashboard alerts are sufficient"

    async with session_factory_fixture() as session:
        stored = list(await session.scalars(select(NotificationSuppression)))
        assert len(stored) == 1 and stored[0].active is True
        events = list(await session.scalars(select(NotificationEvent)))
        assert any(item.event_type == "permanently_suppressed" for item in events)

    restored = await client.delete(
        f"/api/v1/notification-suppressions/{ignored[0]['id']}"
        f"?expected_revision={ignored[0]['revision']}",
        headers=csrf(client),
    )
    assert restored.status_code == 204
    returned = (await client.get(f"/api/v1/notifications?site_id={site_id}")).json()["items"]
    assert any(item["code"] == "recommendation.smtp_not_configured" for item in returned)


@pytest.mark.asyncio
async def test_acknowledge_and_silence_preserve_active_condition(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "lifecycle@example.com",
            "display_name": "Lifecycle Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    async with session_factory_fixture() as session:
        site = await session.scalar(select(Site).where(Site.is_default.is_(True)))
        assert site
        rule = AlertRule(
            name="Backup verification",
            rule_type="backup_failure",
            severity="critical",
            site_id=site.id,
            configuration={},
        )
        session.add(rule)
        await session.flush()
        alert = AlertInstance(
            rule_id=rule.id,
            site_id=site.id,
            status="active",
            severity="critical",
            opened_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
            evidence={"failed_stage": "Checksum verification"},
        )
        session.add(alert)
        await session.commit()
        alert_id = alert.id
        site_id = site.id

    acknowledged = await client.post(
        f"/api/v1/notifications/{alert_id}/acknowledge",
        headers=csrf(client),
        json={"note": "Investigating backup storage"},
    )
    assert acknowledged.status_code == 200
    repeated = await client.post(
        f"/api/v1/notifications/{alert_id}/acknowledge",
        headers=csrf(client),
        json={"note": "Duplicate acknowledgement"},
    )
    assert repeated.status_code == 200

    past = await client.post(
        f"/api/v1/notifications/{alert_id}/silence",
        headers=csrf(client),
        json={"until": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(), "note": ""},
    )
    assert past.status_code == 422
    until = datetime.now(UTC) + timedelta(hours=1)
    silenced = await client.post(
        f"/api/v1/notifications/{alert_id}/silence",
        headers=csrf(client),
        json={"until": until.isoformat(), "note": "Maintenance window"},
    )
    assert silenced.status_code == 200
    views = (await client.get(f"/api/v1/notifications?site_id={site_id}")).json()["items"]
    current = next(item for item in views if item["id"] == alert_id)
    assert current["state"] == "silenced"
    assert current["resolved_at"] is None

    ended = await client.delete(f"/api/v1/notifications/{alert_id}/silence", headers=csrf(client))
    assert ended.status_code == 204
    async with session_factory_fixture() as session:
        stored = await session.get(AlertInstance, alert_id)
        assert stored and stored.status == "acknowledged" and stored.resolved_at is None
        assert stored.acknowledged_at is not None
        assert stored.silenced_until is None


@pytest.mark.asyncio
async def test_remove_hides_one_users_notification_until_the_condition_updates(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    client: httpx.AsyncClient = api_client
    await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "remove-notification@example.com",
            "display_name": "Notification Owner",
            "password": "Long-Production-Password-42!",
        },
    )
    async with session_factory_fixture() as session:
        site = await session.scalar(select(Site).where(Site.is_default.is_(True)))
        assert site
        rule = AlertRule(
            name="Old sensor warning",
            rule_type="heartbeat_stale",
            severity="warning",
            site_id=site.id,
            configuration={"stale_after_seconds": 60},
        )
        session.add(rule)
        await session.flush()
        alert = AlertInstance(
            rule_id=rule.id,
            site_id=site.id,
            status="active",
            severity="warning",
            opened_at=datetime.now(UTC) - timedelta(days=1),
            last_seen_at=datetime.now(UTC) - timedelta(days=1),
            evidence={"stale_after_seconds": 60},
        )
        session.add(alert)
        await session.commit()
        alert_id = alert.id
        site_id = site.id

    before = (await client.get(f"/api/v1/notifications?site_id={site_id}")).json()["items"]
    item = next(entry for entry in before if entry["id"] == alert_id)
    assert item["suppression"]["dismissible"] is True
    removed = await client.post(
        f"/api/v1/notifications/{alert_id}/dismiss",
        headers=csrf(client),
    )
    assert removed.status_code == 201, removed.text
    assert removed.json()["dismissed"] is True
    after = (await client.get(f"/api/v1/notifications?site_id={site_id}")).json()["items"]
    assert all(entry["id"] != alert_id for entry in after)

    async with session_factory_fixture() as session:
        event = await session.scalar(
            select(NotificationEvent).where(
                NotificationEvent.notification_id == alert_id,
                NotificationEvent.event_type == "dismissed",
            )
        )
        assert event and event.details["monitoring_preserved"] is True
        stored = await session.get(AlertInstance, alert_id)
        assert stored
        stored.last_seen_at = datetime.now(UTC) + timedelta(seconds=1)
        await session.commit()

    returned = (await client.get(f"/api/v1/notifications?site_id={site_id}")).json()["items"]
    assert any(entry["id"] == alert_id for entry in returned)
