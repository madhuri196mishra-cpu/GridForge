"""Generator instrumentation templates bound to canonical measurement channels.

Templates describe logical points only. Physical CT/PT/CVT/transducer sources
and their authoritative terminals must be explicitly supplied. This module
never fabricates instruments or bypasses Core MeasurementProvisioning.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.model import EndpointReference
from core.measurement.measurement_channel import (
    MeasurementChannel, MeasurementPhase, MeasurementSignalType,
)
from core.measurement.measurement_provisioning import MeasurementProvisioning

from .catalog import GeneratorFamily


@dataclass(frozen=True, slots=True)
class GeneratorMeasurementPoint:
    point_id: str
    name: str
    signal_type: MeasurementSignalType
    phase: MeasurementPhase
    unit: str
    consumers: frozenset[str]
    description: str = ""

    def __post_init__(self) -> None:
        for field_name in ("point_id", "name", "unit"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.signal_type, MeasurementSignalType):
            raise TypeError("signal_type must be a MeasurementSignalType")
        if not isinstance(self.phase, MeasurementPhase):
            raise TypeError("phase must be a MeasurementPhase")
        if not isinstance(self.consumers, frozenset) or not self.consumers:
            raise TypeError("consumers must be a non-empty frozenset")


def _point(pid: str, name: str, kind: MeasurementSignalType, phase: MeasurementPhase,
           unit: str, consumers: tuple[str, ...]) -> GeneratorMeasurementPoint:
    return GeneratorMeasurementPoint(pid, name, kind, phase, unit, frozenset(consumers))


def generator_measurement_points(family: GeneratorFamily) -> tuple[GeneratorMeasurementPoint, ...]:
    """Return deterministic electrical, machine, control and protection points."""
    if not isinstance(family, GeneratorFamily):
        raise TypeError("family must be a GeneratorFamily")
    C, V, P, F, A, D = (
        MeasurementSignalType.CURRENT, MeasurementSignalType.VOLTAGE,
        MeasurementSignalType.POWER, MeasurementSignalType.FREQUENCY,
        MeasurementSignalType.PHASE_ANGLE, MeasurementSignalType.DIGITAL,
    )
    N, PA, PB, PC, AB, BC, CA, T = (
        MeasurementPhase.NONE, MeasurementPhase.A, MeasurementPhase.B,
        MeasurementPhase.C, MeasurementPhase.AB, MeasurementPhase.BC,
        MeasurementPhase.CA, MeasurementPhase.THREE_PHASE,
    )
    points = [
        _point("terminal.ia", "Generator phase-A current", C, PA, "A", ("metering", "protection", "scada")),
        _point("terminal.ib", "Generator phase-B current", C, PB, "A", ("metering", "protection", "scada")),
        _point("terminal.ic", "Generator phase-C current", C, PC, "A", ("metering", "protection", "scada")),
        _point("terminal.in", "Generator neutral current", C, N, "A", ("protection", "monitoring")),
        _point("terminal.va", "Generator phase-A voltage", V, PA, "V", ("metering", "protection", "control")),
        _point("terminal.vb", "Generator phase-B voltage", V, PB, "V", ("metering", "protection", "control")),
        _point("terminal.vc", "Generator phase-C voltage", V, PC, "V", ("metering", "protection", "control")),
        _point("terminal.vab", "Generator phase-AB voltage", V, AB, "V", ("metering", "synchronization")),
        _point("terminal.vbc", "Generator phase-BC voltage", V, BC, "V", ("metering", "synchronization")),
        _point("terminal.vca", "Generator phase-CA voltage", V, CA, "V", ("metering", "synchronization")),
        _point("electrical.active_power", "Active power", P, T, "MW", ("metering", "scada", "control")),
        _point("electrical.reactive_power", "Reactive power", P, T, "MVAr", ("metering", "scada", "control")),
        _point("electrical.apparent_power", "Apparent power", P, T, "MVA", ("metering", "scada")),
        _point("electrical.power_factor", "Power factor", P, T, "pu", ("metering", "scada")),
        _point("electrical.frequency", "Terminal frequency", F, N, "Hz", ("protection", "control", "scada")),
        _point("electrical.phase_angle", "Terminal phase angle", A, T, "deg", ("synchronization", "protection")),
        _point("machine.speed", "Rotor speed", MeasurementSignalType.CUSTOM, N, "rpm", ("control", "protection", "monitoring")),
        _point("machine.shaft_torque", "Shaft torque", MeasurementSignalType.CUSTOM, N, "N.m", ("monitoring", "analysis")),
        _point("machine.vibration", "Machine vibration", MeasurementSignalType.CUSTOM, N, "mm/s", ("monitoring", "condition_inference")),
        _point("machine.bearing_temperature", "Bearing temperature", MeasurementSignalType.CUSTOM, N, "degC", ("monitoring", "alarm")),
        _point("stator.winding_temperature", "Stator winding temperature", MeasurementSignalType.CUSTOM, T, "degC", ("monitoring", "alarm", "protection")),
        _point("excitation.field_voltage", "Excitation field voltage", V, N, "V", ("control", "monitoring")),
        _point("excitation.field_current", "Excitation field current", C, N, "A", ("control", "monitoring", "protection")),
        _point("governor.speed_reference", "Governor speed reference", MeasurementSignalType.CUSTOM, N, "rpm", ("control", "monitoring")),
        _point("governor.actuator_position", "Governor actuator position", MeasurementSignalType.CUSTOM, N, "%", ("control", "monitoring")),
        _point("synchronization.voltage_difference", "Synchronizing voltage difference", V, T, "V", ("synchronization", "control")),
        _point("synchronization.frequency_difference", "Synchronizing frequency difference", F, N, "Hz", ("synchronization", "control")),
        _point("synchronization.angle_difference", "Synchronizing angle difference", A, T, "deg", ("synchronization", "control")),
        _point("breaker.position", "Generator breaker position", D, N, "state", ("control", "protection", "scada")),
        _point("protection.trip_status", "Generator protection trip status", D, N, "state", ("protection", "scada")),
    ]
    if family in {GeneratorFamily.DIESEL, GeneratorFamily.GAS_ENGINE, GeneratorFamily.GAS_TURBINE, GeneratorFamily.MICRO_TURBINE}:
        points.extend((
            _point("prime_mover.fuel_pressure", "Fuel supply pressure", MeasurementSignalType.CUSTOM, N, "kPa", ("monitoring", "alarm")),
            _point("prime_mover.fuel_flow", "Fuel flow", MeasurementSignalType.CUSTOM, N, "kg/h", ("monitoring", "analysis")),
            _point("prime_mover.exhaust_temperature", "Exhaust temperature", MeasurementSignalType.CUSTOM, N, "degC", ("monitoring", "alarm")),
            _point("prime_mover.oil_pressure", "Lubricating oil pressure", MeasurementSignalType.CUSTOM, N, "kPa", ("monitoring", "alarm", "protection")),
        ))
    if family in {GeneratorFamily.STEAM_TURBINE, GeneratorFamily.GAS_TURBINE, GeneratorFamily.HYDRO}:
        points.extend((
            _point("prime_mover.governor_valve_position", "Governor valve/gate position", MeasurementSignalType.CUSTOM, N, "%", ("control", "monitoring")),
            _point("prime_mover.inlet_pressure", "Prime mover inlet pressure", MeasurementSignalType.CUSTOM, N, "kPa", ("monitoring", "alarm")),
        ))
    if family in {GeneratorFamily.WIND_DFIG, GeneratorFamily.WIND_FULL_CONVERTER, GeneratorFamily.INVERTER_BASED, GeneratorFamily.FUEL_CELL}:
        points.extend((
            _point("converter.dc_link_voltage", "Converter DC-link voltage", V, N, "V", ("control", "protection", "monitoring")),
            _point("converter.dc_link_current", "Converter DC-link current", C, N, "A", ("control", "protection", "monitoring")),
            _point("converter.active_current_reference", "Converter active current reference", C, N, "A", ("control", "monitoring")),
            _point("converter.reactive_current_reference", "Converter reactive current reference", C, N, "A", ("control", "monitoring")),
            _point("converter.thermal_status", "Converter thermal status", D, N, "state", ("monitoring", "alarm")),
        ))
    if len({point.point_id for point in points}) != len(points):
        raise RuntimeError("generator measurement template contains duplicate point IDs")
    return tuple(points)


def provision_generator_measurement_channels(
    *,
    generator_id: str,
    family: GeneratorFamily,
    sources: Mapping[str, tuple[Any, Any, EndpointReference]],
) -> tuple[MeasurementChannel, ...]:
    """Provision configured point channels from explicit physical source terminals.

    The sources mapping must cover the selected family template exactly.
    Every value is (source equipment, authoritative Core terminal,
    EndpointReference). No synthetic or unconnected channel is created.
    """
    if not isinstance(generator_id, str) or not generator_id.strip():
        raise ValueError("generator_id must be a non-empty string")
    points = generator_measurement_points(family)
    expected = {point.point_id for point in points}
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping")
    supplied = set(sources)
    if supplied != expected:
        missing, extra = sorted(expected - supplied), sorted(supplied - expected)
        raise ValueError(f"instrument source mapping mismatch; missing={missing}, extra={extra}")
    channels = []
    for point in points:
        source, terminal, reference = sources[point.point_id]
        channels.append(MeasurementProvisioning.provision_channel(
            source=source,
            source_terminal=terminal,
            source_terminal_reference=reference,
            channel_id=f"{generator_id}.{point.point_id}",
            signal_type=point.signal_type,
            name=point.name,
            phase=point.phase,
            unit=point.unit,
        ))
    return tuple(channels)


__all__ = [
    "GeneratorMeasurementPoint", "generator_measurement_points",
    "provision_generator_measurement_channels",
]
