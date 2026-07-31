from __future__ import annotations

import app.home_aggregate as home_aggregate
from app.db.models import Circuit, Device


def _circuit(
    circuit_id: str,
    *,
    parent_id: str | None = None,
    role: str = "branch",
) -> Circuit:
    return Circuit(
        id=circuit_id,
        site_id="site-1",
        parent_id=parent_id,
        name=circuit_id,
        measurement_role=role,
        split_phase_group=None,
    )


def _device(
    device_id: str,
    circuit_id: str,
    *,
    included: bool,
    role: str = "branch",
) -> Device:
    return Device(
        id=device_id,
        site_id="site-1",
        utility_account_id="account-1",
        circuit_id=circuit_id,
        hardware_id=f"hardware-{device_id}",
        name=device_id,
        measurement_role=role,
        include_in_default_site_total=included,
    )


def test_parent_child_sensors_keep_explicit_selection_to_prevent_double_counting() -> None:
    main = _circuit("main", role="main")
    branch = _circuit("branch", parent_id=main.id)
    main_sensor = _device("main-sensor", main.id, included=True, role="main")
    branch_sensor = _device("branch-sensor", branch.id, included=False)

    selection = home_aggregate._resolve_site_devices(
        [main_sensor, branch_sensor],
        [main, branch],
        {main.id: main, branch.id: branch},
    )

    assert selection.devices == (main_sensor,)
    assert selection.mode == "explicit_topology_fallback"
    assert selection.warnings == ("Circuit branch overlaps selected ancestor main",)
