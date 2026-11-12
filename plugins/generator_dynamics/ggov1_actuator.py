"""GGOV1 fuel-valve actuator with position and slew-rate constraints.

The solver owns the continuous-time state. This block implements the
first-order Tact response with asymmetric opening/closing rate limits and
hard valve-position bounds. It is not the full GGOV1 turbine/fuel subsystem.
"""
from __future__ import annotations

from typing import Any, ClassVar

from plugins.dynamics.base import DynamicPlugin

from .controllers import GGOV1Parameters, _clip, _number


class GGOV1ValveActuator(DynamicPlugin):
    """Single-state GGOV1 fuel-valve actuator sub-block.

    State: actual valve position (pu).
    Input: governor_fuel_command_pu, the final common-limited command from
    GGOV1ControlCoordinator.actuator_command_pu. The actuator retains its own
    defensive position clamp and independent asymmetric slew limits.
    """

    model_type = "PSS_E_GGOV1_VALVE_ACTUATOR"
    state_names: ClassVar[tuple[str, ...]] = ("valve_position_pu",)

    def __init__(self, parameters: GGOV1Parameters) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        self.parameters = parameters

    def initial_state(
        self,
        *,
        initial_valve_position_pu: float,
        **_: Any,
    ) -> tuple[float, ...]:
        p = self.parameters
        position = _number(initial_valve_position_pu, "initial_valve_position_pu")
        return (_clip(position, p.valve_min_pu, p.valve_max_pu),)

    def derivatives(
        self,
        state: tuple[float, ...] | list[float],
        *,
        governor_fuel_command_pu: float,
        **_: Any,
    ) -> tuple[float, ...]:
        self.validate_state(state)
        p = self.parameters
        position = _number(state[0], "valve_position_pu")
        command = _number(governor_fuel_command_pu, "governor_fuel_command_pu")
        bounded_command = _clip(command, p.valve_min_pu, p.valve_max_pu)

        # First-order actuator response, followed by asymmetric slew limits.
        requested_rate = (bounded_command - position) / p.actuator_time_constant_s
        rate = _clip(
            requested_rate,
            p.valve_close_rate_pu_per_s,
            p.valve_open_rate_pu_per_s,
        )

        # Prevent integration farther beyond a hard position stop. An
        # out-of-range solver trial state is allowed to move back in-range.
        if position >= p.valve_max_pu and rate > 0.0:
            rate = 0.0
        elif position <= p.valve_min_pu and rate < 0.0:
            rate = 0.0
        return (rate,)

    def output(
        self,
        state: tuple[float, ...] | list[float],
        **_: Any,
    ) -> float:
        self.validate_state(state)
        position = _number(state[0], "valve_position_pu")
        p = self.parameters
        return _clip(position, p.valve_min_pu, p.valve_max_pu)

    def summary(self) -> dict[str, Any]:
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "parameters": self.parameters,
            "position_limits_pu": (self.parameters.valve_min_pu, self.parameters.valve_max_pu),
            "rate_limits_pu_per_s": (
                self.parameters.valve_close_rate_pu_per_s,
                self.parameters.valve_open_rate_pu_per_s,
            ),
            "scope": "GGOV1 first-order fuel-valve actuator only",
            "outer_ggov1_integration": "command handoff is provided by GGOV1ControlCoordinator; host owns scheduling and state integration",
        }


__all__ = ["GGOV1ValveActuator"]
