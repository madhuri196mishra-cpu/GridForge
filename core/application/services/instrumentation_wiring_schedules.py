"""Deterministic read-only schedules for canonical instrumentation wiring.

Schedule rows are projections of Core inventory. They never own identities,
infer terminal connectivity, or generate missing physical hop IDs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.model.instrument_wiring import (
    CarrierKind,
    InstrumentationWiringInventory,
    WiringTerminalReference,
)


@dataclass(frozen=True, slots=True)
class PhysicalWiringScheduleRow:
    carrier_kind: str
    carrier_id: str
    carrier_tag: str
    carrier_revision: int
    hop_id: Optional[str]
    hop_revision: Optional[int]
    lifecycle_status: str
    from_terminal: WiringTerminalReference
    to_terminal: WiringTerminalReference


@dataclass(frozen=True, slots=True)
class SignalLoopScheduleRow:
    signal_id: str
    signal_tag: str
    description: str
    signal_kind: str
    unit: Optional[str]
    phase: Optional[str]
    binding_id: str
    carrier_kind: str
    carrier_id: str
    direction: str
    carrier_tag: Optional[str]
    hop_ids: tuple[str, ...]
    physical_route_status: str


def _validate_inventory(inventory: InstrumentationWiringInventory) -> None:
    if not isinstance(inventory, InstrumentationWiringInventory):
        raise TypeError("inventory must be an InstrumentationWiringInventory.")


def _physical_rows(inventory: InstrumentationWiringInventory, carrier_kind: CarrierKind) -> tuple[PhysicalWiringScheduleRow, ...]:
    if carrier_kind is CarrierKind.CABLE_CORE:
        carriers = inventory.cores
        carrier_tags = {item.id: item.core_tag or f"{item.cable_id}/CORE-{item.core_number}" for item in carriers}
        paths = {item.id: item.terminal_path for item in carriers}
    else:
        carriers = inventory.panel_wires
        carrier_tags = {item.id: item.tag for item in carriers}
        paths = {item.id: item.terminal_path for item in carriers}

    rows: list[PhysicalWiringScheduleRow] = []
    for carrier in sorted(carriers, key=lambda item: item.id):
        hops = sorted(
            (item for item in inventory.connection_hops
             if item.carrier_kind is carrier_kind and item.carrier_id == carrier.id),
            key=lambda item: (item.revision, item.id),
        )
        for hop in hops:
            rows.append(PhysicalWiringScheduleRow(
                carrier_kind=carrier_kind.value,
                carrier_id=carrier.id,
                carrier_tag=carrier_tags[carrier.id],
                carrier_revision=carrier.revision,
                hop_id=hop.id,
                hop_revision=hop.revision,
                lifecycle_status=hop.lifecycle_status,
                from_terminal=hop.from_terminal,
                to_terminal=hop.to_terminal,
            ))
        # Legacy paths remain visible without inventing hop identity.
        if not hops and len(paths[carrier.id]) >= 2:
            path = paths[carrier.id]
            for index in range(len(path) - 1):
                rows.append(PhysicalWiringScheduleRow(
                    carrier_kind=carrier_kind.value,
                    carrier_id=carrier.id,
                    carrier_tag=carrier_tags[carrier.id],
                    carrier_revision=carrier.revision,
                    hop_id=None,
                    hop_revision=None,
                    lifecycle_status="UNMAPPED_LEGACY",
                    from_terminal=path[index],
                    to_terminal=path[index + 1],
                ))
    return tuple(rows)


def build_cable_core_schedule(inventory: InstrumentationWiringInventory) -> tuple[PhysicalWiringScheduleRow, ...]:
    """Return cable-core segment rows, retaining stable hop IDs where known."""
    _validate_inventory(inventory)
    return _physical_rows(inventory, CarrierKind.CABLE_CORE)


def build_panel_wire_schedule(inventory: InstrumentationWiringInventory) -> tuple[PhysicalWiringScheduleRow, ...]:
    """Return panel-wire segment rows, retaining stable hop IDs where known."""
    _validate_inventory(inventory)
    return _physical_rows(inventory, CarrierKind.PANEL_WIRE)


def build_signal_loop_schedule(inventory: InstrumentationWiringInventory) -> tuple[SignalLoopScheduleRow, ...]:
    """Join signal definitions to carrier bindings and known physical hop IDs."""
    _validate_inventory(inventory)
    signals = {item.id: item for item in inventory.signals}
    cores = {item.id: item for item in inventory.cores}
    wires = {item.id: item for item in inventory.panel_wires}
    hops_by_carrier: dict[tuple[CarrierKind, str], list[str]] = {}
    for hop in inventory.connection_hops:
        hops_by_carrier.setdefault((hop.carrier_kind, hop.carrier_id), []).append(hop.id)

    rows: list[SignalLoopScheduleRow] = []
    for binding in sorted(inventory.signal_bindings, key=lambda item: (item.signal_id, item.id)):
        signal = signals[binding.signal_id]
        carrier = cores.get(binding.carrier_id) if binding.carrier_kind is CarrierKind.CABLE_CORE else wires.get(binding.carrier_id) if binding.carrier_kind is CarrierKind.PANEL_WIRE else None
        carrier_tag = None
        if carrier is not None:
            carrier_tag = (
                carrier.core_tag or f"{carrier.cable_id}/CORE-{carrier.core_number}"
                if binding.carrier_kind is CarrierKind.CABLE_CORE else carrier.tag
            )
        hop_ids = tuple(sorted(hops_by_carrier.get((binding.carrier_kind, binding.carrier_id), ())))
        if binding.carrier_kind in {CarrierKind.CABLE_CORE, CarrierKind.PANEL_WIRE}:
            route_status = "MAPPED" if hop_ids else "UNMAPPED_LEGACY" if carrier is not None and len(carrier.terminal_path) >= 2 else "NO_PHYSICAL_ROUTE"
        else:
            route_status = "LOGICAL_OR_FIBRE_CARRIER"
        rows.append(SignalLoopScheduleRow(
            signal_id=signal.id,
            signal_tag=signal.tag,
            description=signal.description,
            signal_kind=signal.kind.value,
            unit=signal.unit,
            phase=signal.phase,
            binding_id=binding.id,
            carrier_kind=binding.carrier_kind.value,
            carrier_id=binding.carrier_id,
            direction=binding.direction,
            carrier_tag=carrier_tag,
            hop_ids=hop_ids,
            physical_route_status=route_status,
        ))
    return tuple(rows)


__all__ = [
    "PhysicalWiringScheduleRow",
    "SignalLoopScheduleRow",
    "build_cable_core_schedule",
    "build_panel_wire_schedule",
    "build_signal_loop_schedule",
]
