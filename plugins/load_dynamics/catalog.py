"""Immutable catalogue and engineering parameter contracts for electrical loads."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite


class LoadFamily(str, Enum):
    RESISTIVE = "resistive"
    LIGHTING = "lighting"
    INDUCTION_MOTOR = "induction_motor"
    SYNCHRONOUS_MOTOR = "synchronous_motor"
    PMSM = "pmsm"
    BLDC = "bldc"
    DC_MOTOR = "dc_motor"
    COMPRESSOR = "compressor"
    PUMP = "pump"
    FAN = "fan"
    HVAC = "hvac"
    CHILLER = "chiller"
    HEAT_PUMP = "heat_pump"
    REFRIGERATION = "refrigeration"
    VFD_FED = "vfd_fed"
    RECTIFIER = "rectifier"
    UPS = "ups"
    SMPS = "smps"
    EV_CHARGER = "ev_charger"
    BATTERY_CHARGER = "battery_charger"
    DATA_CENTRE = "data_centre"
    ARC_FURNACE = "arc_furnace"
    INDUCTION_FURNACE = "induction_furnace"
    RESISTANCE_FURNACE = "resistance_furnace"
    WELDING = "welding"
    ELECTROLYSER = "electrolyser"
    TRACTION = "traction"
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    INDUSTRIAL_PROCESS = "industrial_process"
    STATION_AUXILIARY = "station_auxiliary"
    DC_AUXILIARY = "dc_auxiliary"
    COMPOSITE = "composite"
    OTHER = "other"


class LoadConnection(str, Enum):
    SINGLE_PHASE_LN = "single_phase_ln"
    SINGLE_PHASE_LL = "single_phase_ll"
    THREE_PHASE_STAR = "three_phase_star"
    THREE_PHASE_DELTA = "three_phase_delta"
    THREE_PHASE_GROUNDED_STAR = "three_phase_grounded_star"
    DC_TWO_WIRE = "dc_two_wire"
    DC_THREE_WIRE = "dc_three_wire"


def _finite(value: float, name: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    if positive and result <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    if nonnegative and result < 0.0:
        raise ValueError(f"{name} must be non-negative")
    return result


@dataclass(frozen=True, slots=True)
class LoadNameplate:
    """Manufacturer/nameplate ratings; powers use positive-consumption convention."""
    family: LoadFamily
    rated_active_power_kw: float
    rated_voltage_kv: float
    connection: LoadConnection
    frequency_hz: float = 50.0
    rated_power_factor: float = 0.9
    rated_efficiency: float | None = None
    rated_current_a: float | None = None
    phase_count: int = 3
    manufacturer: str = ""
    model: str = ""
    duty_class: str = ""
    insulation_class: str = ""
    enclosure_ip: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.family, LoadFamily):
            raise TypeError("family must be LoadFamily")
        if not isinstance(self.connection, LoadConnection):
            raise TypeError("connection must be LoadConnection")
        for name in ("rated_active_power_kw", "rated_voltage_kv", "frequency_hz"):
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))
        pf = _finite(self.rated_power_factor, "rated_power_factor", positive=True)
        if pf > 1.0:
            raise ValueError("rated_power_factor must not exceed 1.0")
        object.__setattr__(self, "rated_power_factor", pf)
        if self.rated_efficiency is not None:
            efficiency = _finite(self.rated_efficiency, "rated_efficiency", positive=True)
            if efficiency > 1.0:
                raise ValueError("rated_efficiency must not exceed 1.0")
            object.__setattr__(self, "rated_efficiency", efficiency)
        if self.rated_current_a is not None:
            object.__setattr__(self, "rated_current_a", _finite(self.rated_current_a, "rated_current_a", positive=True))
        if isinstance(self.phase_count, bool) or not isinstance(self.phase_count, int) or self.phase_count not in (1, 2, 3):
            raise ValueError("phase_count must be 1, 2, or 3")
        for name in ("manufacturer", "model", "duty_class", "insulation_class", "enclosure_ip"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")


@dataclass(frozen=True, slots=True)
class ZIPLoadModel:
    """Independent active/reactive Z-I-P fractions for voltage-dependent demand."""
    active_z: float
    active_i: float
    active_p: float
    reactive_z: float
    reactive_i: float
    reactive_p: float

    def __post_init__(self) -> None:
        for name in ("active_z", "active_i", "active_p", "reactive_z", "reactive_i", "reactive_p"):
            value = _finite(getattr(self, name), name, nonnegative=True)
            object.__setattr__(self, name, value)
        if abs(self.active_z + self.active_i + self.active_p - 1.0) > 1e-9:
            raise ValueError("active ZIP fractions must sum to 1.0")
        if abs(self.reactive_z + self.reactive_i + self.reactive_p - 1.0) > 1e-9:
            raise ValueError("reactive ZIP fractions must sum to 1.0")

    def demand(self, p0_kw: float, q0_kvar: float, voltage_pu: float) -> tuple[float, float]:
        """Return (P kW, Q kvar); voltage_pu must be a positive magnitude."""
        p0 = _finite(p0_kw, "p0_kw", nonnegative=True)
        q0 = _finite(q0_kvar, "q0_kvar", nonnegative=True)
        v = _finite(voltage_pu, "voltage_pu", positive=True)
        p_factor = self.active_z * v * v + self.active_i * v + self.active_p
        q_factor = self.reactive_z * v * v + self.reactive_i * v + self.reactive_p
        return p0 * p_factor, q0 * q_factor


@dataclass(frozen=True, slots=True)
class LoadProfilePoint:
    """Demand multipliers at seconds from the profile origin."""
    time_s: float
    active_multiplier: float
    reactive_multiplier: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "time_s", _finite(self.time_s, "time_s", nonnegative=True))
        object.__setattr__(self, "active_multiplier", _finite(self.active_multiplier, "active_multiplier", nonnegative=True))
        object.__setattr__(self, "reactive_multiplier", _finite(self.reactive_multiplier, "reactive_multiplier", nonnegative=True))


@dataclass(frozen=True, slots=True)
class LoadDemandProfile:
    """Immutable piecewise-linear demand profile, clamped outside its time range."""
    points: tuple[LoadProfilePoint, ...]

    def __post_init__(self) -> None:
        points = tuple(self.points)
        if len(points) < 2 or any(not isinstance(p, LoadProfilePoint) for p in points):
            raise ValueError("a demand profile requires at least two LoadProfilePoint values")
        if any(right.time_s <= left.time_s for left, right in zip(points, points[1:])):
            raise ValueError("profile time_s values must be strictly increasing")
        object.__setattr__(self, "points", points)

    def multipliers(self, time_s: float) -> tuple[float, float]:
        t = _finite(time_s, "time_s", nonnegative=True)
        if t <= self.points[0].time_s:
            p = self.points[0]
            return p.active_multiplier, p.reactive_multiplier
        if t >= self.points[-1].time_s:
            p = self.points[-1]
            return p.active_multiplier, p.reactive_multiplier
        for left, right in zip(self.points, self.points[1:]):
            if left.time_s <= t <= right.time_s:
                ratio = (t - left.time_s) / (right.time_s - left.time_s)
                return (
                    left.active_multiplier + ratio * (right.active_multiplier - left.active_multiplier),
                    left.reactive_multiplier + ratio * (right.reactive_multiplier - left.reactive_multiplier),
                )
        raise RuntimeError("profile interpolation interval could not be resolved")


@dataclass(frozen=True, slots=True)
class InductionMotorParameters:
    """Per-unit equivalent-circuit and mechanical data on the declared motor base."""
    rated_power_kw: float
    rated_voltage_kv: float
    frequency_hz: float
    poles: int
    stator_resistance_pu: float
    stator_reactance_pu: float
    rotor_resistance_pu: float
    rotor_reactance_pu: float
    magnetizing_reactance_pu: float
    inertia_h_s: float
    rated_slip_pu: float
    locked_rotor_current_pu: float
    breakdown_torque_pu: float

    def __post_init__(self) -> None:
        for name in ("rated_power_kw", "rated_voltage_kv", "frequency_hz", "stator_resistance_pu",
                     "stator_reactance_pu", "rotor_resistance_pu", "rotor_reactance_pu",
                     "magnetizing_reactance_pu", "inertia_h_s", "rated_slip_pu",
                     "locked_rotor_current_pu", "breakdown_torque_pu"):
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))
        if isinstance(self.poles, bool) or not isinstance(self.poles, int) or self.poles not in (2, 4, 6, 8, 10, 12):
            raise ValueError("poles must be a supported even pole count from 2 through 12")
        if self.rated_slip_pu >= 1.0:
            raise ValueError("rated_slip_pu must be less than 1.0")


__all__ = [
    "LoadFamily", "LoadConnection", "LoadNameplate", "ZIPLoadModel",
    "LoadProfilePoint", "LoadDemandProfile", "InductionMotorParameters",
]
