from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker.app.tasks import process_notification_jobs, process_report_jobs

from app.config import Settings
from app.db.models import GeneratedReport, NotificationAttempt


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


@pytest.mark.asyncio
async def test_notification_and_report_job_lifecycle(
    api_client: Any,
    session_factory_fixture: async_sessionmaker[AsyncSession],
    test_settings: Settings,
) -> None:
    client: httpx.AsyncClient = api_client
    bootstrap = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "operations@example.com",
            "display_name": "Operations",
            "password": "Long-Production-Password-42!",
        },
    )
    assert bootstrap.status_code == 201
    channel = await client.post(
        "/api/v1/notification-channels",
        headers=csrf(client),
        json={
            "name": "In application",
            "channel_type": "in_app",
            "enabled": True,
            "configuration": {},
        },
    )
    assert channel.status_code == 201, channel.text
    assert channel.json()["secrets_redacted"] is True
    assert "configuration" not in channel.json()
    test_attempt = await client.post(
        f"/api/v1/notification-channels/{channel.json()['id']}/test",
        headers=csrf(client),
    )
    assert test_attempt.status_code == 202
    definition = await client.post(
        "/api/v1/report-definitions",
        headers=csrf(client),
        json={
            "name": "Daily coverage",
            "report_type": "daily_summary",
            "configuration": {},
        },
    )
    assert definition.status_code == 201, definition.text
    queued_report = await client.post(
        f"/api/v1/report-definitions/{definition.json()['id']}/generate",
        headers=csrf(client),
    )
    assert queued_report.status_code == 202

    async with session_factory_fixture() as session:
        notifications = await process_notification_jobs(session, test_settings)
        reports = await process_report_jobs(session, test_settings)
        attempt = await session.get(NotificationAttempt, test_attempt.json()["attempt_id"])
        report = await session.get(GeneratedReport, queued_report.json()["id"])
        assert notifications == {"delivered": 1, "failed": 0, "processed": 1}
        assert attempt is not None and attempt.status == "delivered"
        assert reports == 1
        assert report is not None and report.status == "completed"
        assert report.data_coverage["coverage_percent"] == "0"

    downloaded = await client.get(f"/api/v1/reports/{queued_report.json()['id']}/download")
    assert downloaded.status_code == 200
    assert downloaded.json()["quality"]["estimated"] is True


@pytest.mark.asyncio
async def test_alert_rule_and_maintenance_management(api_client: Any) -> None:
    client: httpx.AsyncClient = api_client
    await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "admin@example.com",
            "display_name": "Admin",
            "password": "Long-Production-Password-42!",
        },
    )
    rule = await client.post(
        "/api/v1/alert-rules",
        headers=csrf(client),
        json={
            "name": "Scoped backlog",
            "rule_type": "sync_backlog",
            "severity": "warning",
            "debounce_seconds": 30,
            "resolve_seconds": 60,
            "configuration": {"records": 100},
        },
    )
    assert rule.status_code == 201, rule.text
    rules = await client.get("/api/v1/alert-rules")
    assert any(item["id"] == rule.json()["id"] for item in rules.json())
    deleted = await client.delete(f"/api/v1/alert-rules/{rule.json()['id']}", headers=csrf(client))
    assert deleted.status_code == 204
