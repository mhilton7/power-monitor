from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from worker.app.tasks import process_cost_jobs

from app.db.models import (
    AggregateMember,
    AggregateSet,
    BillingCycle,
    CostCalculationRun,
    CostIntervalResult,
    Device,
    NormalizedInterval,
    RawReading,
    UtilityAccountAdjustment,
)
from app.network_policy import effective_client_ip
from app.security.protocol import PROTOCOL, sign_headers


def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    value = client.cookies.get("pm_csrf")
    assert value
    return {"X-CSRF-Token": value}


async def bootstrap(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={
            "bootstrap_secret": "test-bootstrap-secret-with-at-least-16",
            "email": "network-admin@example.com",
            "display_name": "Network Administrator",
            "password": "Long-Production-Password-42!",
        },
    )
    assert response.status_code == 201, response.text


def account_payload(version_id: str, name: str = "Main electric account") -> dict[str, Any]:
    return {
        "name": name,
        "nickname": "House meter",
        "account_number_suffix": "1234",
        "utility_provider": "sce",
        "generation_provider": "sce",
        "provider_mode": "sce_bundled",
        "billing_cycle_start_day": 17,
        "currency": "USD",
        "service_class": "Residential",
        "rate_assignment": {
            "rate_version_id": version_id,
            "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "assignment_reason": "Initial setup fixture",
        },
        "cost_scope": "energy_only",
        "adjustments": [],
        "confirmation": True,
    }


@pytest.mark.asyncio
async def test_switching_active_rate_closes_previous_window_and_preserves_history(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    site = (await api_client.get("/api/v1/sites")).json()[0]
    plans = (await api_client.get("/api/v1/rates/plans")).json()
    published = [
        (plan, version)
        for plan in plans
        for version in plan["versions"]
        if version["status"] in {"active", "approved"}
    ]
    assert len(published) >= 2
    first_plan, first_version = published[0]
    second_plan, second_version = next(
        (plan, version) for plan, version in published[1:] if version["id"] != first_version["id"]
    )
    created = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=account_payload(first_version["id"], "Rate switch fixture"),
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["rate_context"]["current_plan"] == first_plan["name"]

    switched_at = datetime.now(UTC)
    switched = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/rate-assignments",
        headers=csrf(api_client),
        json={
            "rate_version_id": second_version["id"],
            "effective_from": switched_at.isoformat(),
            "assignment_reason": "Administrator selected a new active rate plan",
            "replace_current": True,
        },
    )
    assert switched.status_code == 201, switched.text
    result = switched.json()
    assert result["effective_now"] is True
    assert len(result["replaced_assignment_ids"]) == 1

    current = await api_client.get(f"/api/v1/admin/utility-accounts/{account['id']}")
    assert current.status_code == 200, current.text
    assert current.json()["rate_context"]["current_plan"] == second_plan["name"]
    assert current.json()["rate_context"]["rate_version_id"] == second_version["id"]

    history = (
        await api_client.get(f"/api/v1/admin/utility-accounts/{account['id']}/rate-assignments")
    ).json()
    assert len(history) == 2
    previous = next(item for item in history if item["rate_version_id"] == first_version["id"])
    replacement = next(item for item in history if item["rate_version_id"] == second_version["id"])
    assert datetime.fromisoformat(previous["effective_to"]) == datetime.fromisoformat(
        replacement["effective_from"]
    )
    assert replacement["effective_to"] is None

    audits = (await api_client.get("/api/v1/audit-events")).json()
    replacement_audit = next(
        item for item in audits if item["action"] == "rate_assignment.replaced"
    )
    assert (
        replacement_audit["details"]["replaced_assignment_ids"] == result["replaced_assignment_ids"]
    )


