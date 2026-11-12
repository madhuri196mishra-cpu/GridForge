"""Load instrumentation templates; physical sources must be explicitly wired."""
from __future__ import annotations

from dataclasses import dataclass

from core.measurement.measurement_channel import MeasurementPhase, MeasurementSignalType

from .catalog import LoadFamily


@dataclass(frozen=True, slots=True)
class LoadMeasurementPoint:
    point_id: str
    name: str
    signal_type: MeasurementSignalType
    phase: MeasurementPhase
    unit: str
    consumers: frozenset[str]
    families: frozenset[LoadFamily]
    description: str = ""

    def __post_init__(self) -> None:
        for field_name in ("point_id", "name", "unit"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.signal_type, MeasurementSignalType):
            raise TypeError("signal_type must be MeasurementSignalType")
        if not isinstance(self.phase, MeasurementPhase):
            raise TypeError("phase must be MeasurementPhase")
        if not isinstance(self.consumers, frozenset) or not self.consumers:
            raise TypeError("consumers must be a non-empty frozenset")
        if not isinstance(self.families, frozenset) or not self.families:
            raise TypeError("families must be a non-empty frozenset")


_ALL = frozenset(LoadFamily)
_MOTORS = frozenset({
    LoadFamily.INDUCTION_MOTOR, LoadFamily.SYNCHRONOUS_MOTOR, LoadFamily.PMSM,
    LoadFamily.BLDC, LoadFamily.DC_MOTOR, LoadFamily.COMPRESSOR, LoadFamily.PUMP,
    LoadFamily.FAN, LoadFamily.VFD_FED, LoadFamily.TRACTION,
})
_THERMAL = frozenset({
    LoadFamily.RESISTIVE, LoadFamily.RESISTANCE_FURNACE, LoadFamily.INDUCTION_FURNACE,
    LoadFamily.ARC_FURNACE, LoadFamily.WELDING, LoadFamily.HVAC, LoadFamily.CHILLER,
    LoadFamily.HEAT_PUMP, LoadFamily.REFRIGERATION,
})
_POWER_ELECTRONIC = frozenset({
    LoadFamily.RECTIFIER, LoadFamily.UPS, LoadFamily.SMPS, LoadFamily.EV_CHARGER,
    LoadFamily.BATTERY_CHARGER, LoadFamily.DATA_CENTRE, LoadFamily.ELECTROLYSER,
    LoadFamily.TRACTION, LoadFamily.VFD_FED,
})


def _point(pid: str, name: str, kind: MeasurementSignalType, phase: MeasurementPhase,
           unit: str, consumers: tuple[str, ...], families: frozenset[LoadFamily] = _ALL) -> LoadMeasurementPoint:
    return LoadMeasurementPoint(pid, name, kind, phase, unit, frozenset(consumers), families)


