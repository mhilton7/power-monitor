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
        "wrong_device_role",
        "duplicate_sensor",
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
    assert branch_error.value.code == "usage_authority_sensor_wrong_role"


@pytest.mark.asyncio
async def test_whole_account_repair_replaces_stale_ids_and_returns_precise_errors(
    session: AsyncSession,
) -> None:
    now = datetime.now(UTC)
    site = Site(name="Whole-account authority site")
    utility = Utility(name="Whole-account authority utility")
    session.add_all([site, utility])
    await session.flush()
    account = UtilityAccount(site_id=site.id, utility_id=utility.id, name="Home account")
    other_account = UtilityAccount(
        site_id=site.id,
        utility_id=utility.id,
        name="Detached account",
    )
    session.add_all([account, other_account])
    await session.flush()
    main_circuit = Circuit(site_id=site.id, name="Whole home", measurement_role="main")
    second_main_circuit = Circuit(
        site_id=site.id,
        name="Second main",
        measurement_role="main",
    )
    branch_circuit = Circuit(site_id=site.id, name="Branch", measurement_role="branch")
    session.add_all([main_circuit, second_main_circuit, branch_circuit])
    await session.flush()
    main = Device(
        site_id=site.id,
        utility_account_id=account.id,
        circuit_id=main_circuit.id,
        hardware_id="whole-authority-main",
        name="Current whole home",
        measurement_role="main",
    )
    second_main = Device(
        site_id=site.id,
        utility_account_id=account.id,
        circuit_id=second_main_circuit.id,
        hardware_id="whole-authority-second-main",
        name="Second whole home",
        measurement_role="main",
    )
    branch = Device(
        site_id=site.id,
        utility_account_id=account.id,
        circuit_id=branch_circuit.id,
        hardware_id="whole-authority-branch",
        name="Indoor-AC1",
        measurement_role="branch",
    )
    inactive = Device(
        site_id=site.id,
        utility_account_id=account.id,
        circuit_id=second_main_circuit.id,
        hardware_id="whole-authority-inactive",
        name="Inactive main",
        measurement_role="main",
        lifecycle_status="inactive",
    )
    wrong_account = Device(
        site_id=site.id,
        utility_account_id=other_account.id,
        circuit_id=second_main_circuit.id,
        hardware_id="whole-authority-wrong-account",
        name="Other service main",
        measurement_role="main",
    )
    session.add_all([main, second_main, branch, inactive, wrong_account])
    await session.flush()
    stale_id = "00000000-0000-0000-0000-000000000088"
    authority = AccountUsageAuthority(
        utility_account_id=account.id,
        authority_type="whole_account_meter",
        calculation_role="sensor_measurements",
        device_ids=[stale_id, main.id],
        confidence="high",
        complete_account=True,
        revision=1,
        updated_at=now,
    )
    session.add(authority)
    await session.flush()

    before = await authority_reconciliation_plan(session, account, authority)
    main_row = next(item for item in before["sensors"] if item["id"] == main.id)
    branch_row = next(item for item in before["sensors"] if item["id"] == branch.id)
    wrong_account_row = next(item for item in before["sensors"] if item["id"] == wrong_account.id)
    assert before["valid_device_ids"] == [main.id]
    assert before["invalid_device_ids"] == [stale_id]
    assert main_row["eligible_whole_home"] is True
    assert main_row["currently_saved_in_authority"] is True
    assert branch_row["whole_home_eligibility_codes"] == [
        "wrong_device_role",
        "wrong_circuit_role",
    ]
    assert wrong_account_row["whole_home_eligibility_codes"] == ["wrong_account"]

    repaired, after = await apply_sensor_usage_authority(
        session,
        account,
        AuthorityApplyRequest(
            mode="whole_account_meter",
            device_ids=(main.id,),
            expected_revision=1,
            actor_id=None,
            reason="Reviewed the installed complete-service meter",
            idempotency_key="test:whole-account-stale-repair",
        ),
    )
    assert repaired.device_ids == [main.id]
    assert after["invalid_device_ids"] == []
    assert after["stored_authority_healthy"] is True

    cases = [
        ((), "usage_authority_sensor_count"),
        ((main.id, second_main.id), "usage_authority_sensor_count"),
        ((stale_id,), "usage_authority_sensor_stale"),
        ((inactive.id,), "usage_authority_sensor_inactive"),
        ((wrong_account.id,), "usage_authority_sensor_wrong_account"),
        ((branch.id,), "usage_authority_sensor_not_whole_home"),
    ]
    for index, (device_ids, expected_code) in enumerate(cases):
        with pytest.raises(ProblemError) as error:
            await apply_sensor_usage_authority(
                session,
                account,
                AuthorityApplyRequest(
                    mode="whole_account_meter",
                    device_ids=device_ids,
                    expected_revision=2,
                    actor_id=None,
                    reason="Exercise precise whole-home validation",
                    idempotency_key=f"test:whole-account-invalid:{index}",
                ),
            )
        assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_service_leg_pair_rejects_duplicate_and_overlapping_topology_precisely(
    session: AsyncSession,
) -> None:
    site = Site(name="Service topology validation site")
    utility = Utility(name="Service topology validation utility")
    session.add_all([site, utility])
    await session.flush()
    account = UtilityAccount(site_id=site.id, utility_id=utility.id, name="Service account")
    session.add(account)
    await session.flush()
    shared_circuit = Circuit(
        site_id=site.id,
        name="Shared service leg",
        measurement_role="service-leg",
        split_phase_group="main-service",
    )
    parent_circuit = Circuit(
        site_id=site.id,
        name="Parent service leg",
        measurement_role="service-leg",
        split_phase_group="overlap-service",
    )
    session.add_all([shared_circuit, parent_circuit])
    await session.flush()
    child_circuit = Circuit(
        site_id=site.id,
        parent_id=parent_circuit.id,
        name="Child service leg",
        measurement_role="service-leg",
        split_phase_group="overlap-service",
    )
    session.add(child_circuit)
    await session.flush()
    devices = [
        Device(
            site_id=site.id,
            utility_account_id=account.id,
            circuit_id=circuit.id,
            hardware_id=f"pair-precise-{index}",
            name=f"Pair sensor {index}",
            measurement_role="service-leg",
        )
        for index, circuit in enumerate(
            [shared_circuit, shared_circuit, parent_circuit, child_circuit],
            start=1,
        )
    ]
    session.add_all(devices)
    await session.flush()

    cases = [
        ((devices[0].id,), "usage_authority_sensor_count"),
        ((devices[0].id, devices[0].id), "usage_authority_sensor_duplicate"),
        (
            (devices[0].id, devices[1].id),
            "usage_authority_sensor_duplicate_circuit",
        ),
        ((devices[2].id, devices[3].id), "usage_authority_topology_overlap"),
    ]
    for index, (device_ids, expected_code) in enumerate(cases):
        with pytest.raises(ProblemError) as error:
            await apply_sensor_usage_authority(
                session,
                account,
                AuthorityApplyRequest(
                    mode="service_leg_pair",
                    device_ids=device_ids,
                    expected_revision=None,
                    actor_id=None,
                    reason="Exercise precise service-leg validation",
                    idempotency_key=f"test:service-leg-invalid:{index}",
                ),
            )
        assert error.value.code == expected_code


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
