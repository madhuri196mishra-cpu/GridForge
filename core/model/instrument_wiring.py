"""Canonical physical instrumentation wiring and signal identity records.

These records are independent of power-network Branch/Cable and of
MeasurementChannel. They contain engineering identity and connectivity only;
canvas geometry and Qt objects are deliberately excluded.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Optional


def _required_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")
    return value.strip()


def _optional_text(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string or None.")
    value = value.strip()
    return value or None


def _positive_int(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{field} must be a positive integer.")
    return value


def _non_negative_finite(value: Optional[float], field: str) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if not isfinite(number) or number < 0:
        raise ValueError(f"{field} must be finite and non-negative.")
    return number


@dataclass(frozen=True, slots=True)
class WiringTerminalReference:
    """Stable reference to a physical terminal; designation is its visible label."""

    equipment_id: str
    terminal_role: str
    designation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "equipment_id", _required_text(self.equipment_id, "equipment_id"))
        object.__setattr__(self, "terminal_role", _required_text(self.terminal_role, "terminal_role"))
        object.__setattr__(self, "designation", _required_text(self.designation, "designation"))

    @property
    def identity(self) -> tuple[str, str]:
        return self.equipment_id, self.terminal_role


class WiringConductorRole(str, Enum):
    SIGNAL = "SIGNAL"
    RETURN = "RETURN"
    SUPPLY = "SUPPLY"
    PROTECTIVE_EARTH = "PROTECTIVE_EARTH"
    SCREEN = "SCREEN"
    DRAIN = "DRAIN"
    ARMOUR = "ARMOUR"
    SPARE = "SPARE"
    RESERVED = "RESERVED"
    COMMUNICATION = "COMMUNICATION"
    OTHER = "OTHER"


class SignalKind(str, Enum):
    ANALOG = "ANALOG"
    DIGITAL = "DIGITAL"
    COMMUNICATION = "COMMUNICATION"
    CURRENT = "CURRENT"
    VOLTAGE = "VOLTAGE"
    TEMPERATURE = "TEMPERATURE"
    CUSTOM = "CUSTOM"


class CarrierKind(str, Enum):
    CABLE_CORE = "CABLE_CORE"
    PANEL_WIRE = "PANEL_WIRE"
    FIBRE = "FIBRE"
    LOGICAL = "LOGICAL"


@dataclass(frozen=True, slots=True)
class CableAssembly:
    """Installed cable identity and declared construction metadata.

    This is an instrumentation/interconnection cable record, not the
    power-flow Cable branch in core.model.cable.
    """

    id: str
    tag: str
    service: str
    core_count: int
    cable_type: Optional[str] = None
    conductor_material: Optional[str] = None
    cross_section_mm2: Optional[float] = None
    rated_voltage_v: Optional[float] = None
    length_m: Optional[float] = None
    from_location: Optional[str] = None
    to_location: Optional[str] = None
    revision: int = 1
    lifecycle_status: str = "PLANNED"

    def __post_init__(self) -> None:
        for field in ("id", "tag", "service", "lifecycle_status"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        object.__setattr__(self, "core_count", _positive_int(self.core_count, "core_count"))
        for field in ("cable_type", "conductor_material", "from_location", "to_location"):
            object.__setattr__(self, field, _optional_text(getattr(self, field), field))
        for field in ("cross_section_mm2", "rated_voltage_v", "length_m"):
            value = _non_negative_finite(getattr(self, field), field)
            if field == "cross_section_mm2" and value == 0:
                raise ValueError("cross_section_mm2 must be positive when supplied.")
            object.__setattr__(self, field, value)
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))


@dataclass(frozen=True, slots=True)
class CableCore:
    """One identifiable conductor/core within a cable assembly."""

    id: str
    cable_id: str
    core_number: int
    role: WiringConductorRole
    terminal_path: tuple[WiringTerminalReference, ...] = ()
    colour: Optional[str] = None
    pair_id: Optional[str] = None
    core_tag: Optional[str] = None
    conductor_material: Optional[str] = None
    thermocouple_type: Optional[str] = None
    thermocouple_polarity: Optional[str] = None
    revision: int = 1

    def __post_init__(self) -> None:
        for field in ("id", "cable_id"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        object.__setattr__(self, "core_number", _positive_int(self.core_number, "core_number"))
        if not isinstance(self.role, WiringConductorRole):
            raise TypeError("role must be a WiringConductorRole.")
        path = tuple(self.terminal_path)
        if any(not isinstance(item, WiringTerminalReference) for item in path):
            raise TypeError("terminal_path entries must be WiringTerminalReference values.")
        if len(path) == 1:
            raise ValueError("terminal_path must be empty or contain at least two terminals.")
        if len({item.identity for item in path}) != len(path):
            raise ValueError("terminal_path must not repeat a terminal.")
        object.__setattr__(self, "terminal_path", path)
        for field in ("colour", "pair_id", "core_tag", "conductor_material", "thermocouple_type", "thermocouple_polarity"):
            object.__setattr__(self, field, _optional_text(getattr(self, field), field))
        if self.thermocouple_type is not None:
            object.__setattr__(self, "thermocouple_type", self.thermocouple_type.upper())
        if self.thermocouple_polarity is not None:
            polarity = self.thermocouple_polarity.upper()
            if polarity not in {"POSITIVE", "NEGATIVE"}:
                raise ValueError("thermocouple_polarity must be POSITIVE or NEGATIVE.")
            object.__setattr__(self, "thermocouple_polarity", polarity)
        if (self.thermocouple_type is None) != (self.thermocouple_polarity is None):
            raise ValueError("thermocouple_type and thermocouple_polarity must be declared together.")
        if self.thermocouple_type is not None and self.conductor_material is None:
            raise ValueError("conductor_material is required for a declared thermocouple conductor.")
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))


@dataclass(frozen=True, slots=True)
class PanelWire:
    """Individually identified panel wire, jumper or inter-terminal link."""

    id: str
    tag: str
    role: WiringConductorRole
    terminal_path: tuple[WiringTerminalReference, ...]
    cross_section_mm2: Optional[float] = None
    colour: Optional[str] = None
    conductor_material: Optional[str] = None
    thermocouple_type: Optional[str] = None
    thermocouple_polarity: Optional[str] = None
    thermocouple_cable_designation: Optional[str] = None
    revision: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "id"))
        object.__setattr__(self, "tag", _required_text(self.tag, "tag"))
        if not isinstance(self.role, WiringConductorRole):
            raise TypeError("role must be a WiringConductorRole.")
        path = tuple(self.terminal_path)
        if len(path) < 2 or any(not isinstance(item, WiringTerminalReference) for item in path):
            raise ValueError("terminal_path must contain at least two terminal references.")
        if len({item.identity for item in path}) != len(path):
            raise ValueError("terminal_path must not repeat a terminal.")
        object.__setattr__(self, "terminal_path", path)
        area = _non_negative_finite(self.cross_section_mm2, "cross_section_mm2")
        if area == 0:
            raise ValueError("cross_section_mm2 must be positive when supplied.")
        object.__setattr__(self, "cross_section_mm2", area)
        object.__setattr__(self, "colour", _optional_text(self.colour, "colour"))
        for field in ("conductor_material", "thermocouple_type", "thermocouple_polarity", "thermocouple_cable_designation"):
            object.__setattr__(self, field, _optional_text(getattr(self, field), field))
        if self.thermocouple_cable_designation is not None:
            object.__setattr__(self, "thermocouple_cable_designation", self.thermocouple_cable_designation.upper())
        if self.thermocouple_type is not None:
            object.__setattr__(self, "thermocouple_type", self.thermocouple_type.upper())
        if self.thermocouple_polarity is not None:
            polarity = self.thermocouple_polarity.upper()
            if polarity not in {"POSITIVE", "NEGATIVE"}:
                raise ValueError("thermocouple_polarity must be POSITIVE or NEGATIVE.")
            object.__setattr__(self, "thermocouple_polarity", polarity)
        if (self.thermocouple_type is None) != (self.thermocouple_polarity is None):
            raise ValueError("thermocouple_type and thermocouple_polarity must be declared together.")
        if self.thermocouple_type is not None and self.conductor_material is None:
            raise ValueError("conductor_material is required for a declared thermocouple conductor.")
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))


@dataclass(frozen=True, slots=True)
class SignalDefinition:
    """Engineering meaning and designation of a signal, independent of carrier."""

    id: str
    tag: str
    description: str
    kind: SignalKind
    unit: Optional[str] = None
    phase: Optional[str] = None
    measurement_channel_id: Optional[str] = None

    def __post_init__(self) -> None:
        for field in ("id", "tag", "description"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        if not isinstance(self.kind, SignalKind):
            raise TypeError("kind must be a SignalKind.")
        for field in ("unit", "phase", "measurement_channel_id"):
            object.__setattr__(self, field, _optional_text(getattr(self, field), field))


@dataclass(frozen=True, slots=True)
class SignalCarrierBinding:
    """Binds a logical signal to a physical/logical carrier without owning either."""

    id: str
    signal_id: str
    carrier_kind: CarrierKind
    carrier_id: str
    direction: str = "BIDIRECTIONAL"

    def __post_init__(self) -> None:
        for field in ("id", "signal_id", "carrier_id"):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        if not isinstance(self.carrier_kind, CarrierKind):
            raise TypeError("carrier_kind must be a CarrierKind.")
        direction = _required_text(self.direction, "direction").upper()
        if direction not in {"SOURCE_TO_SINK", "SINK_TO_SOURCE", "BIDIRECTIONAL"}:
            raise ValueError("direction must be SOURCE_TO_SINK, SINK_TO_SOURCE or BIDIRECTIONAL.")
        object.__setattr__(self, "direction", direction)



@dataclass(frozen=True, slots=True)
class PhysicalConnectionHop:
    """Stable Core identity for one physical terminal-to-terminal connection.

    Hop IDs survive schedule ordering and canvas layout changes. A changed
    route retires/replaces affected hops; tuple indices are never identities.
    """

    id: str
    from_terminal: WiringTerminalReference
    to_terminal: WiringTerminalReference
    carrier_kind: CarrierKind
    carrier_id: str
    revision: int = 1
    lifecycle_status: str = "PLANNED"
    supersedes_hop_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _required_text(self.id, "id"))
        if not isinstance(self.from_terminal, WiringTerminalReference):
            raise TypeError("from_terminal must be a WiringTerminalReference.")
        if not isinstance(self.to_terminal, WiringTerminalReference):
            raise TypeError("to_terminal must be a WiringTerminalReference.")
        if self.from_terminal.identity == self.to_terminal.identity:
            raise ValueError("A physical connection hop requires two distinct terminals.")
        if not isinstance(self.carrier_kind, CarrierKind):
            raise TypeError("carrier_kind must be a CarrierKind.")
        if self.carrier_kind not in {CarrierKind.CABLE_CORE, CarrierKind.PANEL_WIRE}:
            raise ValueError("Physical connection hops require a cable-core or panel-wire carrier.")
        object.__setattr__(self, "carrier_id", _required_text(self.carrier_id, "carrier_id"))
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))
        status = _required_text(self.lifecycle_status, "lifecycle_status").upper()
        if status not in {"PLANNED", "INSTALLED", "RETIRED", "REPLACED"}:
            raise ValueError("lifecycle_status must be PLANNED, INSTALLED, RETIRED or REPLACED.")
        object.__setattr__(self, "lifecycle_status", status)
        object.__setattr__(self, "supersedes_hop_id", _optional_text(self.supersedes_hop_id, "supersedes_hop_id"))
        if self.supersedes_hop_id == self.id:
            raise ValueError("A physical connection hop cannot supersede itself.")




@dataclass(frozen=True, slots=True)
class WiringTagSeries:
    """Explicit project-defined sequential tag series for one record category."""

    record_kind: str
    prefix: str
    separator: str = ""
    minimum_digits: int = 3
    next_number: int = 1

    def __post_init__(self) -> None:
        kind = _required_text(self.record_kind, "record_kind").upper()
        prefix = _required_text(self.prefix, "prefix")
        if not isinstance(self.separator, str):
            raise TypeError("separator must be a string.")
        if self.separator != self.separator.strip():
            raise ValueError("separator must not contain leading or trailing whitespace.")
        object.__setattr__(self, "record_kind", kind)
        object.__setattr__(self, "prefix", prefix)
        object.__setattr__(self, "minimum_digits", _positive_int(self.minimum_digits, "minimum_digits"))
        object.__setattr__(self, "next_number", _positive_int(self.next_number, "next_number"))

    def format(self, number: int) -> str:
        number = _positive_int(number, "number")
        return self.prefix + self.separator + str(number).zfill(self.minimum_digits)


@dataclass(frozen=True, slots=True)
class WiringTagPolicy:
    """Project-owned tag series; no standard syntax or prefix is assumed."""

    series: tuple[WiringTagSeries, ...] = ()

    def __post_init__(self) -> None:
        values = tuple(self.series)
        if any(not isinstance(item, WiringTagSeries) for item in values):
            raise TypeError("series must contain only WiringTagSeries values.")
        kinds = [item.record_kind for item in values]
        if len(kinds) != len(set(kinds)):
            raise ValueError("Tag policy contains duplicate record_kind series.")
        object.__setattr__(self, "series", values)

    def allocate(
        self, record_kind: str, *, used_tags: tuple[str, ...] | list[str] | set[str] = ()
    ) -> tuple[str, "WiringTagPolicy"]:
        """Return the next unused tag and a new policy with an advanced counter."""
        kind = _required_text(record_kind, "record_kind").upper()
        matches = [item for item in self.series if item.record_kind == kind]
        if len(matches) != 1:
            raise ValueError(f"No unique tag series is configured for {kind!r}.")
        used_values = tuple(used_tags)
        if any(not isinstance(item, str) for item in used_values):
            raise TypeError("used_tags must contain strings.")
        used = {item.strip().casefold() for item in used_values}
        current = matches[0]
        number = current.next_number
        tag = current.format(number)
        while tag.casefold() in used:
            number += 1
            tag = current.format(number)
        advanced = WiringTagSeries(
            record_kind=current.record_kind,
            prefix=current.prefix,
            separator=current.separator,
            minimum_digits=current.minimum_digits,
            next_number=number + 1,
        )
        updated = tuple(advanced if item is current else item for item in self.series)
        return tag, WiringTagPolicy(updated)


@dataclass(frozen=True, slots=True)
class InstrumentationWiringInventory:
    """Validated project-level snapshot of instrumentation wiring records.

    This aggregate enforces cross-record identity and ownership invariants.
    Persistence and controlled mutation remain Application responsibilities.
    """

    cables: tuple[CableAssembly, ...] = ()
    cores: tuple[CableCore, ...] = ()
    panel_wires: tuple[PanelWire, ...] = ()
    signals: tuple[SignalDefinition, ...] = ()
    signal_bindings: tuple[SignalCarrierBinding, ...] = ()
    connection_hops: tuple[PhysicalConnectionHop, ...] = ()
    tag_policy: WiringTagPolicy = WiringTagPolicy()

    def __post_init__(self) -> None:
        if not isinstance(self.tag_policy, WiringTagPolicy):
            raise TypeError("tag_policy must be a WiringTagPolicy.")
        collections = (
            ("cables", self.cables, CableAssembly),
            ("cores", self.cores, CableCore),
            ("panel_wires", self.panel_wires, PanelWire),
            ("signals", self.signals, SignalDefinition),
            ("signal_bindings", self.signal_bindings, SignalCarrierBinding),
            ("connection_hops", self.connection_hops, PhysicalConnectionHop),
        )
        for name, values, expected_type in collections:
            values = tuple(values)
            if any(not isinstance(value, expected_type) for value in values):
                raise TypeError(f"{name} must contain only {expected_type.__name__} records.")
            object.__setattr__(self, name, values)
            ids = [value.id for value in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} contains duplicate stable IDs.")

        all_ids = [
            record.id
            for _, values, _ in collections
            for record in values
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Instrumentation wiring records must have globally unique stable IDs.")

        tagged_records = (
            *((item.tag, "cable") for item in self.cables),
            *((item.core_tag, "cable core") for item in self.cores if item.core_tag is not None),
            *((item.tag, "panel wire") for item in self.panel_wires),
            *((item.tag, "signal") for item in self.signals),
        )
        seen_tags: dict[str, tuple[str, str]] = {}
        for tag, label in tagged_records:
            normalized = tag.strip().casefold()
            if normalized in seen_tags:
                previous_tag, previous_label = seen_tags[normalized]
                raise ValueError(
                    f"Duplicate project wiring tag {tag!r} ({label}); "
                    f"already assigned to {previous_tag!r} ({previous_label})."
                )
            seen_tags[normalized] = (tag, label)

        channel_ids = [
            item.measurement_channel_id for item in self.signals
            if item.measurement_channel_id is not None
        ]
        if len(channel_ids) != len(set(channel_ids)):
            raise ValueError("A MeasurementChannel may be associated with only one instrumentation signal.")

        cable_by_id = {item.id: item for item in self.cables}
        core_numbers: set[tuple[str, int]] = set()
        for core in self.cores:
            cable = cable_by_id.get(core.cable_id)
            if cable is None:
                raise ValueError(f"Cable core '{core.id}' references missing cable '{core.cable_id}'.")
            if core.core_number > cable.core_count:
                raise ValueError(f"Cable core '{core.id}' exceeds cable '{cable.id}' core_count.")
            key = (core.cable_id, core.core_number)
            if key in core_numbers:
                raise ValueError(f"Cable '{core.cable_id}' has duplicate core number {core.core_number}.")
            core_numbers.add(key)

        core_by_id = {item.id: item for item in self.cores}
        wire_by_id = {item.id: item for item in self.panel_wires}
        hop_by_id = {item.id: item for item in self.connection_hops}
        for hop in self.connection_hops:
            carrier = core_by_id.get(hop.carrier_id) if hop.carrier_kind is CarrierKind.CABLE_CORE else wire_by_id.get(hop.carrier_id)
            if carrier is None:
                raise ValueError(
                    f"Physical connection hop '{hop.id}' references missing "
                    f"{hop.carrier_kind.value.lower()} '{hop.carrier_id}'."
                )
            path = carrier.terminal_path
            pair = (hop.from_terminal.identity, hop.to_terminal.identity)
            adjacent_pairs = {
                (path[index].identity, path[index + 1].identity)
                for index in range(len(path) - 1)
            }
            if hop.lifecycle_status in {"PLANNED", "INSTALLED"} and pair not in adjacent_pairs and pair[::-1] not in adjacent_pairs:
                raise ValueError(
                    f"Active physical connection hop '{hop.id}' endpoints are not an adjacent "
                    "segment of its carrier terminal path."
                )
            if hop.supersedes_hop_id is not None and hop.supersedes_hop_id not in hop_by_id:
                raise ValueError(
                    f"Physical connection hop '{hop.id}' supersedes missing hop "
                    f"'{hop.supersedes_hop_id}'."
                )

        signal_ids = {item.id for item in self.signals}
        carrier_ids = (
            {item.id for item in self.cores}
            | {item.id for item in self.panel_wires}
        )
        for binding in self.signal_bindings:
            if binding.signal_id not in signal_ids:
                raise ValueError(f"Signal binding '{binding.id}' references missing signal '{binding.signal_id}'.")
            if binding.carrier_kind is CarrierKind.CABLE_CORE and binding.carrier_id not in {item.id for item in self.cores}:
                raise ValueError(f"Signal binding '{binding.id}' references missing cable core '{binding.carrier_id}'.")
            if binding.carrier_kind is CarrierKind.PANEL_WIRE and binding.carrier_id not in {item.id for item in self.panel_wires}:
                raise ValueError(f"Signal binding '{binding.id}' references missing panel wire '{binding.carrier_id}'.")
            if binding.carrier_kind in {CarrierKind.FIBRE, CarrierKind.LOGICAL} and binding.carrier_id in carrier_ids:
                raise ValueError(
                    f"Signal binding '{binding.id}' reuses a physical core/wire ID "
                    "for a different carrier kind."
                )

    @staticmethod
    def _validate_unique_tags(records: tuple, label: str) -> None:
        tags = [record.tag for record in records]
        if len(tags) != len(set(tags)):
            raise ValueError(f"Duplicate {label} tag in wiring inventory.")


__all__ = [
    "WiringTerminalReference",
    "WiringConductorRole",
    "SignalKind",
    "CarrierKind",
    "CableAssembly",
    "CableCore",
    "PanelWire",
    "SignalDefinition",
    "SignalCarrierBinding",
    "PhysicalConnectionHop",
    "WiringTagSeries",
    "WiringTagPolicy",
    "InstrumentationWiringInventory",
]