def load_measurement_points(family: LoadFamily) -> tuple[LoadMeasurementPoint, ...]:
    """Return stable, family-filtered electrical, process, thermal and status points."""
    if not isinstance(family, LoadFamily):
        raise TypeError("family must be LoadFamily")
    C, V, P, F, A, D, X = (
        MeasurementSignalType.CURRENT, MeasurementSignalType.VOLTAGE,
        MeasurementSignalType.POWER, MeasurementSignalType.FREQUENCY,
        MeasurementSignalType.PHASE_ANGLE, MeasurementSignalType.DIGITAL,
        MeasurementSignalType.CUSTOM,
    )
    N, PA, PB, PC, AB, BC, CA, T = (
        MeasurementPhase.NONE, MeasurementPhase.A, MeasurementPhase.B,
        MeasurementPhase.C, MeasurementPhase.AB, MeasurementPhase.BC,
        MeasurementPhase.CA, MeasurementPhase.THREE_PHASE,
    )
    definitions = (
        _point("terminal.ia", "Phase-A current", C, PA, "A", ("metering", "protection", "scada")),
        _point("terminal.ib", "Phase-B current", C, PB, "A", ("metering", "protection", "scada")),
        _point("terminal.ic", "Phase-C current", C, PC, "A", ("metering", "protection", "scada")),
        _point("terminal.in", "Neutral current", C, N, "A", ("protection", "monitoring")),
        _point("terminal.va", "Phase-A voltage", V, PA, "V", ("metering", "protection", "control")),
        _point("terminal.vb", "Phase-B voltage", V, PB, "V", ("metering", "protection", "control")),
        _point("terminal.vc", "Phase-C voltage", V, PC, "V", ("metering", "protection", "control")),
        _point("terminal.vab", "Phase-AB voltage", V, AB, "V", ("metering", "protection")),
        _point("terminal.vbc", "Phase-BC voltage", V, BC, "V", ("metering", "protection")),
        _point("terminal.vca", "Phase-CA voltage", V, CA, "V", ("metering", "protection")),
        _point("electrical.active_power", "Active power", P, T, "kW", ("metering", "scada", "control")),
        _point("electrical.reactive_power", "Reactive power", P, T, "kvar", ("metering", "scada", "control")),
        _point("electrical.apparent_power", "Apparent power", P, T, "kVA", ("metering", "scada")),
        _point("electrical.power_factor", "Power factor", P, T, "pu", ("metering", "scada")),
        _point("electrical.frequency", "Frequency", F, N, "Hz", ("protection", "control", "scada")),
        _point("electrical.phase_angle", "Phase angle", A, T, "deg", ("analysis", "protection")),
        _point("electrical.energy_import", "Imported energy", P, T, "kWh", ("metering", "billing")),
        _point("quality.thd_voltage", "Voltage total harmonic distortion", X, T, "%", ("power_quality", "monitoring")),
        _point("quality.thd_current", "Current total harmonic distortion", X, T, "%", ("power_quality", "monitoring")),
        _point("quality.voltage_unbalance", "Voltage unbalance", X, T, "%", ("power_quality", "monitoring")),
        _point("quality.current_unbalance", "Current unbalance", X, T, "%", ("power_quality", "monitoring")),
        _point("quality.flicker", "Voltage flicker severity", X, T, "pu", ("power_quality", "monitoring")),
        _point("status.in_service", "Load service status", D, N, "state", ("control", "scada")),
        _point("status.trip", "Load trip status", D, N, "state", ("protection", "scada")),
        _point("process.demand", "Process demand", X, N, "%", ("control", "monitoring")),
        _point("process.duty_cycle", "Process duty cycle", X, N, "%", ("analysis", "monitoring")),
        _point("thermal.winding_temperature", "Winding temperature", X, N, "degC", ("monitoring", "protection"), _MOTORS),
        _point("thermal.bearing_temperature", "Bearing temperature", X, N, "degC", ("monitoring", "protection"), _MOTORS),
        _point("mechanical.speed", "Shaft speed", X, N, "rpm", ("control", "monitoring"), _MOTORS),
        _point("mechanical.torque", "Shaft torque", X, N, "N.m", ("analysis", "monitoring"), _MOTORS),
        _point("mechanical.vibration", "Vibration velocity", X, N, "mm/s", ("condition_monitoring",), _MOTORS),
        _point("thermal.surface_temperature", "Equipment surface temperature", X, N, "degC", ("monitoring", "alarm"), _THERMAL),
        _point("process.coolant_flow", "Coolant flow", X, N, "m3/h", ("monitoring", "alarm"), _THERMAL),
        _point("converter.dc_voltage", "DC-link voltage", V, N, "V", ("control", "protection", "monitoring"), _POWER_ELECTRONIC),
        _point("converter.dc_current", "DC-link current", C, N, "A", ("control", "protection", "monitoring"), _POWER_ELECTRONIC),
        _point("converter.switching_temperature", "Power-semiconductor temperature", X, N, "degC", ("monitoring", "protection"), _POWER_ELECTRONIC),
        _point("battery.state_of_charge", "Battery state of charge", X, N, "%", ("control", "monitoring"), frozenset({LoadFamily.EV_CHARGER, LoadFamily.BATTERY_CHARGER, LoadFamily.UPS})),
        _point("battery.charge_power", "Battery charging power", P, T, "kW", ("control", "metering"), frozenset({LoadFamily.EV_CHARGER, LoadFamily.BATTERY_CHARGER, LoadFamily.UPS})),
        _point("process.fuel_or_feed_flow", "Process feed flow", X, N, "kg/h", ("monitoring", "analysis"), frozenset({LoadFamily.ELECTROLYSER, LoadFamily.INDUSTRIAL_PROCESS})),
        _point("process.pressure", "Process pressure", X, N, "kPa", ("monitoring", "protection"), frozenset({LoadFamily.COMPRESSOR, LoadFamily.PUMP, LoadFamily.INDUSTRIAL_PROCESS})),
    )
    return tuple(point for point in definitions if family in point.families)


__all__ = ["LoadMeasurementPoint", "load_measurement_points"]