@pytest.mark.asyncio
async def test_guided_account_creation_rate_context_history_and_archive(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    site = (await api_client.get("/api/v1/sites")).json()[0]
    empty_readiness = (await api_client.get(f"/api/v1/sites/{site['id']}/setup-readiness")).json()
    assert empty_readiness["rate_and_cost"]["action"] == (f"/billing/accounts?site={site['id']}")
    plans = (await api_client.get("/api/v1/rates/plans")).json()
    version = next(
        version
        for plan in plans
        for version in plan["versions"]
        if version["status"] in {"active", "approved"}
    )
    created = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=account_payload(version["id"]),
    )
    assert created.status_code == 201, created.text
    account = created.json()
    assert account["timezone"] == site["timezone"]
    assert account["cost_scope"] == "energy_only"
    assert account["rate_context"]["state"] == "rate_configured_effective"
    assert account["rate_context"]["current_plan"]
    assert account["rate_context"]["current_period"]
    assert account["rate_context"]["current_price_per_kwh"]
    assert account["rate_context"]["next_period"]
    assert account["rate_context"]["next_price_per_kwh"]
    assert account["rate_context"]["next_period_at"]
    assert account["rate_context"]["billing_cycle"]["starts_at"]
    assert account["rate_context"]["billing_cycle"]["ends_at"]
    assert account["readiness"]["cost"] == "cost_blocked_missing_readings"

    second = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json={
            **account_payload(version["id"], "Detached building"),
            "utility_provider": "custom",
            "generation_provider": "custom",
            "provider_mode": "custom_combined",
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["utility_name"] == "Custom/manual provider"
    listed = await api_client.get(f"/api/v1/admin/sites/{site['id']}/utility-accounts")
    assert len(listed.json()) == 2

    invalid_billing = account_payload(version["id"], "Invalid billing")
    invalid_billing["billing_cycle_start_day"] = 32
    rejected_billing = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=invalid_billing,
    )
    assert rejected_billing.status_code == 422
    unavailable_rate = account_payload("missing-rate-version", "Missing rate")
    rejected_rate = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=unavailable_rate,
    )
    assert rejected_rate.status_code == 422
    assert rejected_rate.json()["code"] == "rate_version_unavailable"

    complete_scope = account_payload(version["id"], "Incomplete whole account")
    complete_scope["cost_scope"] = "full_account_estimate"
    complete_scope["full_account_override"] = False
    rejected_scope = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=complete_scope,
    )
    assert rejected_scope.status_code == 422
    assert rejected_scope.json()["code"] == "incomplete_account_topology"

    edited = await api_client.put(
        f"/api/v1/admin/utility-accounts/{account['id']}",
        headers=csrf(api_client),
        json={"revision": account["revision"], "name": "Upland primary account"},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "Upland primary account"
    stale_edit = await api_client.put(
        f"/api/v1/admin/utility-accounts/{account['id']}",
        headers=csrf(api_client),
        json={"revision": account["revision"], "name": "Stale change"},
    )
    assert stale_edit.status_code == 409
    allocated = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/cost-scope",
        headers=csrf(api_client),
        json={
            "revision": edited.json()["revision"],
            "cost_scope": "allocated_account_estimate",
            "allocation_method": "Proportional to monitored kWh",
        },
    )
    assert allocated.status_code == 200, allocated.text
    assert allocated.json()["allocation_method"] == "Proportional to monitored kWh"
    adjustment = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/adjustments",
        headers=csrf(api_client),
        json={
            "component": "custom_per_kwh",
            "value": "0.01250000",
            "unit": "per_kwh",
            "provenance": "Administrator contract fixture",
            "effective_from": datetime.now(UTC).isoformat(),
        },
    )
    assert adjustment.status_code == 201, adjustment.text
    listed_adjustments = await api_client.get(
        f"/api/v1/admin/utility-accounts/{account['id']}/adjustments"
    )
    assert listed_adjustments.json()[0]["provenance"] == "Administrator contract fixture"

    overlap = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/rate-assignments",
        headers=csrf(api_client),
        json={
            "rate_version_id": version["id"],
            "effective_from": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
            "assignment_reason": "Overlapping fixture",
        },
    )
    assert overlap.status_code == 409
    assert overlap.json()["code"] == "rate_assignment_overlap"

    future = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/rate-assignments",
        headers=csrf(api_client),
        json={
            "rate_version_id": version["id"],
            "effective_from": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "assignment_reason": "Scheduled annual update",
        },
    )
    assert future.status_code == 201, future.text
    assert future.json()["effective_now"] is False
    history = await api_client.get(
        f"/api/v1/admin/utility-accounts/{account['id']}/rate-assignments"
    )
    assert len(history.json()) == 2

    archived = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account['id']}/archive",
        headers=csrf(api_client),
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["assignment_count"] == 2
    audits = await api_client.get("/api/v1/audit-events")
    actions = {event["action"] for event in audits.json()}
    assert {
        "utility_account.created",
        "rate_assignment.created",
        "rate_assignment.scheduled",
        "utility_account.archived",
    }.issubset(actions)


