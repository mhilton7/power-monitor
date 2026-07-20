from __future__ import annotations

from dataclasses import dataclass


def would_create_cycle(
    circuit_id: str, proposed_parent_id: str | None, parents: dict[str, str | None]
) -> bool:
    current = proposed_parent_id
    visited = {circuit_id}
    while current is not None:
        if current in visited:
            return True
        visited.add(current)
        current = parents.get(current)
    return False


@dataclass(frozen=True)
class AggregateItem:
    circuit_id: str
    role: str
    split_phase_group: str | None = None


def overlap_warnings(items: list[AggregateItem], parents: dict[str, str | None]) -> list[str]:
    warnings: list[str] = []
    selected = {item.circuit_id for item in items}
    for item in items:
        ancestor = parents.get(item.circuit_id)
        while ancestor is not None:
            if ancestor in selected:
                warnings.append(f"Circuit {item.circuit_id} overlaps selected ancestor {ancestor}")
                break
            ancestor = parents.get(ancestor)
    service_legs = [item for item in items if item.role == "service-leg"]
    grouped: dict[str, int] = {}
    for item in service_legs:
        if item.split_phase_group:
            grouped[item.split_phase_group] = grouped.get(item.split_phase_group, 0) + 1
    for item in service_legs:
        if not item.split_phase_group or grouped.get(item.split_phase_group) != 2:
            warnings.append(
                f"Service-leg circuit {item.circuit_id} is not a complete two-leg split-phase group"
            )
    return sorted(set(warnings))
