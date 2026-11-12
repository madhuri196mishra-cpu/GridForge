"""Lossless JSON codec for canonical instrumentation wiring inventory."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.model.instrument_wiring import (
    CableAssembly, CableCore, CarrierKind, InstrumentationWiringInventory, PhysicalConnectionHop,
    PanelWire, SignalCarrierBinding, SignalDefinition, SignalKind,
    WiringConductorRole, WiringTerminalReference, WiringTagSeries, WiringTagPolicy,
)

WIRING_SCHEMA_VERSION = 4


class InstrumentationWiringSerializationError(ValueError):
    """Raised when a wiring inventory cannot be serialized without loss."""


def _terminal_to_dict(item: WiringTerminalReference) -> dict[str, str]:
    return {
        "equipment_id": item.equipment_id,
        "terminal_role": item.terminal_role,
        "designation": item.designation,
    }


def _terminal_from_dict(data: Mapping[str, Any]) -> WiringTerminalReference:
    if not isinstance(data, Mapping) or set(data) != {"equipment_id", "terminal_role", "designation"}:
        raise InstrumentationWiringSerializationError("Malformed wiring terminal reference.")
    return WiringTerminalReference(**dict(data))


def _check_record_fields(data: Mapping[str, Any], *, required: set[str], optional: set[str], label: str) -> None:
    keys = set(data)
    missing = required - keys
    unknown = keys - required - optional
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(str(item) for item in unknown)))
        raise InstrumentationWiringSerializationError(
            label + " fields are not canonical: " + "; ".join(details)
        )


def serialize_instrumentation_wiring(
    inventory: InstrumentationWiringInventory,
) -> dict[str, Any]:
    """Serialize the complete immutable inventory; never include canvas geometry."""
    if not isinstance(inventory, InstrumentationWiringInventory):
        raise TypeError("inventory must be an InstrumentationWiringInventory.")
    return {
        "schema": WIRING_SCHEMA_VERSION,
        "cables": [
            {
                "id": item.id, "tag": item.tag, "service": item.service,
                "core_count": item.core_count, "cable_type": item.cable_type,
                "conductor_material": item.conductor_material,
                "cross_section_mm2": item.cross_section_mm2,
                "rated_voltage_v": item.rated_voltage_v, "length_m": item.length_m,
                "from_location": item.from_location, "to_location": item.to_location,
                "revision": item.revision, "lifecycle_status": item.lifecycle_status,
            }
            for item in inventory.cables
        ],
        "cores": [
            {
                "id": item.id, "cable_id": item.cable_id,
                "core_number": item.core_number, "role": item.role.value,
                "terminal_path": [_terminal_to_dict(ref) for ref in item.terminal_path],
                "colour": item.colour, "pair_id": item.pair_id, "core_tag": item.core_tag,
                "revision": item.revision,
                "conductor_material": item.conductor_material,
                "thermocouple_type": item.thermocouple_type,
                "thermocouple_polarity": item.thermocouple_polarity,
            }
            for item in inventory.cores
        ],
        "panel_wires": [
            {
                "id": item.id, "tag": item.tag, "role": item.role.value,
                "terminal_path": [_terminal_to_dict(ref) for ref in item.terminal_path],
                "cross_section_mm2": item.cross_section_mm2, "colour": item.colour,
                "conductor_material": item.conductor_material,
                "thermocouple_type": item.thermocouple_type,
                "thermocouple_polarity": item.thermocouple_polarity,
                "thermocouple_cable_designation": item.thermocouple_cable_designation,
                "revision": item.revision,
            }
            for item in inventory.panel_wires
        ],
        "signals": [
            {
                "id": item.id, "tag": item.tag, "description": item.description,
                "kind": item.kind.value, "unit": item.unit, "phase": item.phase,
                "measurement_channel_id": item.measurement_channel_id,
            }
            for item in inventory.signals
        ],
        "tag_policy": [
            {
                "record_kind": item.record_kind, "prefix": item.prefix,
                "separator": item.separator, "minimum_digits": item.minimum_digits,
                "next_number": item.next_number,
            }
            for item in inventory.tag_policy.series
        ],
        "signal_bindings": [
            {
                "id": item.id, "signal_id": item.signal_id,
                "carrier_kind": item.carrier_kind.value,
                "carrier_id": item.carrier_id, "direction": item.direction,
            }
            for item in inventory.signal_bindings
        ],
        "connection_hops": [
            {
                "id": item.id,
                "from_terminal": _terminal_to_dict(item.from_terminal),
                "to_terminal": _terminal_to_dict(item.to_terminal),
                "carrier_kind": item.carrier_kind.value,
                "carrier_id": item.carrier_id,
                "revision": item.revision,
                "lifecycle_status": item.lifecycle_status,
                "supersedes_hop_id": item.supersedes_hop_id,
            }
            for item in inventory.connection_hops
        ],
    }


def deserialize_instrumentation_wiring(data: Mapping[str, Any] | None) -> InstrumentationWiringInventory:
    """Validate and reconstruct a project-owned wiring inventory."""
    if data is None:
        return InstrumentationWiringInventory()
    if not isinstance(data, Mapping):
        raise InstrumentationWiringSerializationError("Instrumentation wiring payload must be an object.")
    schema = data.get("schema")
    if isinstance(schema, bool) or schema not in {2, 3, WIRING_SCHEMA_VERSION}:
        raise InstrumentationWiringSerializationError(
            f"Unsupported instrumentation wiring schema {schema!r}."
        )
    expected = {"schema", "cables", "cores", "panel_wires", "signals", "signal_bindings", "tag_policy"}
    if schema >= 4:
        expected.add("connection_hops")
    if set(data) != expected:
        raise InstrumentationWiringSerializationError(
            "Instrumentation wiring payload fields are not canonical: "
            + ", ".join(sorted(str(key) for key in set(data) ^ expected))
        )
    collection_keys = ("cables", "cores", "panel_wires", "signals", "signal_bindings", "tag_policy")
    if schema >= 4:
        collection_keys += ("connection_hops",)
    for key in collection_keys:
        if not isinstance(data.get(key), list) or any(not isinstance(item, Mapping) for item in data[key]):
            raise InstrumentationWiringSerializationError(f"Instrumentation wiring {key} must be an array of objects.")

    try:
        _field_contracts = (
            ("cables", {"id", "tag", "service", "core_count"}, {"cable_type", "conductor_material", "cross_section_mm2", "rated_voltage_v", "length_m", "from_location", "to_location", "revision", "lifecycle_status"}, "Cable assembly"),
            ("cores", {"id", "cable_id", "core_number", "role"}, {"terminal_path", "colour", "pair_id", "core_tag", "conductor_material", "thermocouple_type", "thermocouple_polarity", "revision"}, "Cable core"),
            ("panel_wires", {"id", "tag", "role", "terminal_path"}, {"cross_section_mm2", "colour", "conductor_material", "thermocouple_type", "thermocouple_polarity", "thermocouple_cable_designation", "revision"}, "Panel wire"),
            ("signals", {"id", "tag", "description", "kind"}, {"unit", "phase", "measurement_channel_id"}, "Signal definition"),
            ("signal_bindings", {"id", "signal_id", "carrier_kind", "carrier_id"}, {"direction"}, "Signal carrier binding"),
            ("connection_hops", {"id", "from_terminal", "to_terminal", "carrier_kind", "carrier_id"}, {"revision", "lifecycle_status", "supersedes_hop_id"}, "Physical connection hop"),

            ("tag_policy", {"record_kind", "prefix"}, {"separator", "minimum_digits", "next_number"}, "Wiring tag series"),
        )
        if schema < 4:
            _field_contracts = tuple(item for item in _field_contracts if item[0] != "connection_hops")
        for collection_name, required, optional, label in _field_contracts:
            for item in data[collection_name]:
                _check_record_fields(item, required=required, optional=optional, label=label)
        cables = tuple(CableAssembly(**dict(item)) for item in data["cables"])
        cores = tuple(
            CableCore(
                id=item["id"], cable_id=item["cable_id"], core_number=item["core_number"],
                role=WiringConductorRole(item["role"]),
                terminal_path=tuple(_terminal_from_dict(ref) for ref in item.get("terminal_path", ())),
                colour=item.get("colour"), pair_id=item.get("pair_id"), core_tag=item.get("core_tag"),
                conductor_material=item.get("conductor_material"),
                thermocouple_type=item.get("thermocouple_type"),
                thermocouple_polarity=item.get("thermocouple_polarity"),
                revision=item.get("revision", 1),
            )
            for item in data["cores"]
        )
        panel_wires = tuple(
            PanelWire(
                id=item["id"], tag=item["tag"], role=WiringConductorRole(item["role"]),
                terminal_path=tuple(_terminal_from_dict(ref) for ref in item["terminal_path"]),
                cross_section_mm2=item.get("cross_section_mm2"), colour=item.get("colour"),
                conductor_material=item.get("conductor_material"),
                thermocouple_type=item.get("thermocouple_type"),
                thermocouple_polarity=item.get("thermocouple_polarity"),
                thermocouple_cable_designation=item.get("thermocouple_cable_designation"),
                revision=item.get("revision", 1),
            )
            for item in data["panel_wires"]
        )
        signals = tuple(
            SignalDefinition(
                id=item["id"], tag=item["tag"], description=item["description"],
                kind=SignalKind(item["kind"]), unit=item.get("unit"), phase=item.get("phase"),
                measurement_channel_id=item.get("measurement_channel_id"),
            )
            for item in data["signals"]
        )
        tag_policy = WiringTagPolicy(series=tuple(
            WiringTagSeries(**dict(item)) for item in data["tag_policy"]
        ))
        bindings = tuple(
            SignalCarrierBinding(
                id=item["id"], signal_id=item["signal_id"],
                carrier_kind=CarrierKind(item["carrier_kind"]),
                carrier_id=item["carrier_id"], direction=item.get("direction", "BIDIRECTIONAL"),
            )
            for item in data["signal_bindings"]
        )
        hops = tuple(
            PhysicalConnectionHop(
                id=item["id"],
                from_terminal=_terminal_from_dict(item["from_terminal"]),
                to_terminal=_terminal_from_dict(item["to_terminal"]),
                carrier_kind=CarrierKind(item["carrier_kind"]),
                carrier_id=item["carrier_id"],
                revision=item.get("revision", 1),
                lifecycle_status=item.get("lifecycle_status", "PLANNED"),
                supersedes_hop_id=item.get("supersedes_hop_id"),
            )
            for item in data.get("connection_hops", ())
        )
        return InstrumentationWiringInventory(
            cables=cables, cores=cores, panel_wires=panel_wires,
            signals=signals, signal_bindings=bindings, connection_hops=hops, tag_policy=tag_policy,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InstrumentationWiringSerializationError(
            f"Invalid instrumentation wiring inventory: {exc}"
        ) from exc


__all__ = [
    "WIRING_SCHEMA_VERSION",
    "InstrumentationWiringSerializationError",
    "serialize_instrumentation_wiring",
    "deserialize_instrumentation_wiring",
]