@pytest.mark.asyncio
async def test_explicit_network_modes_cidr_validation_and_enrollment_lockdown(
    api_client: httpx.AsyncClient,
) -> None:
    await bootstrap(api_client)
    site = (await api_client.get("/api/v1/sites")).json()[0]
    policies_response = await api_client.get("/api/v1/admin/network/policies")
    assert policies_response.status_code == 200, policies_response.text
    policies = policies_response.json()
    ingress = next(item for item in policies if item["direction"] == "device_ingress")
    pull = next(item for item in policies if item["direction"] == "server_pull")
    assert ingress["mode"] == "legacy_authenticated_any"
    assert ingress["migration_notice_pending"] is True
    assert pull["mode"] == "deny_all"
    assert "denied" in pull["effective_summary"].lower()

    blocked = await api_client.post(
        "/api/v1/admin/network/test-address",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "address": "192.168.50.42"},
    )
    assert blocked.json()["allowed"] is False

    public = await api_client.post(
        "/api/v1/admin/network/cidrs",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "network": "8.8.8.0/24", "label": "Public"},
    )
    assert public.status_code == 422
    assert public.json()["code"] == "public_cidr_blocked"
    loopback = await api_client.post(
        "/api/v1/admin/network/cidrs",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "network": "127.0.0.0/8", "label": "Loopback"},
    )
    assert loopback.status_code == 422

    added = await api_client.post(
        "/api/v1/admin/network/cidrs",
        headers=csrf(api_client),
        json={
            "policy_id": pull["id"],
            "network": "192.168.50.42/24",
            "label": "IoT VLAN",
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["network"] == "192.168.50.0/24"
    updated = await api_client.put(
        f"/api/v1/admin/network/policies/{pull['id']}",
        headers=csrf(api_client),
        json={
            "revision": pull["revision"] + 1,
            "mode": "allow_listed_private",
            "reason": "Segmented sensor VLAN",
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["migration_notice_pending"] is False

    edited_cidr = await api_client.put(
        f"/api/v1/admin/network/cidrs/{added.json()['id']}",
        headers=csrf(api_client),
        json={
            "policy_id": pull["id"],
            "network": "192.168.50.0/24",
            "label": "Dedicated sensor VLAN",
            "enabled": True,
            "revision": added.json()["revision"],
        },
    )
    assert edited_cidr.status_code == 200, edited_cidr.text
    disable_last = await api_client.put(
        f"/api/v1/admin/network/cidrs/{added.json()['id']}",
        headers=csrf(api_client),
        json={
            "policy_id": pull["id"],
            "network": "192.168.50.0/24",
            "label": "Dedicated sensor VLAN",
            "enabled": False,
            "revision": edited_cidr.json()["revision"],
        },
    )
    assert disable_last.status_code == 409
    assert disable_last.json()["code"] == "network_cidr_required"

    duplicate = await api_client.post(
        "/api/v1/admin/network/cidrs",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "network": "192.168.50.99/24", "label": "Duplicate"},
    )
    assert duplicate.status_code == 409
    overlapping = await api_client.post(
        "/api/v1/admin/network/cidrs",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "network": "192.168.50.128/25", "label": "Overlap"},
    )
    assert overlapping.status_code == 201
    assert overlapping.json()["warnings"]
    host_rule = await api_client.post(
        "/api/v1/admin/network/cidrs",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "network": "192.168.51.42/32", "label": "One sensor"},
    )
    assert host_rule.status_code == 201, host_rule.text
    host_allowed = await api_client.post(
        "/api/v1/admin/network/test-address",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "address": "192.168.51.42"},
    )
    assert host_allowed.json()["allowed"] is True
    ipv6 = await api_client.post(
        "/api/v1/admin/network/cidrs",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "network": "fd42:1234::99/64", "label": "IPv6 sensor VLAN"},
    )
    assert ipv6.status_code == 201
    assert ipv6.json()["network"] == "fd42:1234::/64"
    for invalid_network in ("169.254.0.0/16", "224.0.0.0/4", "not-a-cidr"):
        invalid = await api_client.post(
            "/api/v1/admin/network/cidrs",
            headers=csrf(api_client),
            json={"policy_id": pull["id"], "network": invalid_network, "label": "Invalid"},
        )
        assert invalid.status_code == 422

    allowed = await api_client.post(
        "/api/v1/admin/network/test-address",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "address": "::ffff:192.168.50.99"},
    )
    assert allowed.json()["allowed"] is True
    assert allowed.json()["address"] == "192.168.50.99"
    outside = await api_client.post(
        "/api/v1/admin/network/test-address",
        headers=csrf(api_client),
        json={"policy_id": pull["id"], "address": "192.168.60.10"},
    )
    assert outside.json()["allowed"] is False
    for never_allowed_address in (
        "127.0.0.1",
        "169.254.169.254",
        "224.0.0.1",
        "0.0.0.0",  # noqa: S104 -- deliberate unspecified-address rejection fixture
    ):
        never_allowed = await api_client.post(
            "/api/v1/admin/network/test-address",
            headers=csrf(api_client),
            json={"policy_id": pull["id"], "address": never_allowed_address},
        )
        assert never_allowed.json()["allowed"] is False
    removed_overlap = await api_client.delete(
        f"/api/v1/admin/network/cidrs/{overlapping.json()['id']}", headers=csrf(api_client)
    )
    assert removed_overlap.status_code == 204
    suggestion = await api_client.get("/api/v1/admin/network/suggest-current")
    assert suggestion.status_code == 200
    assert suggestion.json()["available"] is False

    token = await api_client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(api_client),
        json={"site_id": site["id"], "name": "Network policy sensor"},
    )
    claim = await api_client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": "esp32-policy-alert-0001",
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
            },
        },
    )
    assert claim.status_code == 201, claim.text
    device_id = claim.json()["device_id"]
    secret = claim.json()["enrollment_secret"].encode()
    heartbeat = {
        "protocol_version": PROTOCOL,
        "schema_version": "heartbeat/1.0.0",
        "device_id": device_id,
        "boot_id": "123e4567-e89b-12d3-a456-426614174000",
        "firmware_version": "1.0.0",
        "firmware_build_hash": "abc123",
        "uptime_seconds": 30,
        "reboot_reason": "power_on",
        "current_ip": "192.168.60.10",
        "rssi_dbm": -50,
        "connection_mode": "push",
        "pzem": {"ok": True, "status": "ok"},
        "sd": {"ok": True, "status": "ok"},
        "oldest_stored_sequence": 0,
        "newest_stored_sequence": 0,
        "server_ack_sequence": 0,
        "backlog_estimate": 0,
        "configuration_version": 1,
        "time": {"trusted": True, "source": "sntp"},
        "resources": {"heap": 100000},
        "queue": {"pending": 0},
    }
    heartbeat_body = json.dumps(heartbeat, separators=(",", ":")).encode()
    heartbeat_response = await api_client.post(
        "/api/v1/device-heartbeats",
        content=heartbeat_body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-heartbeats",
                body=heartbeat_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert heartbeat_response.status_code == 200, heartbeat_response.text
    alerts = await api_client.get("/api/v1/alerts?status=active")
    assert any(
        alert["rule_type"] == "device_address_outside_policy" and alert["device_id"] == device_id
        for alert in alerts.json()
    )

    all_private = await api_client.put(
        f"/api/v1/admin/network/policies/{ingress['id']}",
        headers=csrf(api_client),
        json={
            "revision": ingress["revision"],
            "mode": "allow_all_private",
            "reason": "Accept signed sensors from private networks",
        },
    )
    assert all_private.status_code == 200, all_private.text
    private_result = await api_client.post(
        "/api/v1/admin/network/test-address",
        headers=csrf(api_client),
        json={"policy_id": ingress["id"], "address": "10.44.5.8"},
    )
    assert private_result.json()["allowed"] is True
    public_result = await api_client.post(
        "/api/v1/admin/network/test-address",
        headers=csrf(api_client),
        json={"policy_id": ingress["id"], "address": "8.8.8.8"},
    )
    assert public_result.json()["allowed"] is False

    denied = await api_client.put(
        f"/api/v1/admin/network/policies/{ingress['id']}",
        headers=csrf(api_client),
        json={
            "revision": all_private.json()["revision"],
            "mode": "deny_all",
            "reason": "Maintenance lockdown",
        },
    )
    assert denied.status_code == 200, denied.text
    denied_token = await api_client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(api_client),
        json={"site_id": site["id"], "name": "Blocked sensor"},
    )
    assert denied_token.status_code == 409
    assert denied_token.json()["code"] == "enrollment_policy_deny_all"
    blocked_heartbeat = await api_client.post(
        "/api/v1/device-heartbeats",
        content=heartbeat_body,
        headers={
            **sign_headers(
                secret=secret,
                device_id=device_id,
                direction="device-to-server",
                method="POST",
                target="/api/v1/device-heartbeats",
                body=heartbeat_body,
            ),
            "Content-Type": "application/json",
        },
    )
    assert blocked_heartbeat.status_code == 403
    assert blocked_heartbeat.json()["code"] == "device_network_blocked"


