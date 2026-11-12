"""Transformer instrumentation catalogue and immutable telemetry contracts."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite

from core.model.transformer import Transformer
from core.measurement.measurement_source import MeasurementSourceInterface
from .profile import TransformerProfile, validate_transformer_profile_windings
from collections.abc import Callable, Iterable, Mapping

class MeasurementQuality(str, Enum):
    GOOD="good"; UNCERTAIN="uncertain"; BAD="bad"; NOT_CONNECTED="not_connected"
    STALE="stale"; SUBSTITUTED="substituted"; OUT_OF_RANGE="out_of_range"

def to_core_measurement_quality(quality: MeasurementQuality):
    """Map transformer telemetry quality to the canonical Core channel state."""
    from core.measurement.measurement_channel import MeasurementQuality as CoreQuality
    if not isinstance(quality, MeasurementQuality):
        raise TypeError("quality must be a transformer MeasurementQuality.")
    return {
        MeasurementQuality.GOOD: CoreQuality.GOOD,
        MeasurementQuality.UNCERTAIN: CoreQuality.SUSPECT,
        MeasurementQuality.BAD: CoreQuality.INVALID,
        MeasurementQuality.NOT_CONNECTED: CoreQuality.NOT_CONNECTED,
        MeasurementQuality.STALE: CoreQuality.STALE,
        MeasurementQuality.SUBSTITUTED: CoreQuality.SUBSTITUTED,
        MeasurementQuality.OUT_OF_RANGE: CoreQuality.OUT_OF_RANGE,
    }[quality]


def measurement_availability_for_quality(quality: MeasurementQuality) -> bool:
    if not isinstance(quality, MeasurementQuality):
        raise TypeError("quality must be a transformer MeasurementQuality.")
    return quality is not MeasurementQuality.NOT_CONNECTED


def _require_canonical_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a canonical non-empty string.")
    return value

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
    BUSHING_LEAKAGE_CURRENT="bushing_leakage_current"; OIL_DIELECTRIC_STRENGTH="oil_dielectric_strength"
    OIL_ACIDITY="oil_acidity"; OIL_INHIBITOR="oil_inhibitor"; FURAN="furan"
    DISSOLVED_OXYGEN="dissolved_oxygen"; DISSOLVED_NITROGEN="dissolved_nitrogen"
    VIBRATION="vibration"; ACOUSTIC_EMISSION="acoustic_emission"
    CORE_GROUND_CURRENT="core_ground_current"; TAP_TRANSITION_TIME="tap_transition_time"
    TAP_CONTACT_RESISTANCE="tap_contact_resistance"; FAN_RUNTIME="fan_runtime"
    PUMP_RUNTIME="pump_runtime"; BREATHER_MOISTURE="breather_moisture"
    GAS_PRESSURE="gas_pressure"; WINDING_RESISTANCE="winding_resistance"
    INSULATION_RESISTANCE="insulation_resistance"

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
            _require_canonical_text(getattr(self, field), field)
        if not isinstance(self.quantity, TransformerQuantity):
            raise TypeError("quantity must be a TransformerQuantity.")
        if not isinstance(self.signal_kind, SignalKind):
            raise TypeError("signal_kind must be a SignalKind.")
        if self.phase is not None:
            if not isinstance(self.phase, str) or self.phase.upper() not in {"A","B","C","N","AB","BC","CA"}:
                raise ValueError(f"Unsupported phase designation: {self.phase!r}.")
        if self.winding_id is not None:
            _require_canonical_text(self.winding_id, "winding_id")
        if self.sensor_type is not None:
            _require_canonical_text(self.sensor_type, "sensor_type")
        if self.source_tag is not None:
            _require_canonical_text(self.source_tag, "source_tag")
        if self.sample_period_ms is not None and (
            isinstance(self.sample_period_ms, bool)
            or not isinstance(self.sample_period_ms, int)
            or self.sample_period_ms <= 0
        ):
            raise ValueError("sample_period_ms must be a positive integer.")
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{name} must be a finite number.")
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
    diagnostic_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_canonical_text(self.channel_id, "channel_id")
        _require_canonical_text(self.equipment_id, "equipment_id")
        _require_canonical_text(self.source, "source")
        _require_canonical_text(self.unit, "unit")
        if not isinstance(self.quality, MeasurementQuality):
            raise TypeError("quality must be a MeasurementQuality.")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime.")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Telemetry timestamps must be timezone-aware.")
        if not isinstance(self.value, bool) and (
            isinstance(self.value, complex)
            or not isinstance(self.value, (int, float))
            or not isfinite(float(self.value))
        ):
            raise ValueError("Telemetry value must be a finite number or boolean.")
        if isinstance(self.diagnostic_codes, (str, bytes)):
            raise TypeError("diagnostic_codes must be an iterable of canonical strings.")
        codes = tuple(self.diagnostic_codes)
        if any(not isinstance(code, str) or not code.strip() or code != code.strip() for code in codes):
            raise ValueError("diagnostic_codes must contain canonical non-empty strings.")
        if len(set(codes)) != len(codes):
            raise ValueError("diagnostic_codes cannot contain duplicates.")
        object.__setattr__(self, "diagnostic_codes", codes)
        if self.sequence is not None and (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("Telemetry sequence must be a non-negative integer.")

    @property
    def core_quality(self):
        """Return the equivalent canonical Core MeasurementQuality."""
        return to_core_measurement_quality(self.quality)

    @property
    def available(self) -> bool:
        """Return canonical channel availability implied by source quality."""
        return measurement_availability_for_quality(self.quality)

@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    equipment_id: str
    captured_at: datetime
    samples: tuple[TelemetrySample, ...]

    def __post_init__(self) -> None:
        _require_canonical_text(self.equipment_id, "equipment_id")
        if not isinstance(self.captured_at, datetime):
            raise TypeError("captured_at must be a datetime.")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("Snapshot timestamp must be timezone-aware.")
        samples = tuple(self.samples)
        if any(not isinstance(sample, TelemetrySample) for sample in samples):
            raise TypeError("Snapshot samples must be TelemetrySample values.")
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

def build_transformer_channel_catalogue_for_core(
    transformer: Transformer,
    profile: TransformerProfile,
    *,
    include_dga: bool = True,
    include_oltc: bool = True,
    include_partial_discharge: bool = True,
    include_bushing_monitoring: bool = True,
) -> tuple[ChannelDefinition, ...]:
    """Build telemetry points only from winding IDs declared by the Core Transformer."""
    winding_ids = validate_transformer_profile_windings(profile, transformer)
    return build_transformer_channel_catalogue(
        transformer.id,
        winding_ids,
        include_dga=include_dga,
        include_oltc=include_oltc,
        include_partial_discharge=include_partial_discharge,
        include_bushing_monitoring=include_bushing_monitoring,
    )


def build_transformer_measurement_source_interfaces(
    catalogue: Iterable[ChannelDefinition],
    channel_ids: Iterable[str],
) -> tuple[MeasurementSourceInterface, ...]:
    """Project selected physical-sensor points into Core source-interface records.

    The caller selects channels belonging to one real instrument. This helper
    does not invent a source identity, assert that hardware is installed, or
    provision a project source/installation.
    """
    points = tuple(catalogue)
    by_id = {point.channel_id: point for point in points}
    if len(by_id) != len(points):
        raise ValueError("Transformer telemetry catalogue contains duplicate channel IDs.")
    selected_ids = tuple(channel_ids)
    if not selected_ids:
        raise ValueError("At least one telemetry channel must be selected.")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("Selected telemetry channel IDs must be unique.")
    result: list[MeasurementSourceInterface] = []
    for channel_id in selected_ids:
        point = by_id.get(channel_id)
        if point is None:
            raise KeyError(f"Transformer telemetry channel not found: {channel_id!r}.")
        if point.sensor_type is None:
            raise ValueError(f"Channel {channel_id!r} has no declared physical sensor type.")
        if point.signal_kind is SignalKind.ESTIMATED:
            raise ValueError(
                f"Estimated channel {channel_id!r} is not a physical measurement-source interface."
            )
        result.append(MeasurementSourceInterface(
            interface_id=point.channel_id,
            quantity=point.quantity.value,
            unit=point.unit,
            signal_kind=point.signal_kind.value,
            phase=point.phase,
            winding_id=point.winding_id,
            minimum=point.minimum,
            maximum=point.maximum,
        ))
    return tuple(result)



def publish_transformer_sensor_snapshot(
    snapshot: TelemetrySnapshot,
    catalogue: Iterable[ChannelDefinition],
    *,
    bindings: Iterable[object],
    measurement_channels: Mapping[str, object],
    instrumentation: object,
    attestations: Mapping[str, object],
    publish_sample: Callable[..., object],
) -> tuple[object, ...]:
    """Publish attested physical sensor samples through canonical Application ingress.

    Binding IDs are translated explicitly from plugin telemetry point IDs to
    canonical Core MeasurementChannel IDs. Attestations are keyed by and must
    identify those Core channel IDs, never an untrusted plugin-local alias.
    """
    from math import isclose
    from .measurement_binding import resolve_transformer_measurement_bindings

    if not isinstance(attestations, Mapping):
        raise TypeError("attestations must map Core channel IDs to signed producer attestations.")
    if not isinstance(measurement_channels, Mapping):
        raise TypeError("measurement_channels must be the active Application channel mapping.")
    if not callable(publish_sample):
        raise TypeError("publish_sample must be the canonical Application sample-ingress callable.")

    points = tuple(catalogue)
    validate_transformer_snapshot(snapshot, points)
    resolved = resolve_transformer_measurement_bindings(
        points,
        bindings,
        measurement_channels,
        instrumentation=instrumentation,
    )
    core_by_point = {point.channel_id: channel for point, channel in resolved}
    sample_points = {sample.channel_id for sample in snapshot.samples}
    if not sample_points.issubset(core_by_point):
        missing = sorted(sample_points - set(core_by_point))
        raise ValueError(f"Transformer telemetry samples have no explicit Core channel binding: {missing!r}.")
    if any(
        by_id.sensor_type is None or by_id.signal_kind is SignalKind.ESTIMATED
        for by_id in (point for point in points if point.channel_id in sample_points)
    ):
        raise ValueError("Only declared physical sensor points can use the physical sensor handoff.")

    core_ids = {str(getattr(core_by_point[point_id], "id", "")) for point_id in sample_points}
    if "" in core_ids or len(core_ids) != len(sample_points):
        raise ValueError("Resolved Core channels must expose unique canonical IDs.")
    if set(attestations) != core_ids:
        raise ValueError("Exactly one producer attestation keyed by Core channel ID is required per sample.")

    results: list[object] = []
    for sample in snapshot.samples:
        channel = core_by_point[sample.channel_id]
        core_channel_id = str(channel.id)
        attestation = attestations[core_channel_id]
        expected_quality = sample.core_quality
        expected_available = sample.available
        if getattr(attestation, "channel_id", None) != core_channel_id:
            raise ValueError("Producer attestation channel identity does not match its resolved Core channel.")
        if getattr(attestation, "sample_sequence", None) != sample.sequence or sample.sequence is None:
            raise ValueError("Physical sensor sample and attestation require the same explicit producer sequence.")
        if getattr(attestation, "quality", None) is not expected_quality:
            raise ValueError("Producer attestation quality does not match its telemetry sample.")
        if getattr(attestation, "available", None) is not expected_available:
            raise ValueError("Producer attestation availability does not match its telemetry sample.")
        if tuple(getattr(attestation, "diagnostic_codes", ())) != sample.diagnostic_codes:
            raise ValueError("Producer attestation diagnostics do not match its telemetry sample.")
        attested_timestamp = getattr(attestation, "timestamp", None)
        if (
            isinstance(attested_timestamp, bool)
            or not isinstance(attested_timestamp, (int, float))
            or not isfinite(attested_timestamp)
            or not isclose(
                float(attested_timestamp),
                sample.timestamp.timestamp(),
                rel_tol=0.0,
                abs_tol=1e-6,
            )
        ):
            raise ValueError("Producer attestation timestamp must match the sample's UTC Unix timestamp.")
        results.append(
            publish_sample(
                core_channel_id,
                sample.value,
                attestation=attestation,
                quality=expected_quality,
                available=expected_available,
            )
        )
    return tuple(results)


def publish_active_transformer_sensor_snapshot(
    application: object,
    snapshot: TelemetrySnapshot,
    catalogue: Iterable[ChannelDefinition],
    *,
    bindings: Iterable[object],
    attestations: Mapping[str, object],
) -> tuple[object, ...]:
    """Resolve active project authorities and publish through Application.

    Intended for a transformer plugin initialized with the host-created
    EngineeringPluginContext. It fails closed when project, Network,
    instrumentation, or MeasurementChannel activation identities disagree.
    """
    from core.application.instrumentation import ProjectInstrumentationCollection

    lifecycle = getattr(application, "project_lifecycle", None)
    context = getattr(lifecycle, "context", None)
    network = getattr(lifecycle, "network", None)
    service = getattr(application, "measurement_channel_service", None)
    instrumentation = getattr(application, "instrumentation_collection", None)
    if context is None or network is None or service is None:
        raise RuntimeError("Transformer telemetry publication requires an active Application project.")
    if not isinstance(instrumentation, ProjectInstrumentationCollection):
        raise RuntimeError("Active Application project has no canonical instrumentation collection.")
    if (
        instrumentation.project_id != context.project_id
        or service.project_id != context.project_id
        or service.network is not network
        or getattr(lifecycle, "state", None) != "ACTIVE"
        or service.activation_generation <= 0
        or service.activation_generation != getattr(lifecycle, "activation_generation", None)
    ):
        raise RuntimeError("Application project, instrumentation and measurement activation identities disagree.")
    return publish_transformer_sensor_snapshot(
        snapshot,
        catalogue,
        bindings=bindings,
        measurement_channels=service.channels,
        instrumentation=instrumentation,
        attestations=attestations,
        publish_sample=service.publish_sample,
    )

def validate_transformer_snapshot(
    snapshot: TelemetrySnapshot,
    catalogue: Iterable[ChannelDefinition],
) -> None:
    """Validate samples against the transformer catalogue; does not publish data."""
    if not isinstance(snapshot, TelemetrySnapshot):
        raise TypeError("snapshot must be a TelemetrySnapshot.")
    catalogue_items = tuple(catalogue)
    points = {point.channel_id: point for point in catalogue_items}
    if len(points) != len(catalogue_items):
        raise ValueError("Transformer telemetry catalogue contains duplicate channel ids.")
    if any(point.equipment_id != snapshot.equipment_id for point in catalogue_items):
        raise ValueError("Catalogue contains a point for another transformer equipment id.")

    for sample in snapshot.samples:
        point = points.get(sample.channel_id)
        if point is None:
            raise KeyError(f"Telemetry sample references an unknown catalogue point: {sample.channel_id}")
        if sample.equipment_id != point.equipment_id:
            raise ValueError("Telemetry sample equipment identity does not match its catalogue point.")
        if sample.unit.casefold() != point.unit.casefold():
            raise ValueError(
                f"Telemetry unit mismatch for {sample.channel_id}: "
                f"sample={sample.unit!r}, catalogue={point.unit!r}."
            )
        if sample.timestamp > snapshot.captured_at:
            raise ValueError("Telemetry sample timestamp cannot be later than its snapshot capture time.")
        value = sample.value
        if point.signal_kind is SignalKind.COUNTER:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Counter point {sample.channel_id} requires a non-negative integer.")
        elif point.signal_kind is SignalKind.DIGITAL:
            if not isinstance(value, (bool, int)):
                raise ValueError(f"Digital point {sample.channel_id} requires a boolean or integer state.")
        elif point.signal_kind in {SignalKind.ANALOG, SignalKind.ESTIMATED}:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Analog/estimated point {sample.channel_id} requires a numeric value.")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if point.minimum is not None and value < point.minimum:
                raise ValueError(f"Telemetry value is below the configured minimum for {sample.channel_id}.")
            if point.maximum is not None and value > point.maximum:
                raise ValueError(f"Telemetry value exceeds the configured maximum for {sample.channel_id}.")


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
    (TransformerQuantity.BUSHING_LEAKAGE_CURRENT,"mA","bushing",SignalKind.ANALOG,"bushing leakage CT"),
    (TransformerQuantity.OIL_DIELECTRIC_STRENGTH,"kV","oil_quality",SignalKind.ANALOG,"oil dielectric strength tester"),
    (TransformerQuantity.OIL_ACIDITY,"mgKOH/g","oil_quality",SignalKind.ANALOG,"oil acidity monitor or lab result"),
    (TransformerQuantity.OIL_INHIBITOR,"%","oil_quality",SignalKind.ANALOG,"oil inhibitor monitor or lab result"),
    (TransformerQuantity.FURAN,"ppm","oil_quality",SignalKind.ANALOG,"oil furan laboratory result"),
    (TransformerQuantity.DISSOLVED_OXYGEN,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.DISSOLVED_NITROGEN,"ppm","dga",SignalKind.ANALOG,"dissolved gas monitor"),
    (TransformerQuantity.VIBRATION,"mm/s","mechanical",SignalKind.ANALOG,"vibration sensor"),
    (TransformerQuantity.ACOUSTIC_EMISSION,"dB","mechanical",SignalKind.ANALOG,"acoustic emission sensor"),
    (TransformerQuantity.CORE_GROUND_CURRENT,"mA","grounding",SignalKind.ANALOG,"core ground CT"),
    (TransformerQuantity.TAP_TRANSITION_TIME,"ms","tap_changer",SignalKind.ANALOG,"OLTC timing monitor"),
    (TransformerQuantity.TAP_CONTACT_RESISTANCE,"microohm","tap_changer",SignalKind.ANALOG,"dynamic resistance measurement"),
    (TransformerQuantity.FAN_RUNTIME,"h","cooling",SignalKind.COUNTER,"fan runtime counter"),
    (TransformerQuantity.PUMP_RUNTIME,"h","cooling",SignalKind.COUNTER,"pump runtime counter"),
    (TransformerQuantity.BREATHER_MOISTURE,"%","oil_system",SignalKind.ANALOG,"breather humidity sensor"),
    (TransformerQuantity.GAS_PRESSURE,"kPa","gas_system",SignalKind.ANALOG,"gas pressure transmitter"),
    (TransformerQuantity.WINDING_RESISTANCE,"ohm","diagnostics",SignalKind.ANALOG,"winding resistance test set"),
    (TransformerQuantity.INSULATION_RESISTANCE,"Mohm","diagnostics",SignalKind.ANALOG,"insulation resistance test set"),
)

def build_transformer_channel_catalogue(
    equipment_id: str, winding_ids: Iterable[str], *,
    include_dga: bool = True, include_oltc: bool = True,
    include_partial_discharge: bool = True, include_bushing_monitoring: bool = True,
) -> tuple[ChannelDefinition, ...]:
    """Create stable channel definitions from explicitly declared winding ids."""
    _require_canonical_text(equipment_id, "equipment_id")
    for flag_name, flag_value in (
        ("include_dga", include_dga),
        ("include_oltc", include_oltc),
        ("include_partial_discharge", include_partial_discharge),
        ("include_bushing_monitoring", include_bushing_monitoring),
    ):
        if not isinstance(flag_value, bool):
            raise TypeError(f"{flag_name} must be a boolean.")
    windings = tuple(winding_ids)
    if not windings:
        raise ValueError("At least one non-empty winding id is required.")
    for winding_id in windings:
        _require_canonical_text(winding_id, "winding_id")
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
            TransformerQuantity.CARBON_DIOXIDE, TransformerQuantity.DISSOLVED_OXYGEN,
            TransformerQuantity.DISSOLVED_NITROGEN,
        }: continue
        if not include_oltc and quantity in {
            TransformerQuantity.TAP_POSITION, TransformerQuantity.TAP_MOTOR_CURRENT,
            TransformerQuantity.TAP_OPERATION_COUNT, TransformerQuantity.TAP_TRANSITION_TIME,
            TransformerQuantity.TAP_CONTACT_RESISTANCE,
        }: continue
        if not include_partial_discharge and quantity is TransformerQuantity.PARTIAL_DISCHARGE:
            continue
        if not include_bushing_monitoring and quantity in {
            TransformerQuantity.INSULATION_POWER_FACTOR, TransformerQuantity.CAPACITANCE,
            TransformerQuantity.BUSHING_LEAKAGE_CURRENT,
        }: continue
        add(quantity, unit, subsystem, quantity.value, kind=kind, sensor=sensor)
    return tuple(result)
