from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

import app.sensor_test_mode as test_mode_module
from app.db.models import (
    AlertInstance,
    AuditEvent,
    BackupRun,
    CostCalculationRun,
    CostIntervalResult,
    Device,
    DeviceCredential,
    FirmwareDeployment,
    FirmwareRelease,
    GeneratedReport,
    RawReading,
    UtilityBillImport,
)
from app.main import app
from app.schemas import SensorTestModeUpdate, SensorTestModeWrite
from app.sensor_test_mode import SensorTestModeManager, sensor_test_mode


def csrf_headers(client: Any) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: Any) -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "health-owner@example.com",
            "display_name": "Health Owner",
            "password": "Correct-Horse-Battery-Staple-42!",
        },
    )
    assert response.status_code == 201, response.text


async def test_system_health_is_owner_only_typed_and_secret_safe(api_client: Any) -> None:
    response = await api_client.get("/api/v1/system/health")
    assert response.status_code == 401

    await bootstrap(api_client)
    response = await api_client.get(
        "/api/v1/system/health",
        headers={"X-Power-Monitor-Frontend-Version": "1.0.0"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == "system-health/1.0"
    assert payload["status"] in {"healthy", "degraded", "unhealthy", "unknown"}
    assert {item["key"] for item in payload["components"]} == {
        "api",
        "database",
        "worker",
        "storage",
        "backups",
        "live_data",
        "rate_engine",
    }
    assert payload["versions"]["frontend"] == "1.0.0"
    serialized = response.text.lower()
    assert "session_pepper" not in serialized
    assert "app_master_key" not in serialized
    assert "database_url" not in serialized
    assert "/data/" not in serialized


async def test_sensor_test_mode_is_ephemeral_isolated_and_cleaned_up(
    api_client: Any,
    session: Any,
) -> None:
    await sensor_test_mode.shutdown()
    await bootstrap(api_client)
    headers = csrf_headers(api_client)
    before = {
        "devices": await session.scalar(select(func.count()).select_from(Device)),
        "credentials": await session.scalar(select(func.count()).select_from(DeviceCredential)),
        "readings": await session.scalar(select(func.count()).select_from(RawReading)),
        "bills": await session.scalar(select(func.count()).select_from(UtilityBillImport)),
        "cost_runs": await session.scalar(select(func.count()).select_from(CostCalculationRun)),
        "cost_intervals": await session.scalar(
            select(func.count()).select_from(CostIntervalResult)
        ),
        "alerts": await session.scalar(select(func.count()).select_from(AlertInstance)),
        "backups": await session.scalar(select(func.count()).select_from(BackupRun)),
        "exports": await session.scalar(select(func.count()).select_from(GeneratedReport)),
        "firmware_releases": await session.scalar(
            select(func.count()).select_from(FirmwareRelease)
        ),
        "firmware_deployments": await session.scalar(
            select(func.count()).select_from(FirmwareDeployment)
        ),
    }

    rejected = await api_client.post(
        "/api/v1/test-mode/enable",
        json={
            "sensor_count": 2,
            "load_profile": "steady",
            "offline_sensor_indexes": [],
            "sample_interval_seconds": 1,
            "expires_in_minutes": 5,
            "cost_preview_enabled": False,
            "idempotency_key": "enable-without-csrf",
        },
    )
    assert rejected.status_code == 403
    assert rejected.json()["code"] == "csrf_failed"

    enable_payload = {
        "sensor_count": 3,
        "load_profile": "evening_peak",
        "offline_sensor_indexes": [2],
        "sample_interval_seconds": 1,
        "expires_in_minutes": 5,
        "cost_preview_enabled": False,
        "idempotency_key": "enable-isolated-test-mode",
    }
    response = await api_client.post(
        "/api/v1/test-mode/enable",
        json=enable_payload,
        headers=headers,
    )
    assert response.status_code == 200, response.text
    state = response.json()
    assert state["enabled"] is True
    assert state["sensor_count"] == 3
    assert state["online_sensors"] == 2
    assert state["offline_sensors"] == 1
    assert state["source_type"] == "simulated"
    assert state["environment"] == "test_mode"
    assert state["cost_preview_enabled"] is False
    assert all(state["isolation"].values())

    duplicate = await api_client.post(
        "/api/v1/test-mode/enable",
        json=enable_payload,
        headers=headers,
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["session_id"] == state["session_id"]

    sensors = await api_client.get("/api/v1/test-mode/sensors")
    assert sensors.status_code == 200
    rows = sensors.json()
    assert len(rows) == 3
    assert rows[1]["online"] is False
    assert all(item["source_type"] == "simulated" for item in rows)
    assert all(item["environment"] == "test_mode" for item in rows)
    assert [item["name"] for item in rows] == [
        "Simulated Sensor 1",
        "Simulated Sensor 2",
        "Simulated Sensor 3",
    ]

    sensor_update = await api_client.put(
        f"/api/v1/test-mode/sensors/{rows[1]['id']}",
        json={
            "offline": False,
            "load_override_w": "725.5",
            "idempotency_key": "bring-test-sensor-online",
        },
        headers=headers,
    )
    assert sensor_update.status_code == 200
    assert sensor_update.json()["online"] is True
    assert sensor_update.json()["load_override_w"] == "725.5"

    history = await api_client.get("/api/v1/test-mode/history")
    assert history.status_code == 200
    assert history.json()
    assert all(item["source_type"] == "simulated" for item in history.json())

    after = {
        "devices": await session.scalar(select(func.count()).select_from(Device)),
        "credentials": await session.scalar(select(func.count()).select_from(DeviceCredential)),
        "readings": await session.scalar(select(func.count()).select_from(RawReading)),
        "bills": await session.scalar(select(func.count()).select_from(UtilityBillImport)),
        "cost_runs": await session.scalar(select(func.count()).select_from(CostCalculationRun)),
        "cost_intervals": await session.scalar(
            select(func.count()).select_from(CostIntervalResult)
        ),
        "alerts": await session.scalar(select(func.count()).select_from(AlertInstance)),
        "backups": await session.scalar(select(func.count()).select_from(BackupRun)),
        "exports": await session.scalar(select(func.count()).select_from(GeneratedReport)),
        "firmware_releases": await session.scalar(
            select(func.count()).select_from(FirmwareRelease)
        ),
        "firmware_deployments": await session.scalar(
            select(func.count()).select_from(FirmwareDeployment)
        ),
    }
    assert after == before

    disabled = await api_client.post(
        "/api/v1/test-mode/disable",
        json={"idempotency_key": "disable-and-clean-up-test-mode"},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["total_energy_kwh"] == "0"
    assert disabled.json()["sensor_count"] == 0

    no_history = await api_client.get("/api/v1/test-mode/history")
    assert no_history.status_code == 409
    assert no_history.json()["code"] == "test_mode_disabled"

    audit_actions = set(
        (
            await session.scalars(
                select(AuditEvent.action).where(AuditEvent.action.like("sensor_test_mode.%"))
            )
        ).all()
    )
    assert {
        "sensor_test_mode.enabled",
        "sensor_test_mode.sensor_updated",
        "sensor_test_mode.disabled",
    } <= audit_actions


async def test_sensor_test_mode_count_profiles_pause_reset_and_owner_authorization(
    api_client: Any,
) -> None:
    await sensor_test_mode.shutdown()
    await bootstrap(api_client)
    headers = csrf_headers(api_client)
    created = await api_client.post(
        "/api/v1/users",
        headers=headers,
        json={
            "email": "test-mode-viewer@example.com",
            "display_name": "Test Mode Viewer",
            "password": "Production-Viewer-Password-42!",
            "roles": ["viewer"],
        },
    )
    assert created.status_code == 201, created.text
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as viewer:
        login = await viewer.post(
            "/api/v1/auth/login",
            json={
                "email": "test-mode-viewer@example.com",
                "password": "Production-Viewer-Password-42!",
            },
        )
        assert login.status_code == 200
        assert (await viewer.get("/api/v1/system/health")).status_code == 403
        assert (await viewer.get("/api/v1/test-mode")).status_code == 403
        denied = await viewer.post(
            "/api/v1/test-mode/enable",
            headers=csrf_headers(viewer),
            json={
                "sensor_count": 1,
                "idempotency_key": "viewer-cannot-enable-test-mode",
            },
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "test_mode_owner_required"

    for invalid in (-1, 33):
        rejected = await api_client.post(
            "/api/v1/test-mode/enable",
            headers=headers,
            json={
                "sensor_count": invalid,
                "idempotency_key": f"invalid-test-count-{invalid}",
            },
        )
        assert rejected.status_code == 422

    enabled = await api_client.post(
        "/api/v1/test-mode/enable",
        headers=headers,
        json={
            "sensor_count": 1,
            "load_profile": "steady",
            "expires_in_minutes": None,
            "idempotency_key": "enable-until-disabled-one-sensor",
        },
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["expires_at"] is None
    first_id = (await api_client.get("/api/v1/test-mode/sensors")).json()[0]["id"]

    invalid_resize = await api_client.put(
        "/api/v1/test-mode",
        headers=headers,
        json={
            "sensor_count": 0,
            "offline_sensor_indexes": [1],
            "idempotency_key": "invalid-transactional-resize",
        },
    )
    assert invalid_resize.status_code == 422
    unchanged = (await api_client.get("/api/v1/test-mode")).json()
    assert unchanged["sensor_count"] == 1
    assert unchanged["load_profile"] == "steady"

    invalid_custom = await api_client.put(
        "/api/v1/test-mode",
        headers=headers,
        json={
            "load_profile": "custom",
            "idempotency_key": "invalid-custom-without-load",
        },
    )
    assert invalid_custom.status_code == 422
    unchanged = (await api_client.get("/api/v1/test-mode")).json()
    assert unchanged["sensor_count"] == 1
    assert unchanged["load_profile"] == "steady"

    for sequence, count in enumerate((5, 32, 5, 0, 1), start=1):
        updated = await api_client.put(
            "/api/v1/test-mode",
            headers=headers,
            json={
                "sensor_count": count,
                "idempotency_key": f"resize-test-mode-{sequence}-{count}",
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["sensor_count"] == count
        rows = (await api_client.get("/api/v1/test-mode/sensors")).json()
        assert len(rows) == count
        if count:
            assert rows[0]["id"] == first_id

    for profile in (
        "steady",
        "variable_household",
        "morning_evening_peaks",
        "high_load",
        "low_load",
        "custom",
    ):
        payload: dict[str, Any] = {
            "load_profile": profile,
            "base_load_w": "1250",
            "variation_percent": "15",
            "idempotency_key": f"profile-{profile}-test-mode",
        }
        if profile == "custom":
            payload["custom_load_w"] = "2222.5"
        updated = await api_client.put(
            "/api/v1/test-mode",
            headers=headers,
            json=payload,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["load_profile"] == profile

    paused = await api_client.put(
        "/api/v1/test-mode",
        headers=headers,
        json={"paused": True, "idempotency_key": "pause-test-mode-session"},
    )
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    assert paused.json()["current_power_w"] == "0"
    resumed = await api_client.put(
        "/api/v1/test-mode",
        headers=headers,
        json={"paused": False, "idempotency_key": "resume-test-mode-session"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False

    reset = await api_client.post(
        "/api/v1/test-mode/reset",
        headers=headers,
        json={"idempotency_key": "reset-test-mode-history"},
    )
    assert reset.status_code == 200
    assert reset.json()["total_energy_kwh"] == "0"
    assert (await api_client.get("/api/v1/test-mode/history")).json() == []

    await api_client.post(
        "/api/v1/test-mode/disable",
        headers=headers,
        json={"idempotency_key": "disable-profile-test-mode"},
    )


async def test_sensor_test_mode_auto_expiry_clears_ephemeral_state(
    monkeypatch: Any,
) -> None:
    clock = {"now": datetime(2026, 7, 26, 12, 0, tzinfo=UTC)}
    monkeypatch.setattr(test_mode_module, "_utc_now", lambda: clock["now"])
    manager = SensorTestModeManager()
    enabled = await manager.enable(
        SensorTestModeWrite(
            sensor_count=5,
            load_profile="variable_household",
            sample_interval_seconds=60,
            expires_in_minutes=5,
            idempotency_key="direct-expiry-test-mode",
        )
    )
    assert enabled["enabled"] is True
    original_ids = [item["id"] for item in await manager.sensors()]
    await manager.update(
        SensorTestModeUpdate(
            sensor_count=1,
            idempotency_key="direct-expiry-resize-test",
        )
    )
    assert (await manager.sensors())[0]["id"] == original_ids[0]
    clock["now"] += timedelta(minutes=6)
    expired = await manager.state()
    assert expired["enabled"] is False
    assert expired["end_reason"] == "expired"
    assert expired["sensor_count"] == 0
    expiry_event = await manager.consume_expiry_audit()
    assert expiry_event is not None
    assert expiry_event["session_id"] == enabled["session_id"]
    assert expiry_event["ended_at"] == clock["now"].isoformat()
    assert expiry_event["discarded_sensor_count"] == 1
    assert expiry_event["discarded_history_points"] >= 1
    assert expiry_event["real_data_changed"] is False
    await manager.shutdown()
