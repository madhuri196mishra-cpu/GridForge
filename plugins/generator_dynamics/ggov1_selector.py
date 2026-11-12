"""GGOV1 low-value fuel-command selection and tracking signals.

This block owns no controller states. The host evaluates each active controller,
then this deterministic selector chooses the smallest request. The coordinator
applies common valve-stroke bounds and exposes the final command for tracking
and actuator handoff.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .controllers import GGOV1Parameters, _clip, _number


@dataclass(frozen=True, slots=True)
class GGOV1Selection:
    """Immutable result of one low-value selector evaluation."""

    selected_source: str
    selected_fuel_command_pu: float
    tracking_errors_pu: tuple[tuple[str, float], ...]

    def tracking_error_for(self, source: str) -> float:
        """Return pre-common-limit selected minus this controller's request."""
        for name, error in self.tracking_errors_pu:
            if name == source:
                return error
        raise KeyError(f"controller source {source!r} was not active")


class GGOV1LowValueSelector:
    """Select the minimum active GGOV1 fuel request and expose tracking errors.

    Accepted source names are 'governor', 'acceleration', and
    'load_temperature'. The latter represents the already-combined
    load/temperature limiter output. Disabled limiters are omitted; the
    governor request is always required. Equal values use the stable priority
    governor, acceleration, load_temperature for reproducible source identity.
    """

    SOURCE_PRIORITY = ("governor", "acceleration", "load_temperature")

    @classmethod
    def select(cls, candidates_pu: Mapping[str, float]) -> GGOV1Selection:
        if not isinstance(candidates_pu, Mapping) or not candidates_pu:
            raise TypeError("candidates_pu must be a non-empty mapping")
        unknown = set(candidates_pu) - set(cls.SOURCE_PRIORITY)
        if unknown:
            raise ValueError(f"unsupported GGOV1 selector sources: {sorted(unknown)!r}")
        if "governor" not in candidates_pu:
            raise ValueError("the governor fuel request is required")
        ordered = []
        for source in cls.SOURCE_PRIORITY:
            if source in candidates_pu:
                value = _number(candidates_pu[source], f"{source}_fuel_command_pu")
                ordered.append((source, value))
        selected_source, selected = min(ordered, key=lambda item: item[1])
        tracking = tuple((source, selected - value) for source, value in ordered)
        return GGOV1Selection(
            selected_source=selected_source,
            selected_fuel_command_pu=selected,
            tracking_errors_pu=tracking,
        )




@dataclass(frozen=True, slots=True)
class GGOV1ControlDecision:
    """Final common-limited fuel command and controller tracking feedback."""

    selected_source: str
    selected_unbounded_fuel_command_pu: float
    actuator_command_pu: float
    tracking_errors_pu: tuple[tuple[str, float], ...]

    def tracking_error_for(self, source: str) -> float:
        """Return final command minus the named controller request."""
        for name, error in self.tracking_errors_pu:
            if name == source:
                return error
        raise KeyError(f"controller source {source!r} was not active")


class GGOV1ControlCoordinator:
    """Coordinate selector, common FSR bounds and actuator handoff.

    This is a stateless orchestration boundary. It owns no dynamic state,
    integration clock, event loop or persistent authority. The same final
    bounded command is the actuator setpoint and the tracking feedback for
    the PID, acceleration and temperature/load paths.
    """

    def __init__(self, parameters: GGOV1Parameters) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        self.parameters = parameters

    def coordinate(self, candidates_pu: Mapping[str, float]) -> GGOV1ControlDecision:
        selection = GGOV1LowValueSelector.select(candidates_pu)
        p = self.parameters
        bounded = _clip(
            selection.selected_fuel_command_pu,
            p.valve_min_pu,
            p.valve_max_pu,
        )
        tracking = tuple(
            (
                source,
                bounded - _number(candidates_pu[source], f"{source}_fuel_command_pu"),
            )
            for source in GGOV1LowValueSelector.SOURCE_PRIORITY
            if source in candidates_pu
        )
        return GGOV1ControlDecision(
            selected_source=selection.selected_source,
            selected_unbounded_fuel_command_pu=selection.selected_fuel_command_pu,
            actuator_command_pu=bounded,
            tracking_errors_pu=tracking,
        )