@pytest.mark.asyncio
async def test_network_policy_requires_session_and_signature_remains_independent(
    api_client: httpx.AsyncClient,
) -> None:
    policies = await api_client.get("/api/v1/admin/network/policies")
    assert policies.status_code == 401
    unsigned = await api_client.post(
        "/api/v1/device-heartbeats",
        json={"device_id": "not-authenticated"},
    )
    assert unsigned.status_code in {401, 422}


def test_trusted_proxy_chain_rejects_spoofed_forwarded_hops() -> None:
    trusted = "172.16.0.0/12,10.0.0.0/8"
    assert effective_client_ip("172.20.0.4", "198.51.100.9, 192.168.50.42", trusted) == (
        "192.168.50.42"
    )
    assert effective_client_ip("172.20.0.4", "192.168.50.42, 10.2.0.8", trusted) == (
        "192.168.50.42"
    )
    assert effective_client_ip("203.0.113.5", "192.168.50.42", trusted) == "203.0.113.5"


@pytest.mark.asyncio
async def test_account_adjustment_is_consumed_by_cost_worker(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await bootstrap(api_client)
    site = (await api_client.get("/api/v1/sites")).json()[0]
    plans = (await api_client.get("/api/v1/rates/plans")).json()
    version = next(
        version
        for plan in plans
        for version in plan["versions"]
        if version["status"] in {"active", "approved"}
    )
    payload = account_payload(version["id"], "CCA cost account")
    payload["utility_provider"] = "cca"
    payload["generation_provider"] = "cca"
    payload["provider_mode"] = "sce_delivery_cca"
    payload["adjustments"] = [
        {
            "component": "cca_generation",
            "value": "0.05000000",
            "unit": "per_kwh",
            "provenance": "Deterministic test tariff",
            "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "enabled": True,
        },
        {
            "component": "custom_fixed",
            "value": "10.00",
            "unit": "fixed",
            "provenance": "Must not apply to energy-only scope",
            "effective_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            "enabled": True,
        },
    ]
    created = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=payload,
    )
    assert created.status_code == 201, created.text
    account = created.json()

    token = await api_client.post(
        "/api/v1/enrollment-tokens",
        headers=csrf(api_client),
        json={"site_id": site["id"], "name": "Costed sensor"},
    )
    claim = await api_client.post(
        "/api/v1/device-enrollment/claim",
        json={
            "token": token.json()["token"],
            "protocol_version": PROTOCOL,
            "hardware_id": "esp32-cost-adjustment-001",
            "capabilities": {
                "hardware_target": "esp32-s3-pzem004t-v4",
                "pzem_model": "PZEM-004T V4.0",
                "sd_present": True,
            },
        },
    )
    assert claim.status_code == 201, claim.text
    starts_at = datetime.now(UTC) - timedelta(hours=1)
    ends_at = starts_at + timedelta(hours=1)
    async with session_factory_fixture() as session:
        device = await session.get(Device, claim.json()["device_id"])
        assert device is not None
        configured_adjustment = await session.scalar(
            select(UtilityAccountAdjustment).where(
                UtilityAccountAdjustment.utility_account_id == account["id"],
                UtilityAccountAdjustment.component == "cca_generation",
            )
        )
        assert configured_adjustment is not None
        configured_adjustment.effective_from = starts_at + timedelta(minutes=30)
        device.utility_account_id = account["id"]
        aggregate = AggregateSet(
            site_id=site["id"],
            utility_account_id=account["id"],
            name="Energy-only cost fixture",
            cost_scope="energy_only",
            is_default=True,
        )
        session.add(aggregate)
        await session.flush()
        session.add(AggregateMember(aggregate_set_id=aggregate.id, device_id=device.id))
        raw = RawReading(
            device_id=device.id,
            site_id=site["id"],
            sequence=1,
            boot_id="123e4567-e89b-12d3-a456-426614174000",
            interval_start=starts_at,
            interval_end=ends_at,
            time_trusted=True,
            power_avg=Decimal("1000"),
            device_interval_energy_wh=Decimal("1000"),
            energy_method="device_interval",
            ct_rating_amps=Decimal("100"),
            quality_flags=[],
            firmware_version="1.0.0",
            record_hash="cost-adjustment-fixture",
            original_payload=None,
            ingestion_source="push",
            ingested_at=ends_at,
        )
        session.add(raw)
        await session.flush()
        normalized = NormalizedInterval(
            raw_reading_id=raw.id,
            device_id=device.id,
            interval_start=starts_at,
            interval_end=ends_at,
            device_energy_wh=Decimal("1000"),
            server_energy_wh=Decimal("1000"),
            selected_energy_wh=Decimal("1000"),
            selected_method="device_interval",
            validation_result="valid",
            validation_reason="deterministic test",
        )
        session.add(normalized)
        run = CostCalculationRun(
            utility_account_id=account["id"],
            aggregate_set_id=aggregate.id,
            rate_version_id=version["id"],
            input_start=starts_at,
            input_end=ends_at,
            algorithm_version="rate-engine/1.0.0",
            status="queued",
            coverage_percent=Decimal("0"),
            created_at=datetime.now(UTC),
        )
        session.add(run)
        await session.commit()
        assert await process_cost_jobs(session) == 1
        adjustment = await session.scalar(
            select(CostIntervalResult).where(
                CostIntervalResult.run_id == run.id,
                CostIntervalResult.component == "cca_adjustment",
            )
        )
        assert adjustment is not None
        assert adjustment.unrounded_cost == Decimal("0.025000000000")
        assert Decimal(adjustment.adjustment_breakdown["cca_generation"]) == Decimal("0.025")
        fixed = await session.scalar(
            select(CostIntervalResult).where(
                CostIntervalResult.run_id == run.id,
                CostIntervalResult.component == "manual_adjustment",
            )
        )
        assert fixed is None


