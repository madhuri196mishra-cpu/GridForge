"""Canonical BTD/OTD/WTD service contracts, independent of sensor technology.

The service identity (bearing/oil/winding) is not inferred from an RTD or
thermocouple type. All terminal and interface declarations are engineering
inputs; this module does not invent wiring, ranges, thresholds or settings.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
import re
from typing import Optional

from .instrument_wiring import WiringTerminalReference


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")
    return value.strip()


def _optional(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string or None.")
    return value.strip() or None


def _number(value: Optional[float], field: str) -> Optional[float]:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric.") from exc
    if not isfinite(result):
        raise ValueError(f"{field} must be finite.")
    return result


class TemperatureService(str, Enum):
    BTD = "BTD"
    OTD = "OTD"
    WTD = "WTD"


class TemperatureSensorTechnology(str, Enum):
    RTD = "RTD"
    THERMOCOUPLE = "THERMOCOUPLE"
    OTHER = "OTHER"


class TemperatureInputMode(str, Enum):
    DIRECT_SENSOR = "DIRECT_SENSOR"
    TRANSMITTER = "TRANSMITTER"
    TEMPERATURE_MONITOR = "TEMPERATURE_MONITOR"
    THERMAL_IMAGE = "THERMAL_IMAGE"


class TemperatureConsumerKind(str, Enum):
    CONTROL = "CONTROL"
    PROTECTION = "PROTECTION"
    ALARM = "ALARM"
    TRIP = "TRIP"


class TemperatureThermocoupleCableClass(str, Enum):
    EXTENSION = "EXTENSION"
    COMPENSATING = "COMPENSATING"


@dataclass(frozen=True, slots=True)
class TemperatureThermocoupleCableSpec:
    """Project-approved conductor pair for one thermocouple cable designation.

    This is a project catalogue entry, not an embedded manufacturer's catalogue.
    Material names are explicit procurement/engineering identifiers and are
    compared exactly after whitespace/case normalization.
    """

    id: str
    thermocouple_type: str
    cable_designation: str
    cable_class: TemperatureThermocoupleCableClass
    positive_conductor_material: str
    negative_conductor_material: str
    standard_reference: str
    tolerance_class: str

    def __post_init__(self) -> None:
        for field in ("id", "thermocouple_type", "cable_designation",
                      "positive_conductor_material", "negative_conductor_material",
                      "standard_reference", "tolerance_class"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        object.__setattr__(self, "thermocouple_type", self.thermocouple_type.upper())
        object.__setattr__(self, "cable_designation", self.cable_designation.upper())
        if not re.fullmatch(r"[A-Z][A-Z0-9]{0,3}", self.thermocouple_type):
            raise ValueError("thermocouple_type must be an explicit compact designation.")
        if not isinstance(self.cable_class, TemperatureThermocoupleCableClass):
            raise TypeError("cable_class must be a TemperatureThermocoupleCableClass.")
        if self.positive_conductor_material.casefold() == self.negative_conductor_material.casefold():
            raise ValueError("Positive and negative conductor materials must be separately declared.")


class TemperatureReceiverInputKind(str, Enum):
    RTD = "RTD"
    THERMOCOUPLE = "THERMOCOUPLE"
    ANALOG_CURRENT = "ANALOG_CURRENT"
    ANALOG_VOLTAGE = "ANALOG_VOLTAGE"
    DIGITAL = "DIGITAL"
    THERMAL_IMAGE = "THERMAL_IMAGE"


@dataclass(frozen=True, slots=True)
class TemperatureReceiverInput:
    """Explicit capability record for one receiver input channel."""

    id: str
    module_equipment_id: str
    channel_designation: str
    input_kind: TemperatureReceiverInputKind
    terminals: tuple[WiringTerminalReference, ...]
    engineering_unit: str
    supported_sensor_technologies: tuple[TemperatureSensorTechnology, ...] = ()
    supported_sensor_configurations: tuple[str, ...] = ()
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    range_unit: Optional[str] = None

    def __post_init__(self) -> None:
        for field in ("id", "module_equipment_id", "channel_designation", "engineering_unit"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if not isinstance(self.input_kind, TemperatureReceiverInputKind):
            raise TypeError("input_kind must be a TemperatureReceiverInputKind.")
        terminals = tuple(self.terminals)
        if not terminals or any(not isinstance(item, WiringTerminalReference) for item in terminals):
            raise ValueError("Receiver input must declare physical terminal references.")
        if any(item.equipment_id != self.module_equipment_id for item in terminals):
            raise ValueError("Receiver terminals must belong to module_equipment_id.")
        if len({item.identity for item in terminals}) != len(terminals):
            raise ValueError("Receiver input contains duplicate terminals.")
        technologies = tuple(self.supported_sensor_technologies)
        if any(not isinstance(item, TemperatureSensorTechnology) for item in technologies):
            raise TypeError("Invalid receiver sensor technology.")
        configs = tuple(_text(item, "supported_sensor_configurations entry") for item in self.supported_sensor_configurations)
        low, high = _number(self.range_min, "range_min"), _number(self.range_max, "range_max")
        range_unit = _optional(self.range_unit, "range_unit")
        if (low is None) != (high is None) or (low is not None and high <= low):
            raise ValueError("Receiver range bounds must both be supplied with maximum greater than minimum.")
        if low is not None and range_unit is None:
            raise ValueError("range_unit is required when receiver input range is declared.")
        if low is None and range_unit is not None:
            raise ValueError("range_unit cannot be declared without a receiver input range.")
        if self.input_kind in {TemperatureReceiverInputKind.RTD, TemperatureReceiverInputKind.THERMOCOUPLE} and not technologies:
            raise ValueError("Direct sensor receiver inputs must declare supported sensor technologies.")
        if self.input_kind is TemperatureReceiverInputKind.RTD and technologies != (TemperatureSensorTechnology.RTD,):
            raise ValueError("RTD receiver inputs must declare RTD technology only.")
        if self.input_kind is TemperatureReceiverInputKind.THERMOCOUPLE and technologies != (TemperatureSensorTechnology.THERMOCOUPLE,):
            raise ValueError("Thermocouple receiver inputs must declare thermocouple technology only.")
        if self.input_kind in {TemperatureReceiverInputKind.RTD, TemperatureReceiverInputKind.THERMOCOUPLE} and not configs:
            raise ValueError("Direct sensor receiver inputs must declare supported sensor configurations.")
        if self.input_kind not in {TemperatureReceiverInputKind.RTD, TemperatureReceiverInputKind.THERMOCOUPLE} and technologies:
            raise ValueError("Analog/digital receiver inputs must not declare direct sensor technologies.")
        object.__setattr__(self, "terminals", terminals)
        object.__setattr__(self, "supported_sensor_technologies", technologies)
        object.__setattr__(self, "supported_sensor_configurations", configs)
        object.__setattr__(self, "range_min", low)
        object.__setattr__(self, "range_max", high)
        object.__setattr__(self, "range_unit", range_unit)


@dataclass(frozen=True, slots=True)
class TemperatureTerminalMapping:
    """Manufacturer-declared sensor pin to interface input terminal mapping."""

    sensor_terminal: WiringTerminalReference
    interface_terminal: WiringTerminalReference
    polarity: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.sensor_terminal, WiringTerminalReference) or not isinstance(self.interface_terminal, WiringTerminalReference):
            raise TypeError("Temperature terminal mappings require canonical terminal references.")
        polarity = _optional(self.polarity, "polarity")
        if polarity is not None:
            polarity = polarity.upper()
            if polarity not in {"POSITIVE", "NEGATIVE"}:
                raise ValueError("Temperature terminal mapping polarity must be POSITIVE or NEGATIVE.")
        object.__setattr__(self, "polarity", polarity)


@dataclass(frozen=True, slots=True)
class TemperatureInstrument:
    """Service-specific temperature instrument identity and declared endpoints.

    sensor_equipment_id identifies the actual sensing element. The service
    identifies what is measured and where; technology/configuration identifies
    how it is measured. A WTD thermal-image arrangement is explicitly distinct
    from a direct winding RTD/thermocouple.
    """

    id: str
    tag: str
    service: TemperatureService
    asset_id: str
    location: str
    sensor_equipment_id: str
    sensor_technology: TemperatureSensorTechnology
    input_mode: TemperatureInputMode
    sensor_configuration: Optional[str] = None
    interface_equipment_id: Optional[str] = None
    measurement_channel_id: Optional[str] = None
    unit: Optional[str] = None
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    sensor_terminals: tuple[WiringTerminalReference, ...] = ()
    receiver_terminals: tuple[WiringTerminalReference, ...] = ()
    sensor_input_mappings: tuple[TemperatureTerminalMapping, ...] = ()
    receiver_input_id: Optional[str] = None
    thermocouple_type: Optional[str] = None

    def __post_init__(self) -> None:
        for field in ("id", "tag", "asset_id", "location", "sensor_equipment_id"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        for field, enum_type in (
            ("service", TemperatureService),
            ("sensor_technology", TemperatureSensorTechnology),
            ("input_mode", TemperatureInputMode),
        ):
            if not isinstance(getattr(self, field), enum_type):
                raise TypeError(f"{field} must be a {enum_type.__name__}.")
        for field in ("sensor_configuration", "interface_equipment_id", "measurement_channel_id", "unit", "receiver_input_id", "thermocouple_type"):
            object.__setattr__(self, field, _optional(getattr(self, field), field))
        if self.thermocouple_type is not None:
            object.__setattr__(self, "thermocouple_type", self.thermocouple_type.upper())
        if self.sensor_technology is TemperatureSensorTechnology.THERMOCOUPLE:
            if self.thermocouple_type is not None and not re.fullmatch(r"[A-Z][A-Z0-9]{0,3}", self.thermocouple_type):
                raise ValueError("thermocouple_type must be an explicit compact type designation.")
        elif self.thermocouple_type is not None:
            raise ValueError("thermocouple_type is only valid for thermocouple sensors.")
        low = _number(self.range_min, "range_min")
        high = _number(self.range_max, "range_max")
        if (low is None) != (high is None) or (low is not None and high <= low):
            raise ValueError("range_min and range_max must both be supplied with range_max > range_min.")
        object.__setattr__(self, "range_min", low)
        object.__setattr__(self, "range_max", high)
        sensor_terms = tuple(self.sensor_terminals)
        receiver_terms = tuple(self.receiver_terminals)
        mappings = tuple(self.sensor_input_mappings)
        if any(not isinstance(item, TemperatureTerminalMapping) for item in mappings):
            raise TypeError("sensor_input_mappings must contain TemperatureTerminalMapping values.")
        if any(not isinstance(item, WiringTerminalReference) for item in sensor_terms + receiver_terms):
            raise TypeError("sensor_terminals and receiver_terminals must contain WiringTerminalReference values.")
        if len({item.identity for item in sensor_terms}) != len(sensor_terms):
            raise ValueError("sensor_terminals contains duplicate terminal identities.")
        if len({item.identity for item in receiver_terms}) != len(receiver_terms):
            raise ValueError("receiver_terminals contains duplicate terminal identities.")
        if any(item.equipment_id != self.sensor_equipment_id for item in sensor_terms):
            raise ValueError("Every sensor terminal must belong to sensor_equipment_id.")
        if self.input_mode is TemperatureInputMode.DIRECT_SENSOR and self.interface_equipment_id:
            raise ValueError("DIRECT_SENSOR must not declare an intermediary interface equipment.")
        if self.input_mode is not TemperatureInputMode.DIRECT_SENSOR and not self.interface_equipment_id:
            raise ValueError("A transmitter, temperature monitor or thermal-image mode requires interface_equipment_id.")
        if self.service is TemperatureService.WTD and self.input_mode is TemperatureInputMode.THERMAL_IMAGE:
            if self.sensor_technology is not TemperatureSensorTechnology.OTHER:
                raise ValueError("THERMAL_IMAGE WTD must identify its non-direct sensing technology as OTHER.")
        if self.input_mode is TemperatureInputMode.THERMAL_IMAGE and self.service is not TemperatureService.WTD:
            raise ValueError("THERMAL_IMAGE input mode is reserved for an explicitly declared WTD.")
        if self.sensor_technology is TemperatureSensorTechnology.RTD and self.sensor_configuration:
            match = re.search(r"\b([234])\s*[- ]?wire\b", self.sensor_configuration, re.IGNORECASE)
            if match and sensor_terms and len(sensor_terms) != int(match.group(1)):
                raise ValueError(
                    f"RTD {match.group(1)}-wire configuration requires exactly {match.group(1)} declared sensor terminals."
                )
        if self.sensor_technology is TemperatureSensorTechnology.THERMOCOUPLE and sensor_terms and len(sensor_terms) != 2:
            raise ValueError("Thermocouple sensor wiring must declare exactly two measurement terminals; screen/earth is separate.")
        if len({item.sensor_terminal.identity for item in mappings}) != len(mappings):
            raise ValueError("sensor_input_mappings contains duplicate sensor pins.")
        if len({item.interface_terminal.identity for item in mappings}) != len(mappings):
            raise ValueError("sensor_input_mappings contains duplicate interface input pins.")
        if any(item.sensor_terminal not in sensor_terms for item in mappings):
            raise ValueError("Every mapped sensor pin must be in sensor_terminals.")
        if self.sensor_technology is TemperatureSensorTechnology.THERMOCOUPLE and mappings:
            polarities = {item.polarity for item in mappings}
            if None in polarities or polarities != {"POSITIVE", "NEGATIVE"}:
                raise ValueError("Thermocouple mappings must explicitly identify positive and negative polarity.")
        elif any(item.polarity is not None for item in mappings):
            raise ValueError("Polarity labels are only valid for thermocouple terminal mappings.")
        object.__setattr__(self, "sensor_terminals", sensor_terms)
        object.__setattr__(self, "receiver_terminals", receiver_terms)
        object.__setattr__(self, "sensor_input_mappings", mappings)


@dataclass(frozen=True, slots=True)
class TemperatureInterfaceDevice:
    """Declared transmitter/temperature-monitor capability and terminal contract."""

    id: str
    tag: str
    equipment_kind: TemperatureInputMode
    supported_sensor_technologies: tuple[TemperatureSensorTechnology, ...]
    supported_sensor_configurations: tuple[str, ...]
    input_terminals: tuple[WiringTerminalReference, ...]
    output_terminals: tuple[WiringTerminalReference, ...]
    output_signal_kind: str
    output_unit: str
    output_range_min: Optional[float] = None
    output_range_max: Optional[float] = None
    output_range_unit: Optional[str] = None
    measurement_channel_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "id"))
        object.__setattr__(self, "tag", _text(self.tag, "tag"))
        if self.equipment_kind not in {TemperatureInputMode.TRANSMITTER, TemperatureInputMode.TEMPERATURE_MONITOR, TemperatureInputMode.THERMAL_IMAGE}:
            raise ValueError("equipment_kind must describe an intermediary temperature interface.")
        technologies = tuple(self.supported_sensor_technologies)
        if not technologies or any(not isinstance(item, TemperatureSensorTechnology) for item in technologies):
            raise ValueError("supported_sensor_technologies must declare at least one valid technology.")
        configs = tuple(_text(item, "supported_sensor_configurations entry") for item in self.supported_sensor_configurations)
        inputs, outputs = tuple(self.input_terminals), tuple(self.output_terminals)
        if not inputs or not outputs:
            raise ValueError("Interface device must declare input and output terminal contracts.")
        if any(not isinstance(item, WiringTerminalReference) for item in inputs + outputs):
            raise TypeError("Interface terminals must be WiringTerminalReference values.")
        if any(item.equipment_id != self.id for item in inputs + outputs):
            raise ValueError("Interface terminals must belong to the interface equipment ID.")
        low, high = _number(self.output_range_min, "output_range_min"), _number(self.output_range_max, "output_range_max")
        if (low is None) != (high is None) or (low is not None and high <= low):
            raise ValueError("Output range bounds must both be supplied with maximum greater than minimum.")
        object.__setattr__(self, "supported_sensor_technologies", technologies)
        object.__setattr__(self, "supported_sensor_configurations", configs)
        object.__setattr__(self, "input_terminals", inputs)
        object.__setattr__(self, "output_terminals", outputs)
        object.__setattr__(self, "output_signal_kind", _text(self.output_signal_kind, "output_signal_kind"))
        object.__setattr__(self, "output_unit", _text(self.output_unit, "output_unit"))
        object.__setattr__(self, "output_range_min", low)
        output_range_unit = _optional(self.output_range_unit, "output_range_unit")
        if low is not None and output_range_unit is None:
            raise ValueError("output_range_unit is required when interface output range is declared.")
        if low is None and output_range_unit is not None:
            raise ValueError("output_range_unit cannot be declared without an interface output range.")
        object.__setattr__(self, "output_range_max", high)
        object.__setattr__(self, "output_range_unit", output_range_unit)
        object.__setattr__(self, "measurement_channel_id", _optional(self.measurement_channel_id, "measurement_channel_id"))


@dataclass(frozen=True, slots=True)
class TemperatureAlarmTripBinding:
    """Explicit association; thresholds and reset policy are never defaulted."""

    id: str
    instrument_id: str
    signal_id: str
    consumer_kind: TemperatureConsumerKind
    consumer_id: str
    function_id: str
    stage: str
    threshold: Optional[float] = None
    unit: Optional[str] = None
    latching: Optional[bool] = None
    reset_policy: Optional[str] = None

    def __post_init__(self) -> None:
        for field in ("id", "instrument_id", "signal_id", "consumer_id", "function_id", "stage"):
            object.__setattr__(self, field, _text(getattr(self, field), field))
        if not isinstance(self.consumer_kind, TemperatureConsumerKind):
            raise TypeError("consumer_kind must be a TemperatureConsumerKind.")
        object.__setattr__(self, "threshold", _number(self.threshold, "threshold"))
        object.__setattr__(self, "unit", _optional(self.unit, "unit"))
        object.__setattr__(self, "reset_policy", _optional(self.reset_policy, "reset_policy"))
        if self.latching is not None and not isinstance(self.latching, bool):
            raise TypeError("latching must be bool or None.")


@dataclass(frozen=True, slots=True)
class TemperatureInstrumentInventory:
    """Immutable service-specific inventory; no implicit legacy classification."""

    instruments: tuple[TemperatureInstrument, ...] = ()
    interfaces: tuple[TemperatureInterfaceDevice, ...] = ()
    receiver_inputs: tuple[TemperatureReceiverInput, ...] = ()
    alarm_trip_bindings: tuple[TemperatureAlarmTripBinding, ...] = ()
    thermocouple_cable_specs: tuple[TemperatureThermocoupleCableSpec, ...] = ()

    def __post_init__(self) -> None:
        groups = (
            ("instruments", self.instruments, TemperatureInstrument),
            ("interfaces", self.interfaces, TemperatureInterfaceDevice),
            ("receiver_inputs", self.receiver_inputs, TemperatureReceiverInput),
            ("alarm_trip_bindings", self.alarm_trip_bindings, TemperatureAlarmTripBinding),
            ("thermocouple_cable_specs", self.thermocouple_cable_specs, TemperatureThermocoupleCableSpec),
        )
        all_ids: list[str] = []
        for name, values, expected in groups:
            values = tuple(values)
            if any(not isinstance(item, expected) for item in values):
                raise TypeError(f"{name} must contain only {expected.__name__} records.")
            ids = [item.id for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} contains duplicate stable IDs.")
            all_ids.extend(ids)
            object.__setattr__(self, name, values)
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("Temperature instrument records must have globally unique IDs.")
        instruments = {item.id: item for item in self.instruments}
        interfaces = {item.id: item for item in self.interfaces}
        receiver_inputs = {item.id: item for item in self.receiver_inputs}
        receiver_channel_keys = [
            (item.module_equipment_id, item.channel_designation)
            for item in self.receiver_inputs
        ]
        if len(receiver_channel_keys) != len(set(receiver_channel_keys)):
            raise ValueError("Receiver module channel designations must be unique within each module.")
        tags = [item.tag for item in self.instruments]
        if len(tags) != len(set(tags)):
            raise ValueError("Temperature instrument tags must be unique within this inventory.")
        interface_tags = [item.tag for item in self.interfaces]
        if len(interface_tags) != len(set(interface_tags)):
            raise ValueError("Temperature interface tags must be unique within this inventory.")
        for instrument in self.instruments:
            if instrument.sensor_technology is TemperatureSensorTechnology.THERMOCOUPLE and instrument.sensor_input_mappings and not instrument.thermocouple_type:
                raise ValueError(f"Temperature instrument '{instrument.id}' has mapped thermocouple pins but no explicit thermocouple_type.")
            if instrument.interface_equipment_id:
                interface = interfaces.get(instrument.interface_equipment_id)
                if interface is None:
                    raise ValueError(f"Temperature instrument '{instrument.id}' references a missing interface device.")
                if interface.equipment_kind is not instrument.input_mode:
                    raise ValueError(f"Temperature instrument '{instrument.id}' input mode does not match its interface.")
                if instrument.sensor_technology not in interface.supported_sensor_technologies:
                    raise ValueError(f"Temperature interface '{interface.id}' does not support the declared sensor technology.")
                if instrument.sensor_configuration and interface.supported_sensor_configurations and instrument.sensor_configuration not in interface.supported_sensor_configurations:
                    raise ValueError(f"Temperature interface '{interface.id}' does not support the declared sensor configuration.")
        for instrument in self.instruments:
            if instrument.receiver_terminals and not instrument.receiver_input_id:
                raise ValueError(f"Temperature instrument '{instrument.id}' declares receiver terminals without a receiver input capability record.")
            if instrument.receiver_input_id:
                receiver = receiver_inputs.get(instrument.receiver_input_id)
                if receiver is None:
                    raise ValueError(f"Temperature instrument '{instrument.id}' references a missing receiver input.")
                if {t.identity for t in instrument.receiver_terminals} != {t.identity for t in receiver.terminals}:
                    raise ValueError(f"Temperature instrument '{instrument.id}' receiver terminals do not match its receiver input.")
                if receiver.engineering_unit != instrument.unit:
                    raise ValueError(f"Temperature instrument '{instrument.id}' unit does not match receiver input engineering unit.")
                if instrument.input_mode is TemperatureInputMode.DIRECT_SENSOR:
                    expected_technology = {
                        "RTD": TemperatureSensorTechnology.RTD,
                        "THERMOCOUPLE": TemperatureSensorTechnology.THERMOCOUPLE,
                    }.get(receiver.input_kind.value)
                    if expected_technology is None or instrument.sensor_technology is not expected_technology:
                        raise ValueError(f"Receiver input '{receiver.id}' type does not match the declared sensor technology.")
                    if not instrument.sensor_configuration or instrument.sensor_configuration not in receiver.supported_sensor_configurations:
                        raise ValueError(f"Receiver input '{receiver.id}' does not explicitly support the declared sensor configuration.")
                else:
                    interface = interfaces[instrument.interface_equipment_id]
                    signal_kind = interface.output_signal_kind.strip().upper().replace(" ", "").replace("-", "_")
                    aliases = {"4_20MA": "ANALOG_CURRENT", "0_20MA": "ANALOG_CURRENT", "0_10V": "ANALOG_VOLTAGE", "1_5V": "ANALOG_VOLTAGE", "DIGITAL": "DIGITAL", "THERMAL_IMAGE": "THERMAL_IMAGE"}
                    expected_kind = aliases.get(signal_kind)
                    if expected_kind is None or receiver.input_kind.value != expected_kind:
                        raise ValueError(f"Receiver input '{receiver.id}' capability does not match interface output signal kind '{interface.output_signal_kind}'.")
                    if receiver.engineering_unit != interface.output_unit:
                        raise ValueError(f"Receiver input '{receiver.id}' engineering unit does not match interface output unit.")
                    if (interface.output_range_min is None) != (receiver.range_min is None) or (interface.output_range_max is None) != (receiver.range_max is None) or (interface.output_range_unit is None) != (receiver.range_unit is None):
                        raise ValueError(f"Receiver input '{receiver.id}' range declaration does not match interface output range declaration.")
                    if interface.output_range_min is not None and (interface.output_range_min != receiver.range_min or interface.output_range_max != receiver.range_max or interface.output_range_unit != receiver.range_unit):
                        raise ValueError(f"Receiver input '{receiver.id}' range/value unit does not match interface output range.")
        spec_keys = [(item.thermocouple_type, item.cable_designation) for item in self.thermocouple_cable_specs]
        if len(spec_keys) != len(set(spec_keys)):
            raise ValueError("Thermocouple cable catalogue contains duplicate type/designation entries.")
        for binding in self.alarm_trip_bindings:
            if binding.instrument_id not in instruments:
                raise ValueError(f"Temperature alarm/trip binding '{binding.id}' references a missing instrument.")


__all__ = [
    "TemperatureService", "TemperatureSensorTechnology", "TemperatureInputMode",
    "TemperatureConsumerKind", "TemperatureInstrument", "TemperatureInterfaceDevice",
    "TemperatureTerminalMapping", "TemperatureReceiverInputKind", "TemperatureReceiverInput",
    "TemperatureAlarmTripBinding", "TemperatureThermocoupleCableClass",
    "TemperatureThermocoupleCableSpec", "TemperatureInstrumentInventory",
]
