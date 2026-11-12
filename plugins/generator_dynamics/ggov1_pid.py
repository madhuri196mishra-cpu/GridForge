"""PSS/E-derived GGOV1 frequency-control PID sub-block.

This is a local continuous-time block, not the complete GGOV1 governor. The
host integrator owns time and state; outer low-value selection, limiter
tracking, actuator, turbine/fuel dynamics and DAE coupling remain separate.
"""
from __future__ import annotations

from typing import Any, ClassVar

from plugins.dynamics.base import DynamicPlugin

from .controllers import GGOV1Parameters, _clip, _deadband, _number


class GGOV1PIDGovernor(DynamicPlugin):
    """Four-state PSS/E-derived GGOV1 PID and supervisory reset sub-block.

    State order:
      1. filtered electrical power,
      2. derivative-filter input state,
      3. governor integral contribution,
      4. bounded supervisory power-controller reset contribution.

    Inputs are per-unit on the caller's explicitly reconciled GGOV1 base.
    The class does not convert generator MVA to turbine MW base.
    """

    model_type = "PSS_E_GGOV1_PID_SUBBLOCK"
    state_names: ClassVar[tuple[str, ...]] = (
        "electrical_power_filtered_pu",
        "derivative_filter_state_pu",
        "governor_integral_output_pu",
        "power_controller_reset_output_pu",
    )

    def __init__(self, parameters: GGOV1Parameters) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        if parameters.governor_derivative_gain > 0.0 and parameters.governor_derivative_time_constant_s <= 0.0:
            raise ValueError("positive derivative gain requires a positive derivative filter time constant")
        self.parameters = parameters

    def _error(
        self,
        state: tuple[float, ...] | list[float],
        *,
        speed_deviation_pu: float,
        power_reference_pu: float,
        valve_stroke_pu: float,
        governor_output_pu: float,
        electrical_power_pu: float | None = None,
    ) -> float:
        p = self.parameters
        if p.electrical_power_time_constant_s == 0.0:
            if electrical_power_pu is None:
                raise ValueError(
                    "electrical_power_pu is required when T_pelec is zero (algebraic bypass)"
                )
            feedback_power = _number(electrical_power_pu, "electrical_power_pu")
        else:
            feedback_power = _number(state[0], "electrical_power_filtered_pu")
        feedback = p.droop_feedback_signal(
            electrical_power_pu=feedback_power,
            valve_stroke_pu=valve_stroke_pu,
            governor_output_pu=governor_output_pu,
        )
        raw = (
            _number(power_reference_pu, "power_reference_pu")
            + _number(state[3], "power_controller_reset_output_pu")
            - _number(speed_deviation_pu, "speed_deviation_pu")
            - p.droop_pu * feedback
        )
        return _clip(
            _deadband(raw, p.speed_deadband_pu),
            p.governor_error_min_pu,
            p.governor_error_max_pu,
        )

    def initial_state(
        self,
        *,
        initial_electrical_power_pu: float,
        initial_speed_deviation_pu: float = 0.0,
        initial_power_reference_pu: float = 0.0,
        initial_valve_stroke_pu: float = 0.0,
        initial_governor_output_pu: float = 0.0,
        initial_mechanical_power_pu: float | None = None,
        **_: Any,
    ) -> tuple[float, ...]:
        p = self.parameters
        pelec = _number(initial_electrical_power_pu, "initial_electrical_power_pu")
        pm0 = pelec if initial_mechanical_power_pu is None else _number(
            initial_mechanical_power_pu, "initial_mechanical_power_pu"
        )
        # PSS/E-derived OpenIPSL initialization: FSR0=(Pm0 + Dm)/Kturb + Wfnl.
        fsr0 = (pm0 + p.mechanical_damping) / p.turbine_gain + p.no_load_fuel_flow_pu
        state = (pelec, 0.0, fsr0, 0.0)
        error = self._error(
            state,
            speed_deviation_pu=initial_speed_deviation_pu,
            power_reference_pu=initial_power_reference_pu,
            valve_stroke_pu=initial_valve_stroke_pu,
            governor_output_pu=initial_governor_output_pu,
            electrical_power_pu=pelec,
        )
        # A filtered derivative initialized to the current error has zero
        # derivative output at the operating point.
        return (pelec, error, fsr0, 0.0)

    def derivatives(
        self,
        state: tuple[float, ...] | list[float],
        *,
        electrical_power_pu: float,
        speed_deviation_pu: float,
        power_reference_pu: float,
        supervisory_power_setpoint_pu: float = 0.0,
        valve_stroke_pu: float = 0.0,
        governor_output_pu: float = 0.0,
        selected_fuel_command_pu: float | None = None,
        tracking_time_constant_s: float | None = None,
        **_: Any,
    ) -> tuple[float, ...]:
        self.validate_state(state)
        pelec_f, derivative_state, integral, reset = (
            _number(value, "GGOV1 PID state") for value in state
        )
        pelec = _number(electrical_power_pu, "electrical_power_pu")
        pmw_set = _number(supervisory_power_setpoint_pu, "supervisory_power_setpoint_pu")
        p = self.parameters
        error = self._error(
            state,
            speed_deviation_pu=speed_deviation_pu,
            power_reference_pu=power_reference_pu,
            valve_stroke_pu=valve_stroke_pu,
            governor_output_pu=governor_output_pu,
            electrical_power_pu=pelec,
        )

        d_pelec = (
            0.0 if p.electrical_power_time_constant_s == 0.0
            else (pelec - pelec_f) / p.electrical_power_time_constant_s
        )
        d_derivative_state = (
            0.0 if p.governor_derivative_time_constant_s == 0.0
            else (error - derivative_state) / p.governor_derivative_time_constant_s
        )
        d_integral = p.governor_integral_gain_per_s * error
        if (selected_fuel_command_pu is None) != (tracking_time_constant_s is None):
            raise ValueError(
                "selected_fuel_command_pu and tracking_time_constant_s must be supplied together"
            )
        if selected_fuel_command_pu is not None:
            tracking_time = _number(tracking_time_constant_s, "tracking_time_constant_s")
            if tracking_time <= 0.0:
                raise ValueError("tracking_time_constant_s must be greater than zero")
            governor_output = (
                p.governor_proportional_gain * error
                + integral
                + (
                    0.0 if p.governor_derivative_time_constant_s == 0.0
                    else p.governor_derivative_gain
                    * (error - derivative_state)
                    / p.governor_derivative_time_constant_s
                )
            )
            tracking_error = (
                _number(selected_fuel_command_pu, "selected_fuel_command_pu")
                - governor_output
            )
            d_integral += tracking_error / tracking_time

        reset_limit = 1.1 * p.droop_pu
        measured_power = pelec if p.electrical_power_time_constant_s == 0.0 else pelec_f
        d_reset = p.power_controller_integral_gain_per_s * (pmw_set - measured_power)
        if (reset >= reset_limit and d_reset > 0.0) or (
            reset <= -reset_limit and d_reset < 0.0
        ):
            d_reset = 0.0
        if reset_limit == 0.0:
            d_reset = 0.0
        return (d_pelec, d_derivative_state, d_integral, d_reset)

    def output(
        self,
        state: tuple[float, ...] | list[float],
        *,
        speed_deviation_pu: float,
        power_reference_pu: float,
        valve_stroke_pu: float = 0.0,
        governor_output_pu: float = 0.0,
        electrical_power_pu: float | None = None,
        **_: Any,
    ) -> float:
        self.validate_state(state)
        p = self.parameters
        error = self._error(
            state,
            speed_deviation_pu=speed_deviation_pu,
            power_reference_pu=power_reference_pu,
            valve_stroke_pu=valve_stroke_pu,
            governor_output_pu=governor_output_pu,
            electrical_power_pu=electrical_power_pu,
        )
        derivative_state = _number(state[1], "derivative_filter_state_pu")
        integral = _number(state[2], "governor_integral_output_pu")
        derivative = (
            0.0 if p.governor_derivative_time_constant_s == 0.0
            else p.governor_derivative_gain
            * (error - derivative_state)
            / p.governor_derivative_time_constant_s
        )
        return (
            p.governor_proportional_gain * error
            + integral
            + derivative
        )

    def summary(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "parameters": self.parameters,
            "scope": "PSS/E-derived PID and supervisory reset sub-block only",
            "outer_ggov1_integration": "not implemented",
        }


__all__ = ["GGOV1PIDGovernor"]