@pytest.mark.asyncio
async def test_recalculation_preserves_finalized_cycles_and_queues_only_mutable_runs(
    api_client: httpx.AsyncClient,
    session_factory_fixture: async_sessionmaker[AsyncSession],
) -> None:
    await bootstrap(api_client)
    site = (await api_client.get("/api/v1/sites")).json()[0]
    plans = (await api_client.get("/api/v1/rates/plans")).json()
    version = next(
        item
        for plan in plans
        for item in plan["versions"]
        if item["status"] in {"active", "approved"}
    )
    created = await api_client.post(
        f"/api/v1/admin/sites/{site['id']}/utility-accounts",
        headers=csrf(api_client),
        json=account_payload(version["id"], "Recalculation fixture"),
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    now = datetime.now(UTC)
    async with session_factory_fixture() as session:
        aggregate = AggregateSet(
            site_id=site["id"],
            utility_account_id=account_id,
            name="Recalculation aggregate",
            cost_scope="energy_only",
            is_default=True,
        )
        session.add(aggregate)
        await session.flush()
        session.add(
            BillingCycle(
                utility_account_id=account_id,
                starts_at=now - timedelta(days=30),
                ends_at=now,
                explicit_meter_dates=True,
                finalized_at=now,
            )
        )
        protected = CostCalculationRun(
            utility_account_id=account_id,
            aggregate_set_id=aggregate.id,
            rate_version_id=version["id"],
            input_start=now - timedelta(days=2),
            input_end=now - timedelta(days=1),
            algorithm_version="rate-engine/1.0.0",
            status="completed",
            coverage_percent=Decimal("100"),
            created_at=now,
            completed_at=now,
        )
        mutable = CostCalculationRun(
            utility_account_id=account_id,
            aggregate_set_id=aggregate.id,
            rate_version_id=version["id"],
            input_start=now + timedelta(days=1),
            input_end=now + timedelta(days=2),
            algorithm_version="rate-engine/1.0.0",
            status="completed",
            coverage_percent=Decimal("100"),
            created_at=now,
            completed_at=now,
        )
        session.add_all([protected, mutable])
        await session.commit()
        protected_id, mutable_id = protected.id, mutable.id

    response = await api_client.post(
        f"/api/v1/admin/utility-accounts/{account_id}/recalculate",
        headers=csrf(api_client),
    )
    assert response.status_code == 202, response.text
    assert response.json()["queued_runs"] == 1
    async with session_factory_fixture() as session:
        assert (await session.get(CostCalculationRun, protected_id)).status == "completed"  # type: ignore[union-attr]
        assert (await session.get(CostCalculationRun, mutable_id)).status == "queued"  # type: ignore[union-attr]
