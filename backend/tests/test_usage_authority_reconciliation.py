from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AccountUsageAuthority,
    BillingCycle,
    Circuit,
    Device,
    DeviceCapability,
    DeviceHeartbeat,
    Site,
    Utility,
    UtilityAccount,
)
from app.problem import ProblemError
from app.rates.tiered import _missing_sensor_warnings
from app.usage_authority import (
    AuthorityApplyRequest,
    apply_sensor_usage_authority,
    authority_reconciliation_plan,
)


@pytest.mark.asyncio
async def test_stale_authority_id_does_not_hide_two_eligible_service_legs(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Authority reconciliation site")
    utility = Utility(name="Authority reconciliation utility")
    session.add_all([site, utility])
    await session.flush()
    account = UtilityAccount(
        site_id=site.id,
        utility_id=utility.id,
        name="Reconciliation account",
    )
    session.add(account)
    await session.flush()
    circuits = [
        Circuit(
            site_id=site.id,
            name=f"Service leg {index}",
            measurement_role="service-leg",
            split_phase_group="main-service",
        )
        for index in (1, 2)
    ]
    session.add_all(circuits)
    await session.flush()
    devices = [
        Device(
            site_id=site.id,
            utility_account_id=account.id,
            circuit_id=circuit.id,
            hardware_id=f"authority-leg-{index}",
            name=f"Leg {index}",
            measurement_role="service-leg",
        )
        for index, circuit in enumerate(circuits, start=1)
    ]
    session.add_all(devices)
    await session.flush()
    authority = AccountUsageAuthority(
        utility_account_id=account.id,
        authority_type="service_leg_pair",
        calculation_role="sensor_measurements",
        device_ids=[devices[0].id, "00000000-0000-0000-0000-000000000099"],
        confidence="high",
        complete_account=True,
        revision=1,
        updated_at=now,
    )
    cycle = BillingCycle(
        utility_account_id=account.id,
        starts_at=now - timedelta(days=5),
        ends_at=now + timedelta(days=25),
        status="confirmed",
        recalculation_required=False,
    )
    finalized_cycle = BillingCycle(
        utility_account_id=account.id,
        starts_at=now - timedelta(days=35),
        ends_at=now - timedelta(days=5),
        status="finalized",
        recalculation_required=False,
        finalized_at=now - timedelta(days=4),
    )
    session.add_all([authority, cycle, finalized_cycle])
    await session.flush()

    plan = await authority_reconciliation_plan(session, account, authority)
    assert plan["valid_device_ids"] == [devices[0].id]
    assert plan["invalid_device_ids"] == ["00000000-0000-0000-0000-000000000099"]
    assert {item["id"] for item in plan["eligible_service_leg_sensors"]} == {
        devices[0].id,
        devices[1].id,
    }

    repaired, after = await apply_sensor_usage_authority(
        session,
        account,
        AuthorityApplyRequest(
            mode="service_leg_pair",
            device_ids=(devices[0].id, devices[1].id),
            expected_revision=1,
            actor_id=None,
            reason="Reviewed physical split-phase service-leg topology",
            idempotency_key="test:service-leg-authority-repair",
        ),
    )
    assert repaired.revision == 2
    assert after["stored_authority_healthy"] is True
    assert after["invalid_device_ids"] == []
    assert cycle.recalculation_required is True
    assert cycle.usage_source_type == "unavailable"
    assert finalized_cycle.recalculation_required is False

    with pytest.raises(ProblemError) as stale_revision:
        await apply_sensor_usage_authority(
            session,
            account,
            AuthorityApplyRequest(
                mode="service_leg_pair",
                device_ids=(devices[0].id, devices[1].id),
                expected_revision=1,
                actor_id=None,
                reason="Stale concurrent edit",
                idempotency_key="test:stale-service-leg-authority-repair",
            ),
        )
    assert stale_revision.value.code == "stale_revision"


@pytest.mark.asyncio
async def test_authority_plan_reports_duplicate_inactive_wrong_account_and_branch(
    session: AsyncSession,
) -> None:
    site = Site(name="Invalid authority site")
    utility = Utility(name="Invalid authority utility")
    session.add_all([site, utility])
    await session.flush()
    account = UtilityAccount(site_id=site.id, utility_id=utility.id, name="Primary account")
    other_account = UtilityAccount(site_id=site.id, utility_id=utility.id, name="Other account")
    session.add_all([account, other_account])
    await session.flush()
    circuits = [
        Circuit(site_id=site.id, name="Branch", measurement_role="branch"),
        Circuit(
            site_id=site.id,
            name="Inactive leg",
            measurement_role="service-leg",
            split_phase_group="main-service",
        ),
        Circuit(
            site_id=site.id,
            name="Other leg",
            measurement_role="service-leg",
            split_phase_group="main-service",
        ),
    ]
    session.add_all(circuits)
    await session.flush()
    branch = Device(
        site_id=site.id,
        utility_account_id=account.id,
        circuit_id=circuits[0].id,
        hardware_id="invalid-authority-branch",
        name="Branch sensor",
        measurement_role="branch",
    )
    branch_two = Device(
        site_id=site.id,
        utility_account_id=account.id,
        circuit_id=circuits[0].id,
        hardware_id="invalid-authority-branch-two",
        name="Second branch sensor",
        measurement_role="branch",
    )
    inactive = Device(
        site_id=site.id,
        utility_account_id=account.id,
        circuit_id=circuits[1].id,
        hardware_id="invalid-authority-inactive",
        name="Inactive leg",
        measurement_role="service-leg",
        lifecycle_status="decommissioned",
    )
    wrong_account = Device(
        site_id=site.id,
        utility_account_id=other_account.id,
        circuit_id=circuits[2].id,
        hardware_id="invalid-authority-other-account",
        name="Other account leg",
        measurement_role="service-leg",
    )
    session.add_all([branch, branch_two, inactive, wrong_account])
    await session.flush()
    authority = AccountUsageAuthority(
        utility_account_id=account.id,
        authority_type="service_leg_pair",
        calculation_role="sensor_measurements",
        device_ids=[branch.id, branch.id, inactive.id, wrong_account.id],
        confidence="high",
        complete_account=True,
        revision=1,
        updated_at=datetime.now(UTC),
    )
    session.add(authority)
    await session.flush()

    plan = await authority_reconciliation_plan(session, account, authority)

    assert plan["device_ids"] == [branch.id, branch.id, inactive.id, wrong_account.id]
    assert {item["reason"] for item in plan["invalid_devices"]} == {
        "wrong_measurement_role",
        "duplicate",
        "removed",
        "wrong_account",
    }
    assert plan["stored_authority_healthy"] is False

    with pytest.raises(ProblemError) as branch_error:
        await apply_sensor_usage_authority(
            session,
            account,
            AuthorityApplyRequest(
                mode="service_leg_pair",
                device_ids=(branch.id, branch_two.id),
                expected_revision=1,
                actor_id=None,
                reason="Invalid branch topology must fail",
                idempotency_key="test:invalid-branch-service-leg-pair",
            ),
        )
    assert branch_error.value.code == "usage_authority_wrong_measurement_role"


@pytest.mark.asyncio
async def test_missing_sensor_warnings_name_pzem_and_required_sd_failures(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Hardware readiness site")
    session.add(site)
    await session.flush()
    pzem_device = Device(
        site_id=site.id,
        hardware_id="billing-pzem-failure",
        name="Outdoor-AC",
    )
    sd_device = Device(
        site_id=site.id,
        hardware_id="billing-sd-failure",
        name="Indoor-AC",
    )
    session.add_all([pzem_device, sd_device])
    await session.flush()
    session.add_all(
        [
            DeviceCapability(
                device_id=pzem_device.id,
                hardware_target="esp32s3",
                pzem_model="PZEM-004T-v3",
                sd_required=True,
                features={},
                reported_at=now,
            ),
            DeviceCapability(
                device_id=sd_device.id,
                hardware_target="esp32s3",
                pzem_model="PZEM-004T-v3",
                sd_required=True,
                features={},
                reported_at=now,
            ),
            DeviceHeartbeat(
                device_id=pzem_device.id,
                boot_id="83869685-4032-4e2c-8d5f-7aad43f1637e",
                received_at=now,
                pzem_ok=False,
                pzem_status="uart_timeout",
                sd_ok=True,
                sd_status="healthy",
                time_trusted=True,
                newest_sequence=0,
                backlog_estimate=0,
                payload={},
            ),
            DeviceHeartbeat(
                device_id=sd_device.id,
                boot_id="93869685-4032-4e2c-8d5f-7aad43f1637e",
                received_at=now,
                pzem_ok=True,
                pzem_status="healthy",
                sd_ok=False,
                sd_status="write_failed",
                time_trusted=True,
                newest_sequence=0,
                backlog_estimate=0,
                payload={},
            ),
        ]
    )
    await session.flush()

    warnings = await _missing_sensor_warnings(session, [pzem_device.id, sd_device.id], set())

    assert set(warnings) == {
        "Indoor-AC microSD is not writable (write_failed).",
        "Outdoor-AC PZEM meter is not returning valid samples (uart_timeout).",
    }
