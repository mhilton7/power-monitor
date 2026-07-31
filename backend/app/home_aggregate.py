from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Circuit, Device
from app.topology import AggregateItem, overlap_warnings


@dataclass(frozen=True)
class HomeAggregateSelection:
    devices: tuple[Device, ...]
    mode: str
    warnings: tuple[str, ...] = ()


async def resolve_home_aggregate_devices(
    session: AsyncSession,
    devices: list[Device],
) -> HomeAggregateSelection:
    """Resolve a topology-safe live Home total without an N+1 query.

    A complete set of measurement sensors on the same utility account can be
    combined automatically when every sensor has a distinct, non-overlapping
    circuit assignment. Ambiguous topologies retain the administrator's
    explicit Home-total selections.
    """

    if not devices:
        return HomeAggregateSelection((), "no_devices")

    site_ids = {device.site_id for device in devices}
    circuits = list(await session.scalars(select(Circuit).where(Circuit.site_id.in_(site_ids))))
    circuit_by_id = {circuit.id: circuit for circuit in circuits}
    circuits_by_site: dict[str, list[Circuit]] = {}
    devices_by_site: dict[str, list[Device]] = {}
    for circuit in circuits:
        circuits_by_site.setdefault(circuit.site_id, []).append(circuit)
    for device in devices:
        devices_by_site.setdefault(device.site_id, []).append(device)

    selected: list[Device] = []
    modes: set[str] = set()
    warnings: list[str] = []
    for site_device_list in devices_by_site.values():
        site_selection = _resolve_site_devices(
            site_device_list,
            circuits_by_site.get(site_device_list[0].site_id, []),
            circuit_by_id,
        )
        selected.extend(site_selection.devices)
        modes.add(site_selection.mode)
        warnings.extend(site_selection.warnings)

    mode = modes.pop() if len(modes) == 1 else "mixed_site_selection"
    return HomeAggregateSelection(tuple(selected), mode, tuple(sorted(set(warnings))))


def _resolve_site_devices(
    devices: list[Device],
    site_circuits: list[Circuit],
    circuit_by_id: dict[str, Circuit],
) -> HomeAggregateSelection:
    explicit = tuple(device for device in devices if device.include_in_default_site_total)
    measurement_devices = [
        device for device in devices if device.measurement_role != "informational"
    ]

    if len(measurement_devices) > 1:
        fully_assigned = all(
            device.circuit_id is not None
            and device.utility_account_id is not None
            and device.circuit_id in circuit_by_id
            and circuit_by_id[device.circuit_id].site_id == device.site_id
            for device in measurement_devices
        )
        account_ids = {device.utility_account_id for device in measurement_devices}
        if fully_assigned and len(account_ids) == 1:
            circuit_ids = [device.circuit_id for device in measurement_devices]
            duplicate_circuits = len(circuit_ids) != len(set(circuit_ids))
            parents = {circuit.id: circuit.parent_id for circuit in site_circuits}
            items: list[AggregateItem] = []
            for device in measurement_devices:
                assert device.circuit_id is not None
                circuit = circuit_by_id[device.circuit_id]
                items.append(
                    AggregateItem(
                        circuit_id=circuit.id,
                        role=circuit.measurement_role,
                        split_phase_group=circuit.split_phase_group,
                    )
                )
            topology_warnings = overlap_warnings(items, parents)
            if duplicate_circuits:
                topology_warnings.append("Multiple sensors are assigned to the same circuit")
            if not topology_warnings:
                return HomeAggregateSelection(
                    tuple(measurement_devices),
                    "complete_non_overlapping_service",
                )
            if explicit:
                return HomeAggregateSelection(
                    explicit,
                    "explicit_topology_fallback",
                    tuple(sorted(set(topology_warnings))),
                )

    if explicit:
        return HomeAggregateSelection(explicit, "explicit_selection")
    if len(devices) == 1:
        return HomeAggregateSelection((devices[0],), "single_sensor_fallback")
    return HomeAggregateSelection((), "configuration_required")
