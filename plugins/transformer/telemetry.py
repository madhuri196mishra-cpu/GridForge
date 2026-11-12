"""Transformer instrumentation catalogue and immutable telemetry contracts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Iterable

class MeasurementQuality(str, Enum):
    GOOD="good"; UNCERTAIN="uncertain"; BAD="bad"; NOT_CONNECTED="not_connected"
    STALE="stale"; SUBSTITUTED="substituted"; OUT_OF_RANGE="out_of_range"

class SignalKind(str, Enum):
    ANALOG="analog"; DIGITAL="digital"; COUNTER="counter"
    ESTIMATED="estimated"; DIAGNOSTIC="diagnostic"

class TransformerQuantity(str, Enum):
    VOLTAGE="voltage"; CURRENT="current"; FREQUENCY="frequency"; PHASE_ANGLE="phase_angle"
    ACTIVE_POWER="active_power"; REACTIVE_POWER="reactive_power"; APPARENT_POWER="apparent_power"
    POWER_FACTOR="power_factor"; ACTIVE_ENERGY="active_energy"; REACTIVE_ENERGY="reactive_energy"
    WINDING_TEMPERATURE="winding_temperature"; OIL_TEMPERATURE="oil_temperature"
    AMBIENT_TEMPERATURE="ambient_temperature"; HOTSPOT_TEMPERATURE="hotspot_temperature"
    CORE_TEMPERATURE="core_temperature"; OIL_LEVEL="oil_level"; OIL_PRESSURE="oil_pressure"
    OIL_FLOW="oil_flow"; OIL_MOISTURE="oil_moisture"; HYDROGEN="hydrogen"; METHANE="methane"
    ETHANE="ethane"; ETHYLENE="ethylene"; ACETYLENE="acetylene"; CARBON_MONOXIDE="carbon_monoxide"
    CARBON_DIOXIDE="carbon_dioxide"; PARTIAL_DISCHARGE="partial_discharge"
    INSULATION_POWER_FACTOR="insulation_power_factor"; CAPACITANCE="capacitance"
    NEUTRAL_CURRENT="neutral_current"; GROUND_CURRENT="ground_current"; TAP_POSITION="tap_position"
    TAP_MOTOR_CURRENT="tap_motor_current"; TAP_OPERATION_COUNT="tap_operation_count"
    COOLING_FAN_STATUS="cooling_fan_status"; COOLING_PUMP_STATUS="cooling_pump_status"
    COOLING_STAGE="cooling_stage"; BUCHHOLZ_ALARM="buchholz_alarm"
    PRESSURE_RELIEF_ALARM="pressure_relief_alarm"; SUDDEN_PRESSURE_ALARM="sudden_pressure_alarm"
    DIFFERENTIAL_OPERATE="differential_operate"; THERMAL_ALARM="thermal_alarm"
    TRIP_STATUS="trip_status"; BREAKER_STATUS="breaker_status"

@dataclass(frozen=True, slots=True)
class ChannelDefinition:
    channel_id: str
    equipment_id: str
    quantity: TransformerQuantity
    unit: str
    signal_kind: SignalKind
    subsystem: str
    description: str
    winding_id: str | None = None
    phase: str | None = None
    sensor_type: str | None = None
    source_tag: str | None = None
    sample_period_ms: int | None = None
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        for field in ("channel_id", "equipment_id", "unit", "subsystem", "description"):
            if not str(getattr(self, field)).strip():
                raise ValueError(f"{field} cannot be blank.")
        if self.phase is not None and self.phase.upper() not in {"A","B","C","N","AB","BC","CA"}:
            raise ValueError(f"Unsupported phase designation: {self.phase!r}.")
        if self.sample_period_ms is not None and self.sample_period_ms <= 0:
            raise ValueError("sample_period_ms must be positive.")
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if value is not None and not isfinite(value):
                raise ValueError(f"{name} must be finite.")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Channel minimum cannot exceed maximum.")

@dataclass(frozen=True, slots=True)
class TelemetrySample:
    channel_id: str
    equipment_id: str
    value: float | int | bool
    timestamp: datetime
    quality: MeasurementQuality = MeasurementQuality.GOOD
    source: str = ""
    sequence: int | None = None
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.channel_id.strip() or not self.equipment_id.strip():
            raise ValueError("Telemetry sample requires channel and equipment ids.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Telemetry timestamps must be timezone-aware.")
        if not isinstance(self.value, bool) and (
            not isinstance(self.value, (int, float)) or not isfinite(float(self.value))
        ):
            raise ValueError("Telemetry value must be a finite number or boolean.")
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("Telemetry sequence cannot be negative.")
        if not self.source.strip():
            raise ValueError("Telemetry sample requires source provenance.")

@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    equipment_id: str
    captured_at: datetime
    samples: tuple[TelemetrySample, ...]

    def __post_init__(self) -> None:
        if not self.equipment_id.strip():
            raise ValueError("Telemetry snapshot requires an equipment id.")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("Snapshot timestamp must be timezone-aware.")
        samples = tuple(self.samples)
        if any(sample.equipment_id != self.equipment_id for sample in samples):
            raise ValueError("Snapshot cannot contain samples for another equipment id.")
        keys = [(s.channel_id, s.timestamp, s.sequence) for s in samples]
        if len(set(keys)) != len(keys):
            raise ValueError("Snapshot contains duplicate channel observations.")
        object.__setattr__(self, "samples", samples)

    @classmethod
    def from_samples(cls, equipment_id: str, samples: Iterable[TelemetrySample],
                     captured_at: datetime | None = None) -> "TelemetrySnapshot":
        batch = tuple(samples)
        return cls(equipment_id, captured_at or datetime.now(timezone.utc), batch)

_PHASED = (
    (TransformerQuantity.VOLTAGE, "V", "electrical", "PT/VT secondary"),
    (TransformerQuantity.CURRENT, "A", "electrical", "CT secondary"),
    (TransformerQuantity.PHASE_ANGLE, "deg", "electrical", "synchronized phasor"),
)
_POWER = (
    (TransformerQuantity.ACTIVE_POWER, "MW"),
    (TransformerQuantity.REACTIVE_POWER, "MVAr"),
    (TransformerQuantity.APPARENT_POWER, "MVA"),
    (TransformerQuantity.POWER_FACTOR, "pu"),
)
_COMMON = (
    (TransformerQuantity.FREQUENCY,"Hz","electrical",SignalKind.ANALOG,"frequency transducer"),
    (TransformerQuantity.ACTIVE_ENERGY,"MWh","energy",SignalKind.ANALOG,"energy meter"),
    (TransformerQuantity.REACTIVE_ENERGY,"MVArh","energy",SignalKind.ANALOG,"energy meter"),
    (TransformerQuantity.AMBIENT_TEMPERATURE,"degC","thermal",SignalKind.ANALOG,"ambient RTD"),
    (TransformerQuantity.OIL_TEMPERATURE,"degC","thermal",SignalKind.ANALOG,"oil temperature sensor"),
    (TransformerQuantity.HOTSPOT_TEMPERATURE,"degC","thermal",SignalKind.ESTIMATED,"thermal model or fiber optic probe"),
    (TransformerQuantity.CORE_TEMPERATURE,"degC","thermal",SignalKind.ANALOG,"core temperature sensor"),
    (TransformerQuantity.OIL_LEVEL,"%","oil_system",SignalKind.ANALOG,"oil level transmitter"),
    (TransformerQuantity.OIL_PRESSURE,"kPa","oil_system",SignalKind.ANALOG,"pressure transmitter"),
    (TransformerQuantity.OIL_FLOW,"L/min","cooling",SignalKind.ANALOG,"oil flow transmitter"),
    (TransformerQuantity.OIL_MOISTURE,"ppm","insulation",SignalKind.ANALOG,"online moisture monitor"),
    (TransformerQuantity.HYDROGEN,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.METHANE,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.ETHANE,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.ETHYLENE,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.ACETYLENE,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.CARBON_MONOXIDE,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.CARBON_DIOXIDE,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.PARTIAL_DISCHARGE,"pC","insulation",SignalKind.ANALOG,"partial discharge sensor"),
    (TransformerQuantity.INSULATION_POWER_FACTOR,"%","insulation",SignalKind.ANALOG,"tan delta monitor"),
    (TransformerQuantity.CAPACITANCE,"nF","insulation",SignalKind.ANALOG,"bushing capacitance monitor"),
    (TransformerQuantity.NEUTRAL_CURRENT,"A","grounding",SignalKind.ANALOG,"neutral CT"),
    (TransformerQuantity.GROUND_CURRENT,"A","grounding",SignalKind.ANALOG,"ground CT"),
    (TransformerQuantity.TAP_POSITION,"position","tap_changer",SignalKind.DIGITAL,"tap position encoder"),
    (TransformerQuantity.TAP_MOTOR_CURRENT,"A","tap_changer",SignalKind.ANALOG,"OLTC motor current transducer"),
    (TransformerQuantity.TAP_OPERATION_COUNT,"operations","tap_changer",SignalKind.COUNTER,"OLTC operation counter"),
    (TransformerQuantity.COOLING_FAN_STATUS,"bool","cooling",SignalKind.DIGITAL,"fan auxiliary contact"),
    (TransformerQuantity.COOLING_PUMP_STATUS,"bool","cooling",SignalKind.DIGITAL,"pump auxiliary contact"),
    (TransformerQuantity.COOLING_STAGE,"stage","cooling",SignalKind.DIGITAL,"cooling stage selector"),
    (TransformerQuantity.BUCHHOLZ_ALARM,"bool","protection_indication",SignalKind.DIGITAL,"Buchholz relay contact"),
    (TransformerQuantity.PRESSURE_RELIEF_ALARM,"bool","protection_indication",SignalKind.DIGITAL,"pressure relief device contact"),
    (TransformerQuantity.SUDDEN_PRESSURE_ALARM,"bool","protection_indication",SignalKind.DIGITAL,"sudden pressure relay contact"),
    (TransformerQuantity.DIFFERENTIAL_OPERATE,"bool","protection_indication",SignalKind.DIGITAL,"87T relay indication"),
    (TransformerQuantity.THERMAL_ALARM,"bool","protection_indication",SignalKind.DIGITAL,"thermal relay indication"),
    (TransformerQuantity.TRIP_STATUS,"bool","protection_indication",SignalKind.DIGITAL,"trip circuit status"),
    (TransformerQuantity.BREAKER_STATUS,"bool","switchgear",SignalKind.DIGITAL,"breaker auxiliary contact"),
)

def build_transformer_channel_catalogue(
    equipment_id: str, winding_ids: Iterable[str], *,
    include_dga: bool = True, include_oltc: bool = True,
    include_partial_discharge: bool = True, include_bushing_monitoring: bool = True,
) -> tuple[ChannelDefinition, ...]:
    """Create stable channel definitions from explicitly declared winding ids."""
    if not equipment_id.strip():
        raise ValueError("equipment_id cannot be blank.")
    windings = tuple(str(item).strip() for item in winding_ids)
    if not windings or any(not item for item in windings):
        raise ValueError("At least one non-empty winding id is required.")
    if len(set(windings)) != len(windings):
        raise ValueError("Winding ids must be unique.")
    result: list[ChannelDefinition] = []
    seen: set[str] = set()

    def add(quantity, unit, subsystem, description, winding=None, phase=None,
            kind=SignalKind.ANALOG, sensor=None):
        parts = [equipment_id, "telemetry"]
        if winding: parts.append(winding)
        if phase: parts.append(phase)
        parts.append(quantity.value)
        channel_id = "/".join(parts)
        if channel_id in seen:
            raise ValueError(f"Duplicate generated channel id: {channel_id}")
        seen.add(channel_id)
        result.append(ChannelDefinition(
            channel_id, equipment_id, quantity, unit, kind, subsystem, description,
            winding, phase, sensor_type=sensor,
        ))

    for winding in windings:
        for quantity, unit, subsystem, sensor in _PHASED:
            for phase in ("A","B","C"):
                add(quantity, unit, subsystem, f"{quantity.value} phase {phase}",
                    winding, phase, sensor=sensor)
        for quantity, unit in _POWER:
            add(quantity, unit, "electrical", quantity.value, winding,
                kind=SignalKind.ESTIMATED, sensor="meter or synchronized V/I")
        add(TransformerQuantity.WINDING_TEMPERATURE, "degC", "thermal",
            "winding temperature", winding, sensor="winding RTD or fiber optic probe")

    for quantity, unit, subsystem, kind, sensor in _COMMON:
        if not include_dga and quantity in {
            TransformerQuantity.HYDROGEN, TransformerQuantity.METHANE,
            TransformerQuantity.ETHANE, TransformerQuantity.ETHYLENE,
            TransformerQuantity.ACETYLENE, TransformerQuantity.CARBON_MONOXIDE,
            TransformerQuantity.CARBON_DIOXIDE,
        }: continue
        if not include_oltc and quantity in {
            TransformerQuantity.TAP_POSITION, TransformerQuantity.TAP_MOTOR_CURRENT,
            TransformerQuantity.TAP_OPERATION_COUNT,
        }: continue
        if not include_partial_discharge and quantity is TransformerQuantity.PARTIAL_DISCHARGE:
            continue
        if not include_bushing_monitoring and quantity in {
            TransformerQuantity.INSULATION_POWER_FACTOR, TransformerQuantity.CAPACITANCE,
        }: continue
        add(quantity, unit, subsystem, quantity.value, kind=kind, sensor=sensor)
    return tuple(result)
