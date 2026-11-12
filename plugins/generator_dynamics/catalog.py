"""Immutable generator nameplate and dynamics parameter contracts."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite


class GeneratorFamily(str, Enum):
    DIESEL = "diesel"
    GAS_ENGINE = "gas_engine"
    STEAM_TURBINE = "steam_turbine"
    GAS_TURBINE = "gas_turbine"
    HYDRO = "hydro"
    WIND_DFIG = "wind_dfig"
    WIND_FULL_CONVERTER = "wind_full_converter"
    SYNCHRONOUS = "synchronous"
    INDUCTION = "induction"
    PERMANENT_MAGNET = "permanent_magnet"
    INVERTER_BASED = "inverter_based"
    FUEL_CELL = "fuel_cell"
    MICRO_TURBINE = "micro_turbine"
    OTHER = "other"


class GeneratorOperatingMode(str, Enum):
    GRID_FOLLOWING = "grid_following"
    GRID_FORMING = "grid_forming"
    ISLANDED = "islanded"
    SYNCHRONOUS_CONDENSER = "synchronous_condenser"
    STANDBY = "standby"
    PRIME = "prime"
    CONTINUOUS = "continuous"


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
class GeneratorNameplate:
    """Common electrical and operating nameplate data."""
    rated_power_mva: float
    rated_voltage_kv: float
    frequency_hz: float = 50.0
    power_factor: float = 0.8
    pole_pairs: int | None = None
    rated_speed_rpm: float | None = None
    rated_current_a: float | None = None
    cooling_method: str = ""
    insulation_class: str = ""
    manufacturer: str = ""
    model: str = ""

    def __post_init__(self) -> None:
        for name in ("rated_power_mva", "rated_voltage_kv", "frequency_hz"):
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))
        pf = _finite(self.power_factor, "power_factor", positive=True)
        if pf > 1.0:
            raise ValueError("power_factor must not exceed 1.0")
        object.__setattr__(self, "power_factor", pf)
        for name in ("rated_speed_rpm", "rated_current_a"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(value, name, positive=True))
        if self.pole_pairs is not None and (
            isinstance(self.pole_pairs, bool) or not isinstance(self.pole_pairs, int) or self.pole_pairs < 1
        ):
            raise ValueError("pole_pairs must be a positive integer or None")
        for name in ("cooling_method", "insulation_class", "manufacturer", "model"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")


@dataclass(frozen=True, slots=True)
class SynchronousMachineDynamics:
    """Per-unit synchronous-machine data on the declared machine base."""
    xd_pu: float
    xq_pu: float
    xd_transient_pu: float
    xq_transient_pu: float
    xd_subtransient_pu: float
    xq_subtransient_pu: float
    x_leakage_pu: float
    inertia_h_s: float
    damping_pu: float = 0.0
    t_d0_transient_s: float = 5.0
    t_q0_transient_s: float = 0.5
    t_d0_subtransient_s: float = 0.05
    t_q0_subtransient_s: float = 0.05
    machine_base_mva: float = 1.0
    machine_base_kv: float = 1.0

    def __post_init__(self) -> None:
        positive = (
            "xd_pu", "xq_pu", "xd_transient_pu", "xq_transient_pu",
            "xd_subtransient_pu", "xq_subtransient_pu", "x_leakage_pu",
            "inertia_h_s", "t_d0_transient_s", "t_q0_transient_s",
            "t_d0_subtransient_s", "t_q0_subtransient_s",
            "machine_base_mva", "machine_base_kv",
        )
        for name in positive:
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))
        object.__setattr__(self, "damping_pu", _finite(self.damping_pu, "damping_pu", nonnegative=True))
        if self.xd_subtransient_pu > self.xd_transient_pu or self.xd_transient_pu > self.xd_pu:
            raise ValueError("d-axis reactances must satisfy Xd'' <= Xd' <= Xd")
        if self.xq_subtransient_pu > self.xq_transient_pu or self.xq_transient_pu > self.xq_pu:
            raise ValueError("q-axis reactances must satisfy Xq'' <= Xq' <= Xq")


@dataclass(frozen=True, slots=True)
class ExcitationParameters:
    """AVR/exciter configuration with explicit output limits."""
    regulator_gain: float
    regulator_time_constant_s: float
    field_voltage_min_pu: float
    field_voltage_max_pu: float
    field_current_limit_pu: float | None = None
    ceiling_voltage_pu: float | None = None
    under_excitation_limit_pu: float | None = None
    over_excitation_limit_pu: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "regulator_gain", _finite(self.regulator_gain, "regulator_gain", positive=True))
        object.__setattr__(self, "regulator_time_constant_s", _finite(self.regulator_time_constant_s, "regulator_time_constant_s", positive=True))
        low = _finite(self.field_voltage_min_pu, "field_voltage_min_pu")
        high = _finite(self.field_voltage_max_pu, "field_voltage_max_pu")
        if low >= high:
            raise ValueError("field voltage minimum must be less than maximum")
        for name in ("field_current_limit_pu", "ceiling_voltage_pu", "under_excitation_limit_pu", "over_excitation_limit_pu"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(value, name, positive=True))


@dataclass(frozen=True, slots=True)
class GovernorParameters:
    """Speed governor and actuator configuration."""
    droop_percent: float
    governor_gain: float
    governor_time_constant_s: float
    actuator_time_constant_s: float
    valve_or_gate_min_pu: float = 0.0
    valve_or_gate_max_pu: float = 1.0
    rate_limit_pu_per_s: float | None = None

    def __post_init__(self) -> None:
        for name in ("droop_percent", "governor_gain", "governor_time_constant_s", "actuator_time_constant_s"):
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))
        low = _finite(self.valve_or_gate_min_pu, "valve_or_gate_min_pu")
        high = _finite(self.valve_or_gate_max_pu, "valve_or_gate_max_pu")
        if low < 0.0 or high > 1.0 or low >= high:
            raise ValueError("valve/gate limits must satisfy 0 <= minimum < maximum <= 1")
        if self.rate_limit_pu_per_s is not None:
            object.__setattr__(self, "rate_limit_pu_per_s", _finite(self.rate_limit_pu_per_s, "rate_limit_pu_per_s", positive=True))


@dataclass(frozen=True, slots=True)
class PrimeMoverParameters:
    """Generic prime-mover energy and response data."""
    family: GeneratorFamily
    rated_mechanical_power_mw: float
    response_time_s: float
    minimum_output_pu: float = 0.0
    maximum_output_pu: float = 1.0
    fuel_or_resource: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.family, GeneratorFamily):
            raise TypeError("family must be a GeneratorFamily")
        object.__setattr__(self, "rated_mechanical_power_mw", _finite(self.rated_mechanical_power_mw, "rated_mechanical_power_mw", positive=True))
        object.__setattr__(self, "response_time_s", _finite(self.response_time_s, "response_time_s", positive=True))
        low = _finite(self.minimum_output_pu, "minimum_output_pu")
        high = _finite(self.maximum_output_pu, "maximum_output_pu")
        if low < 0.0 or high > 1.0 or low >= high:
            raise ValueError("prime-mover output limits must satisfy 0 <= minimum < maximum <= 1")
        if not isinstance(self.fuel_or_resource, str):
            raise TypeError("fuel_or_resource must be a string")


__all__ = [
    "GeneratorFamily", "GeneratorOperatingMode", "GeneratorNameplate",
    "SynchronousMachineDynamics", "ExcitationParameters", "GovernorParameters",
    "PrimeMoverParameters",
]
