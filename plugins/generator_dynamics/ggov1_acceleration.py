"""GGOV1 acceleration-control request sub-block.

The PSS/E/OpenIPSL topology differentiates SPEED through the Ta filter and
adds Ka*DELT*(ASET - d(SPEED)/dt) to the already selected FSR command. The
host owns the state and supplies the model's discrete sample interval.
"""
from __future__ import annotations

from typing import Any, ClassVar

from plugins.dynamics.base import DynamicPlugin

from .controllers import GGOV1Parameters, _number


class GGOV1AccelerationLimiter(DynamicPlugin):
    """One-state filtered speed derivative and algebraic FSRA request.

    The FSR input is the currently selected/limited fuel-stroke command. This
    feedback is the reference model's tracking path; no second FSRA integrator
    or private sample clock is introduced.
    """

    model_type = "PSS_E_GGOV1_ACCELERATION_LIMITER"
    state_names: ClassVar[tuple[str, ...]] = ("filtered_speed_deviation_pu",)

    def __init__(self, parameters: GGOV1Parameters) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        if parameters.acceleration_filter_time_constant_s <= 0.0:
            raise ValueError("GGOV1 Ta must be greater than zero")
        self.parameters = parameters

    def initial_state(
        self,
        *,
        initial_speed_deviation_pu: float = 0.0,
        **_: Any,
    ) -> tuple[float, ...]:
        speed = _number(initial_speed_deviation_pu, "initial_speed_deviation_pu")
        return (speed,)

    def derivatives(
        self,
        state: tuple[float, ...] | list[float],
        *,
        speed_deviation_pu: float,
        **_: Any,
    ) -> tuple[float, ...]:
        self.validate_state(state)
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        filtered = _number(state[0], "filtered_speed_deviation_pu")
        return (
            (speed - filtered) / self.parameters.acceleration_filter_time_constant_s,
        )

    def output(
        self,
        state: tuple[float, ...] | list[float],
        *,
        speed_deviation_pu: float,
        selected_fuel_command_pu: float,
        sample_interval_s: float,
        **_: Any,
    ) -> float:
        self.validate_state(state)
        p = self.parameters
        speed = _number(speed_deviation_pu, "speed_deviation_pu")
        filtered = _number(state[0], "filtered_speed_deviation_pu")
        selected = _number(selected_fuel_command_pu, "selected_fuel_command_pu")
        sample_interval = _number(sample_interval_s, "sample_interval_s")
        if sample_interval <= 0.0:
            raise ValueError("sample_interval_s must be greater than zero")
        speed_derivative = (
            speed - filtered
        ) / p.acceleration_filter_time_constant_s
        return selected + (
            p.acceleration_limiter_gain
            * sample_interval
            * (p.acceleration_limit_pu_per_s - speed_derivative)
        )

    def summary(self) -> dict[str, Any]:
        p = self.parameters
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "parameters": p,
            "equations": {
                "filtered_speed_derivative": (
                    "d(x_speed)/dt = (SPEED - x_speed) / Ta"
                ),
                "speed_acceleration": "(SPEED - x_speed) / Ta",
                "FSRA": "FSR + Ka * DELT * (ASET - speed_acceleration)",
            },
            "scope": (
                "PSS/E/OpenIPSL acceleration limiter sub-block; caller supplies "
                "the selected FSR command and discrete sample interval"
            ),
        }


__all__ = ["GGOV1AccelerationLimiter"]