@dataclass(frozen=True, slots=True)
class GGOV1OperatingPoint:
    """Calculated initialization targets and residuals for one GGOV1 plant point.

    The host still initializes each solver-owned block with its own
    initial_state() method. This value object makes shared FSR, valve, fuel
    and temperature-proxy assumptions explicit and reports mismatches that
    must be reconciled before a study is considered initialized.
    """

    electrical_power_pu: float
    mechanical_power_pu: float
    shaft_speed_pu: float
    initial_fuel_stroke_pu: float
    initial_fuel_flow_pu: float
    initial_temperature_proxy_input_pu: float
    pid_integral_target_pu: float
    initial_power_reference_pu: float
    initial_governor_output_pu: float
    load_integral_target_pu: float
    load_limiter_enabled: bool
    load_limiter_request_pu: float
    integral_alignment_error_pu: float
    load_limiter_balance_error_pu: float

    def require_balanced(self, *, tolerance_pu: float = 1e-8) -> None:
        """Reject an operating point whose independently initialized paths disagree."""
        tolerance = _number(tolerance_pu, "tolerance_pu")
        if tolerance <= 0.0:
            raise ValueError("tolerance_pu must be greater than zero")
        if self.load_limiter_enabled and abs(self.integral_alignment_error_pu) > tolerance:
            raise ValueError(
                "PID and load-limiter integral targets disagree: "
                f"{self.integral_alignment_error_pu} pu"
            )
        if self.load_limiter_enabled and abs(self.load_limiter_balance_error_pu) > tolerance:
            raise ValueError(
                "temperature/load limiter is not balanced at the initial FSR: "
                f"{self.load_limiter_balance_error_pu} pu"
            )


def calculate_ggov1_operating_point(
    parameters: GGOV1Parameters,
    *,
    electrical_power_pu: float,
    mechanical_power_pu: float | None = None,
    speed_deviation_pu: float = 0.0,
    initial_governor_output_pu: float = 0.0,
    load_limiter_enabled: bool = True,
) -> GGOV1OperatingPoint:
    """Calculate common initial targets without owning or mutating block state."""
    if not isinstance(parameters, GGOV1Parameters):
        raise TypeError("parameters must be GGOV1Parameters")
    p = parameters
    if not isinstance(load_limiter_enabled, bool):
        raise TypeError("load_limiter_enabled must be bool")
    pelec = _number(electrical_power_pu, "electrical_power_pu")
    pm = pelec if mechanical_power_pu is None else _number(
        mechanical_power_pu, "mechanical_power_pu"
    )
    speed_deviation = _number(speed_deviation_pu, "speed_deviation_pu")
    shaft_speed = 1.0 + speed_deviation
    if shaft_speed <= 0.0:
        raise ValueError("absolute shaft speed must be greater than zero")
    if speed_deviation != 0.0:
        raise ValueError(
            "source-derived steady-state initialization requires nominal speed; "
            "use a disturbance initialization path for nonzero speed deviation"
        )

    fsr = (pm + p.mechanical_damping) / p.turbine_gain + p.no_load_fuel_flow_pu
    if not p.valve_min_pu <= fsr <= p.valve_max_pu:
        raise ValueError(
            "calculated initial FSR is outside common valve limits; "
            "reconcile the operating point before initializing"
        )
    if load_limiter_enabled and fsr > 1.0:
        raise ValueError(
            "calculated initial FSR exceeds the active load limiter's 1.0 pu upper bound"
        )

    fuel_multiplier = shaft_speed if p.flag == 1 else 1.0
    fuel_flow = fsr * fuel_multiplier
    speed_factor = shaft_speed if p.mechanical_damping >= 0.0 else shaft_speed ** p.mechanical_damping
    temperature_proxy = (fuel_flow - p.no_load_fuel_flow_pu) * speed_factor

    pid_integral = (pm + p.mechanical_damping) / p.turbine_gain + p.no_load_fuel_flow_pu
    governor_output = _number(initial_governor_output_pu, "initial_governor_output_pu")
    feedback = p.droop_feedback_signal(
        electrical_power_pu=pelec,
        valve_stroke_pu=fsr,
        governor_output_pu=governor_output,
    )
    initial_power_reference = p.droop_pu * feedback
    load_integral = (pelec + p.mechanical_damping) / p.turbine_gain + p.no_load_fuel_flow_pu
    temperature_reference = p.no_load_fuel_flow_pu + p.load_limit_reference_pu / p.turbine_gain
    temperature_error = temperature_reference - temperature_proxy
    load_request = min(
        1.0,
        p.load_proportional_gain * temperature_error + load_integral,
    )
    return GGOV1OperatingPoint(
        electrical_power_pu=pelec,
        mechanical_power_pu=pm,
        shaft_speed_pu=shaft_speed,
        initial_fuel_stroke_pu=fsr,
        initial_fuel_flow_pu=fuel_flow,
        initial_temperature_proxy_input_pu=temperature_proxy,
        pid_integral_target_pu=pid_integral,
        initial_power_reference_pu=initial_power_reference,
        initial_governor_output_pu=governor_output,
        load_integral_target_pu=load_integral,
        load_limiter_enabled=load_limiter_enabled,
        load_limiter_request_pu=load_request,
        integral_alignment_error_pu=load_integral - pid_integral,
        load_limiter_balance_error_pu=load_request - fsr,
    )


__all__ = [
    "GGOV1LowValueSelector",
    "GGOV1Selection",
    "GGOV1ControlCoordinator",
    "GGOV1ControlDecision",
    "GGOV1OperatingPoint",
    "calculate_ggov1_operating_point",
]
