"""Executable first-order generator control models.

These models implement transparent continuous-time blocks that can be
embedded by the transient solver. They deliberately do not own a time loop,
persist runtime state, dispatch protection, or claim a specific IEEE model
certification. The host integrator must evaluate derivatives at every actual
integration stage time.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, ClassVar

from plugins.dynamics.base import DynamicPlugin

from .catalog import GovernorParameters, PrimeMoverParameters


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _deadband(value: float, width: float) -> float:
    if abs(value) <= width:
        return 0.0
    return value - (width if value > 0.0 else -width)


@dataclass(frozen=True, slots=True)
class FirstOrderAVRParameters:
    """Generic transducer/regulator/exciter block; all values are per-unit or seconds."""
    regulator_gain: float
    transducer_time_constant_s: float
    regulator_time_constant_s: float
    exciter_time_constant_s: float
    regulator_min_pu: float
    regulator_max_pu: float
    field_min_pu: float
    field_max_pu: float

    def __post_init__(self) -> None:
        for name in ("regulator_gain", "transducer_time_constant_s",
                     "regulator_time_constant_s", "exciter_time_constant_s"):
            value = _number(getattr(self, name), name)
            if value <= 0.0:
                raise ValueError(f"{name} must be greater than zero")
            object.__setattr__(self, name, value)
        for name in ("regulator_min_pu", "regulator_max_pu", "field_min_pu", "field_max_pu"):
            object.__setattr__(self, name, _number(getattr(self, name), name))
        if self.regulator_min_pu >= self.regulator_max_pu:
            raise ValueError("regulator output limits must be ordered")
        if self.field_min_pu < 0.0 or self.field_min_pu >= self.field_max_pu:
            raise ValueError("field limits must satisfy 0 <= minimum < maximum")


class FirstOrderAVR(DynamicPlugin):
    """Three-state AVR: terminal transducer, regulator output, and field voltage."""
    model_type = "GENERIC_AVR_3STATE"
    state_names: ClassVar[tuple[str, ...]] = (
        "terminal_voltage_filtered_pu", "regulator_output_pu", "field_voltage_pu",
    )

    def __init__(self, parameters: FirstOrderAVRParameters) -> None:
        if not isinstance(parameters, FirstOrderAVRParameters):
            raise TypeError("parameters must be FirstOrderAVRParameters")
        self.parameters = parameters

    def initial_state(self, *, terminal_voltage_pu: float, field_voltage_pu: float,
                      regulator_output_pu: float | None = None, **_: Any) -> tuple[float, ...]:
        vt = _number(terminal_voltage_pu, "terminal_voltage_pu")
        efd = _clip(_number(field_voltage_pu, "field_voltage_pu"),
                    self.parameters.field_min_pu, self.parameters.field_max_pu)
        regulator = efd if regulator_output_pu is None else _clip(
            _number(regulator_output_pu, "regulator_output_pu"),
            self.parameters.regulator_min_pu, self.parameters.regulator_max_pu,
        )
        return (vt, regulator, efd)

    def derivatives(self, state: tuple[float, ...] | list[float], *,
                    terminal_voltage_pu: float, voltage_reference_pu: float,
                    stabilizing_signal_pu: float = 0.0, **_: Any) -> tuple[float, ...]:
        self.validate_state(state)
        vf, regulator, efd = (_number(value, "AVR state") for value in state)
        vt = _number(terminal_voltage_pu, "terminal_voltage_pu")
        reference = _number(voltage_reference_pu, "voltage_reference_pu")
        pss = _number(stabilizing_signal_pu, "stabilizing_signal_pu")
        p = self.parameters
        dvf = (vt - vf) / p.transducer_time_constant_s
        target = _clip(p.regulator_gain * (reference + pss - vf),
                       p.regulator_min_pu, p.regulator_max_pu)
        dreg = (target - regulator) / p.regulator_time_constant_s
        if (regulator >= p.regulator_max_pu and dreg > 0.0) or (
            regulator <= p.regulator_min_pu and dreg < 0.0
        ):
            dreg = 0.0
        field_target = _clip(regulator, p.field_min_pu, p.field_max_pu)
        defd = (field_target - efd) / p.exciter_time_constant_s
        if (efd >= p.field_max_pu and defd > 0.0) or (efd <= p.field_min_pu and defd < 0.0):
            defd = 0.0
        return (dvf, dreg, defd)

    def output(self, state: tuple[float, ...] | list[float], **_: Any) -> float:
        self.validate_state(state)
        return _clip(_number(state[2], "field_voltage_pu"),
                     self.parameters.field_min_pu, self.parameters.field_max_pu)

    def summary(self) -> dict[str, Any]:
        return {"model_type": self.model_type, "state_names": self.state_names,
                "parameters": self.parameters}


class TGOV1Governor(DynamicPlugin):
    """Two-state droop governor and turbine lag with rate/position limits."""
    model_type = "TGOV1_STYLE_GOVERNOR"
    state_names: ClassVar[tuple[str, ...]] = ("valve_position_pu", "mechanical_power_pu")

    def __init__(self, governor: GovernorParameters, prime_mover: PrimeMoverParameters,
                 *, speed_deadband_pu: float = 0.0) -> None:
        if not isinstance(governor, GovernorParameters) or not isinstance(prime_mover, PrimeMoverParameters):
            raise TypeError("governor and prime_mover must use their typed parameter contracts")
        self.governor = governor
        self.prime_mover = prime_mover
        self.speed_deadband_pu = _number(speed_deadband_pu, "speed_deadband_pu")
        if self.speed_deadband_pu < 0.0:
            raise ValueError("speed_deadband_pu must be non-negative")

    def initial_state(self, *, mechanical_power_pu: float,
                      valve_position_pu: float | None = None, **_: Any) -> tuple[float, ...]:
        pm = _number(mechanical_power_pu, "mechanical_power_pu")
        valve = pm if valve_position_pu is None else _number(valve_position_pu, "valve_position_pu")
        if not self.governor.valve_or_gate_min_pu <= valve <= self.governor.valve_or_gate_max_pu:
            raise ValueError("initial valve position is outside configured limits")
        if not self.prime_mover.minimum_output_pu <= pm <= self.prime_mover.maximum_output_pu:
            raise ValueError("initial mechanical power is outside prime-mover limits")
        return (valve, pm)

    def derivatives(self, state: tuple[float, ...] | list[float], *,
                    speed_deviation_pu: float, power_reference_pu: float,
                    **_: Any) -> tuple[float, ...]:
        self.validate_state(state)
        valve, pm = (_number(value, "governor state") for value in state)
        speed = _deadband(_number(speed_deviation_pu, "speed_deviation_pu"), self.speed_deadband_pu)
        reference = _number(power_reference_pu, "power_reference_pu")
        p = self.governor
        droop_fraction = p.droop_percent / 100.0
        target = _clip(reference - speed / droop_fraction,
                       p.valve_or_gate_min_pu, p.valve_or_gate_max_pu)
        dvalve = p.governor_gain * (target - valve) / p.governor_time_constant_s
        if p.rate_limit_pu_per_s is not None:
            dvalve = _clip(dvalve, -p.rate_limit_pu_per_s, p.rate_limit_pu_per_s)
        if (valve >= p.valve_or_gate_max_pu and dvalve > 0.0) or (
            valve <= p.valve_or_gate_min_pu and dvalve < 0.0
        ):
            dvalve = 0.0
        turbine_target = _clip(valve, self.prime_mover.minimum_output_pu,
                               self.prime_mover.maximum_output_pu)
        dpm = (turbine_target - pm) / self.prime_mover.response_time_s
        if (pm >= self.prime_mover.maximum_output_pu and dpm > 0.0) or (
            pm <= self.prime_mover.minimum_output_pu and dpm < 0.0
        ):
            dpm = 0.0
        return (dvalve, dpm)

    def output(self, state: tuple[float, ...] | list[float], **_: Any) -> float:
        self.validate_state(state)
        return _number(state[1], "mechanical_power_pu")

    def summary(self) -> dict[str, Any]:
        return {"model_type": self.model_type, "state_names": self.state_names,
                "governor": self.governor, "prime_mover": self.prime_mover,
                "speed_deadband_pu": self.speed_deadband_pu}


@dataclass(frozen=True, slots=True)
class PSSParameters:
    """Washout plus one lead-lag stabilizer stage."""
    gain: float
    washout_time_constant_s: float
    lead_time_constant_s: float
    lag_time_constant_s: float
    output_min_pu: float
    output_max_pu: float

    def __post_init__(self) -> None:
        for name in ("gain", "washout_time_constant_s", "lead_time_constant_s", "lag_time_constant_s"):
            value = _number(getattr(self, name), name)
            if value <= 0.0:
                raise ValueError(f"{name} must be greater than zero")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "output_min_pu", _number(self.output_min_pu, "output_min_pu"))
        object.__setattr__(self, "output_max_pu", _number(self.output_max_pu, "output_max_pu"))
        if self.output_min_pu >= self.output_max_pu:
            raise ValueError("PSS output limits must be ordered")


class WashoutLeadLagPSS(DynamicPlugin):
    """Two-state speed washout and lead-lag PSS; output is a bounded AVR input."""
    model_type = "WASHOUT_LEAD_LAG_PSS"
    state_names: ClassVar[tuple[str, ...]] = ("washout_state_pu", "lead_lag_state_pu")

    def __init__(self, parameters: PSSParameters) -> None:
        if not isinstance(parameters, PSSParameters):
            raise TypeError("parameters must be PSSParameters")
        self.parameters = parameters

    def initial_state(self, *, speed_deviation_pu: float = 0.0, **_: Any) -> tuple[float, ...]:
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        return (speed, 0.0)

    def derivatives(self, state: tuple[float, ...] | list[float], *,
                    speed_deviation_pu: float, **_: Any) -> tuple[float, ...]:
        self.validate_state(state)
        washout_state, lead_lag_state = (_number(value, "PSS state") for value in state)
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        p = self.parameters
        washout_derivative = (speed - washout_state) / p.washout_time_constant_s
        washout_output = p.gain * (speed - washout_state)
        lead_lag_derivative = (washout_output - lead_lag_state) / p.lag_time_constant_s
        return (washout_derivative, lead_lag_derivative)

    def output(self, state: tuple[float, ...] | list[float], *,
               speed_deviation_pu: float, **_: Any) -> float:
        self.validate_state(state)
        washout_state, lead_lag_state = (_number(value, "PSS state") for value in state)
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        p = self.parameters
        washout_output = p.gain * (speed - washout_state)
        ratio = p.lead_time_constant_s / p.lag_time_constant_s
        raw = ratio * washout_output + (1.0 - ratio) * lead_lag_state
        return _clip(raw, p.output_min_pu, p.output_max_pu)

    def summary(self) -> dict[str, Any]:
        return {"model_type": self.model_type, "state_names": self.state_names,
                "parameters": self.parameters}


@dataclass(frozen=True, slots=True)
class TGOV1Parameters:
    """IEEE TGOV1 steam-governor parameters on the connected machine base.

    T1 is the steam-bowl/governor time constant, T2 the reheater numerator
    time constant, and T3 the reheater lag time constant. All power and
    speed quantities are per-unit on the same declared base.
    """
    droop_pu: float
    t1_s: float
    t2_s: float
    t3_s: float
    valve_min_pu: float = 0.0
    valve_max_pu: float = 1.0
    turbine_damping_pu: float = 0.0

    def __post_init__(self) -> None:
        for name in ("droop_pu", "t1_s", "t3_s"):
            value = _number(getattr(self, name), name)
            if value <= 0.0:
                raise ValueError(f"{name} must be greater than zero")
            object.__setattr__(self, name, value)
        t2 = _number(self.t2_s, "t2_s")
        if t2 < 0.0:
            raise ValueError("t2_s must be non-negative")
        object.__setattr__(self, "t2_s", t2)
        low = _number(self.valve_min_pu, "valve_min_pu")
        high = _number(self.valve_max_pu, "valve_max_pu")
        if low >= high:
            raise ValueError("valve limits must satisfy minimum < maximum")
        object.__setattr__(self, "valve_min_pu", low)
        object.__setattr__(self, "valve_max_pu", high)
        damping = _number(self.turbine_damping_pu, "turbine_damping_pu")
        if damping < 0.0:
            raise ValueError("turbine_damping_pu must be non-negative")
        object.__setattr__(self, "turbine_damping_pu", damping)


class TGOV1SteamGovernor(DynamicPlugin):
    """Two-state IEEE TGOV1 steam governor/reheater block.

    States are valve position Pv and reheater/turbine power Pt. The output
    Pm = Pt - Dt*speed_deviation. The caller supplies the reference and
    speed at every integrator stage; this object owns no simulation clock.
    """
    model_type = "IEEE_TGOV1_STEAM_GOVERNOR"
    state_names: ClassVar[tuple[str, ...]] = (
        "valve_position_pu", "turbine_power_pu",
    )

    def __init__(self, parameters: TGOV1Parameters) -> None:
        if not isinstance(parameters, TGOV1Parameters):
            raise TypeError("parameters must be TGOV1Parameters")
        self.parameters = parameters

    def initial_state(self, *, mechanical_power_pu: float,
                      speed_deviation_pu: float = 0.0,
                      power_reference_pu: float | None = None,
                      **_: Any) -> tuple[float, ...]:
        pm = _number(mechanical_power_pu, "mechanical_power_pu")
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        valve = pm + self.parameters.turbine_damping_pu * speed
        if not self.parameters.valve_min_pu <= valve <= self.parameters.valve_max_pu:
            raise ValueError("steady-state valve position is outside TGOV1 limits")
        expected_reference = speed + self.parameters.droop_pu * valve
        if power_reference_pu is not None:
            supplied = _number(power_reference_pu, "power_reference_pu")
            if abs(supplied - expected_reference) > 1e-8:
                raise ValueError(
                    "power reference is inconsistent with the requested TGOV1 steady state"
                )
        return (valve, valve)

    def derivatives(self, state: tuple[float, ...] | list[float], *,
                    speed_deviation_pu: float, power_reference_pu: float,
                    **_: Any) -> tuple[float, ...]:
        self.validate_state(state)
        valve, turbine_power = (_number(value, "TGOV1 state") for value in state)
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        reference = _number(power_reference_pu, "power_reference_pu")
        p = self.parameters

        governor_error = (reference - speed) / p.droop_pu - valve
        d_valve = governor_error / p.t1_s
        if (valve >= p.valve_max_pu and d_valve > 0.0) or (
            valve <= p.valve_min_pu and d_valve < 0.0
        ):
            d_valve = 0.0

        d_turbine = (valve + p.t2_s * d_valve - turbine_power) / p.t3_s
        if not all(isfinite(value) for value in (d_valve, d_turbine)):
            raise ValueError("TGOV1 derivative contains non-finite values")
        return (d_valve, d_turbine)

    def output(self, state: tuple[float, ...] | list[float], *,
               speed_deviation_pu: float, **_: Any) -> float:
        self.validate_state(state)
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        return _number(state[1], "turbine_power_pu") - self.parameters.turbine_damping_pu * speed

    def summary(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "parameters": self.parameters,
            "power_base": "connected_machine_base",
        }


__all__ = [
    "FirstOrderAVRParameters", "FirstOrderAVR", "TGOV1Governor",
    "PSSParameters", "WashoutLeadLagPSS", "TGOV1Parameters", "TGOV1SteamGovernor",
]

@dataclass(frozen=True, slots=True)
class HYGOVReducedParameters:
    """Reduced HYGOV hydro-governor parameters on one declared per-unit base.

    This contract implements the permanent-droop PI governor, gate servo and
    nonlinear inelastic single-penstock/turbine coupling. It intentionally
    does not claim full PSS/E HYGOV equivalence.
    """
    permanent_droop_pu: float
    temporary_droop_pu: float
    governor_time_constant_s: float
    filter_time_constant_s: float
    gate_servo_time_constant_s: float
    gate_velocity_limit_pu_per_s: float
    gate_min_pu: float
    gate_max_pu: float
    water_time_constant_s: float
    turbine_gain_pu: float
    turbine_damping_pu: float
    no_load_flow_pu: float
    nominal_head_pu: float = 1.0

    def __post_init__(self) -> None:
        positive = (
            "permanent_droop_pu", "temporary_droop_pu",
            "governor_time_constant_s", "filter_time_constant_s",
            "gate_servo_time_constant_s", "gate_velocity_limit_pu_per_s",
            "water_time_constant_s", "turbine_gain_pu", "nominal_head_pu",
        )
        for name in positive:
            value = _number(getattr(self, name), name)
            if value <= 0.0:
                raise ValueError(f"{name} must be greater than zero")
            object.__setattr__(self, name, value)
        for name in ("gate_min_pu", "gate_max_pu", "turbine_damping_pu",
                     "no_load_flow_pu"):
            object.__setattr__(self, name, _number(getattr(self, name), name))
        if self.temporary_droop_pu <= self.permanent_droop_pu:
            raise ValueError("temporary droop must exceed permanent droop")
        if not 0.0 <= self.gate_min_pu < self.gate_max_pu <= 1.0:
            raise ValueError("gate limits must satisfy 0 <= minimum < maximum <= 1")
        if self.turbine_damping_pu < 0.0 or self.no_load_flow_pu < 0.0:
            raise ValueError("turbine damping and no-load flow must be non-negative")


class HYGOVReducedHydroGovernor(DynamicPlugin):
    """Reduced hydro governor with nonlinear inelastic single-penstock dynamics.

    States: filtered governor error, governor integrator, gate position and
    penstock flow. Inputs are evaluated at every solver stage. The model owns
    no integration clock or persistent state.
    """
    model_type = "HYGOV_REDUCED_NONLINEAR_PENSTOCK"
    state_names: ClassVar[tuple[str, ...]] = (
        "filtered_governor_error_pu", "governor_integrator_pu",
        "gate_position_pu", "penstock_flow_pu",
    )

    def __init__(self, parameters: HYGOVReducedParameters) -> None:
        if not isinstance(parameters, HYGOVReducedParameters):
            raise TypeError("parameters must be HYGOVReducedParameters")
        self.parameters = parameters

    def _head(self, gate: float, flow: float) -> float:
        if abs(gate) <= 1e-12:
            if abs(flow) <= 1e-12:
                return self.parameters.nominal_head_pu
            raise ValueError("non-zero penstock flow is invalid at a closed gate")
        return (flow / gate) ** 2

    def initial_state(self, *, gate_position_pu: float, penstock_flow_pu: float,
                      power_reference_pu: float, speed_deviation_pu: float = 0.0,
                      **_: Any) -> tuple[float, ...]:
        p = self.parameters
        gate = _number(gate_position_pu, "gate_position_pu")
        flow = _number(penstock_flow_pu, "penstock_flow_pu")
        reference = _number(power_reference_pu, "power_reference_pu")
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        if not p.gate_min_pu <= gate <= p.gate_max_pu:
            raise ValueError("initial gate position is outside configured limits")
        if flow < 0.0:
            raise ValueError("initial penstock flow must be non-negative")
        head = self._head(gate, flow)
        if abs(head - p.nominal_head_pu) > 1e-6:
            raise ValueError(
                "initial flow and gate must satisfy the configured steady-state head"
            )
        equilibrium_reference = speed + p.permanent_droop_pu * gate
        if abs(reference - equilibrium_reference) > 1e-6:
            raise ValueError(
                "power reference is inconsistent with the requested steady-state "
                "speed and gate position"
            )
        return (0.0, gate, gate, flow)

    def derivatives(self, state: tuple[float, ...] | list[float], *,
                    power_reference_pu: float, speed_deviation_pu: float = 0.0,
                    **_: Any) -> tuple[float, ...]:
        self.validate_state(state)
        filtered_error, integrator, gate, flow = (
            _number(value, "HYGOV state") for value in state
        )
        p = self.parameters
        reference = _number(power_reference_pu, "power_reference_pu")
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        if not p.gate_min_pu <= gate <= p.gate_max_pu:
            raise ValueError("gate state is outside configured limits")
        if flow < 0.0:
            raise ValueError("penstock flow state must be non-negative")

        error = reference - speed - p.permanent_droop_pu * gate
        d_filtered = (error - filtered_error) / p.filter_time_constant_s
        gate_command = integrator + filtered_error / p.temporary_droop_pu
        limited_command = _clip(gate_command, p.gate_min_pu, p.gate_max_pu)
        d_gate = (limited_command - gate) / p.gate_servo_time_constant_s
        d_gate = _clip(d_gate, -p.gate_velocity_limit_pu_per_s,
                       p.gate_velocity_limit_pu_per_s)
        if (gate >= p.gate_max_pu and d_gate > 0.0) or (
            gate <= p.gate_min_pu and d_gate < 0.0
        ):
            d_gate = 0.0

        d_integrator = filtered_error / (
            p.temporary_droop_pu * p.governor_time_constant_s
        )
        if (gate_command >= p.gate_max_pu and d_integrator > 0.0) or (
            gate_command <= p.gate_min_pu and d_integrator < 0.0
        ):
            d_integrator = 0.0

        head = self._head(gate, flow)
        d_flow = (p.nominal_head_pu - head) / p.water_time_constant_s
        if flow <= 0.0 and d_flow < 0.0:
            d_flow = 0.0
        return (d_filtered, d_integrator, d_gate, d_flow)

    def output(self, state: tuple[float, ...] | list[float], *,
               speed_deviation_pu: float = 0.0, **_: Any) -> float:
        self.validate_state(state)
        _, _, gate, flow = (_number(value, "HYGOV state") for value in state)
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        if not self.parameters.gate_min_pu <= gate <= self.parameters.gate_max_pu:
            raise ValueError("gate state is outside configured limits")
        head = self._head(gate, flow)
        p = self.parameters
        return (
            p.turbine_gain_pu * head * (flow - p.no_load_flow_pu)
            - p.turbine_damping_pu * speed * gate
        )

    def summary(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "parameters": self.parameters,
            "equation_scope": "reduced_PI_governor_nonlinear_inelastic_single_penstock",
            "power_base": "declared_connected_machine_per_unit_base",
        }



@dataclass(frozen=True, slots=True)
class GGOV1Parameters:
    """Validated GGOV1 parameter contract; not an executable controller.

    Power/fuel quantities are on the explicit turbine MW rating base. The
    turbine rating is distinct from the connected generator MVA base.
    """
    turbine_rating_mw: float
    rselect: int
    flag: int
    droop_pu: float
    electrical_power_time_constant_s: float
    governor_error_max_pu: float
    governor_error_min_pu: float
    governor_proportional_gain: float
    governor_integral_gain_per_s: float
    governor_derivative_gain: float
    governor_derivative_time_constant_s: float
    valve_max_pu: float
    valve_min_pu: float
    actuator_time_constant_s: float
    valve_open_rate_pu_per_s: float
    valve_close_rate_pu_per_s: float
    turbine_gain: float
    no_load_fuel_flow_pu: float
    turbine_lag_time_constant_s: float
    turbine_lead_time_constant_s: float
    engine_transport_time_constant_s: float
    load_filter_time_constant_s: float
    load_proportional_gain: float
    load_integral_gain_per_s: float
    load_limit_reference_pu: float
    mechanical_damping: float
    power_controller_integral_gain_per_s: float
    acceleration_limit_pu_per_s: float
    acceleration_limiter_gain: float
    acceleration_filter_time_constant_s: float
    speed_deadband_pu: float
    temperature_lead_time_constant_s: float
    temperature_lag_time_constant_s: float
    load_limit_increase_rate_pu_per_s: float
    load_limit_decrease_rate_pu_per_s: float
    power_controller_setpoint_mw: float | None = None

    def __post_init__(self) -> None:
        # NERC's PSS/E selector table includes 0 (no droop feedback), while
        # some independent implementations omit it. Keep the model-library
        # selector set explicit rather than silently coercing values.
        if isinstance(self.rselect, bool) or not isinstance(self.rselect, int) or self.rselect not in (1, 0, -1, -2):
            raise ValueError("rselect must be 1 (electrical power), 0 (none), -1 (valve stroke), or -2 (governor output)")
        if isinstance(self.flag, bool) or not isinstance(self.flag, int) or self.flag not in (0, 1):
            raise ValueError("flag must be 0 or 1")
        positive = ("turbine_rating_mw", "turbine_gain", "actuator_time_constant_s")
        nonnegative = (
            "electrical_power_time_constant_s", "governor_proportional_gain",
            "governor_integral_gain_per_s", "governor_derivative_gain",
            "governor_derivative_time_constant_s", "turbine_lag_time_constant_s",
            "turbine_lead_time_constant_s", "engine_transport_time_constant_s",
            "load_filter_time_constant_s", "load_proportional_gain",
            "load_integral_gain_per_s", "no_load_fuel_flow_pu",
            "power_controller_integral_gain_per_s", "acceleration_limiter_gain",
            "speed_deadband_pu",
            "temperature_lead_time_constant_s", "temperature_lag_time_constant_s",
        )
        for name in positive:
            value = _number(getattr(self, name), name)
            if value <= 0.0:
                raise ValueError(f"{name} must be greater than zero")
            object.__setattr__(self, name, value)
        for name in nonnegative:
            value = _number(getattr(self, name), name)
            if value < 0.0:
                raise ValueError(f"{name} must be non-negative")
            object.__setattr__(self, name, value)
        droop = _number(self.droop_pu, "droop_pu")
        if droop < 0.0 or (self.rselect == 0 and droop != 0.0) or (self.rselect != 0 and droop <= 0.0):
            raise ValueError("droop must be zero for isochronous Rselect=0 and positive for droop-feedback modes")
        object.__setattr__(self, "droop_pu", droop)
        finite_names = (
            "governor_error_max_pu", "governor_error_min_pu", "valve_max_pu",
            "valve_min_pu", "valve_open_rate_pu_per_s", "valve_close_rate_pu_per_s",
            "load_limit_reference_pu", "mechanical_damping", "acceleration_limit_pu_per_s",
            "load_limit_increase_rate_pu_per_s", "load_limit_decrease_rate_pu_per_s",
        )
        for name in finite_names:
            object.__setattr__(self, name, _number(getattr(self, name), name))
        if self.governor_error_min_pu > self.governor_error_max_pu:
            raise ValueError("governor error limits must satisfy minimum <= maximum")
        if self.valve_min_pu > self.valve_max_pu:
            raise ValueError("valve limits must satisfy minimum <= maximum")
        if self.valve_open_rate_pu_per_s <= 0.0 or self.valve_close_rate_pu_per_s >= 0.0:
            raise ValueError("valve opening rate must be positive and closing rate negative")
        if self.acceleration_limit_pu_per_s < 0.0:
            raise ValueError("acceleration_limit_pu_per_s must be non-negative")
        acceleration_time = _number(
            self.acceleration_filter_time_constant_s,
            "acceleration_filter_time_constant_s",
        )
        if acceleration_time <= 0.0:
            raise ValueError("GGOV1 acceleration filter time constant Ta must be greater than zero")
        object.__setattr__(self, "acceleration_filter_time_constant_s", acceleration_time)
        if (self.load_limit_increase_rate_pu_per_s <= 0.0
                or self.load_limit_decrease_rate_pu_per_s >= 0.0):
            raise ValueError("load-limit increase rate must be positive and decrease rate negative")
        if self.turbine_lag_time_constant_s == 0.0 and self.turbine_lead_time_constant_s != 0.0:
            raise ValueError("zero turbine lag requires zero turbine lead time constant")
        if self.power_controller_setpoint_mw is not None:
            setpoint = _number(self.power_controller_setpoint_mw, "power_controller_setpoint_mw")
            if setpoint < 0.0:
                raise ValueError("power_controller_setpoint_mw must be non-negative")
            object.__setattr__(self, "power_controller_setpoint_mw", setpoint)

    def droop_feedback_signal(
        self, *, electrical_power_pu: float, valve_stroke_pu: float,
        governor_output_pu: float,
    ) -> float:
        """Resolve the PSS/E/NERC Rselect feedback input without hidden coercion."""
        if self.rselect == 1:
            return _number(electrical_power_pu, "electrical_power_pu")
        if self.rselect == -1:
            return _number(valve_stroke_pu, "valve_stroke_pu")
        if self.rselect == -2:
            return _number(governor_output_pu, "governor_output_pu")
        # Rselect=0 is isochronous: no droop feedback signal is selected.
        return 0.0

    def fuel_speed_multiplier(self, speed_pu: float) -> float:
        """PSS/E Flag=1 uses shaft speed; Flag=0 uses unity."""
        return _number(speed_pu, "speed_pu") if self.flag == 1 else 1.0

    def system_to_turbine_power_base_ratio(self, system_base_mva: float) -> float:
        """Return S_system / turbine_rating; caller owns MW/MVA convention."""
        base = _number(system_base_mva, "system_base_mva")
        if base <= 0.0:
            raise ValueError("system_base_mva must be greater than zero")
        return base / self.turbine_rating_mw
