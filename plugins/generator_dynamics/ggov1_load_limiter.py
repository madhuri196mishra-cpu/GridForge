"""PSS/E-derived GGOV1 exhaust-temperature/load limiter.

The block implements the OpenIPSL LoadLimiter topology: LDREF is converted
through Kturb and Wfnl, compared with conditioned exhaust temperature TEXM,
then passed through a proportional-plus-integral controller. Temperature
conditioning (Tsa/Tsb) remains the separate GGOV1TemperatureMeasurement block.
"""
from __future__ import annotations

from typing import Any, ClassVar

from plugins.dynamics.base import DynamicPlugin

from .controllers import GGOV1Parameters, _number


class GGOV1LoadLimiter(DynamicPlugin):
    """One-state GGOV1 exhaust-temperature/load-limit PI sub-block.

    State: integral fuel-stroke contribution. Inputs are expressed on the
    GGOV1 turbine base. The caller supplies TEXM after the separate Tsa/Tsb
    conditioner and supplies electrical power only for operating-point
    initialization. FSRT has the PSS/E/OpenIPSL upper limit of 1.0 pu and no
    artificial lower clamp.
    """

    model_type = "PSS_E_GGOV1_EXHAUST_TEMPERATURE_LOAD_LIMITER"
    state_names: ClassVar[tuple[str, ...]] = ("load_limiter_integral_output_pu",)

    def __init__(self, parameters: GGOV1Parameters) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        self.parameters = parameters

    def _temperature_error(self, exhaust_temperature_pu: float) -> float:
        p = self.parameters
        texm = _number(exhaust_temperature_pu, "exhaust_temperature_pu")
        temperature_reference = (
            p.no_load_fuel_flow_pu
            + p.load_limit_reference_pu / p.turbine_gain
        )
        return temperature_reference - texm

    def initial_state(
        self,
        *,
        initial_electrical_power_pu: float,
        initial_integral_output_pu: float | None = None,
        **_: Any,
    ) -> tuple[float, ...]:
        p = self.parameters
        pelec = _number(initial_electrical_power_pu, "initial_electrical_power_pu")
        integral = (
            (pelec + p.mechanical_damping) / p.turbine_gain
            + p.no_load_fuel_flow_pu
            if initial_integral_output_pu is None
            else _number(initial_integral_output_pu, "initial_integral_output_pu")
        )
        return (integral,)

    def derivatives(
        self,
        state: tuple[float, ...] | list[float],
        *,
        exhaust_temperature_pu: float,
        selected_fuel_command_pu: float | None = None,
        tracking_time_constant_s: float | None = None,
        **_: Any,
    ) -> tuple[float, ...]:
        self.validate_state(state)
        p = self.parameters
        integral = _number(state[0], "load_limiter_integral_output_pu")
        error = self._temperature_error(exhaust_temperature_pu)
        derivative = p.load_integral_gain_per_s * error
        if (selected_fuel_command_pu is None) != (tracking_time_constant_s is None):
            raise ValueError(
                "selected_fuel_command_pu and tracking_time_constant_s must be supplied together"
            )
        if selected_fuel_command_pu is not None:
            tracking_time = _number(tracking_time_constant_s, "tracking_time_constant_s")
            if tracking_time <= 0.0:
                raise ValueError("tracking_time_constant_s must be greater than zero")
            raw_request = p.load_proportional_gain * error + integral
            selected = _number(selected_fuel_command_pu, "selected_fuel_command_pu")
            derivative += (selected - raw_request) / tracking_time
        return (derivative,)

    def output(
        self,
        state: tuple[float, ...] | list[float],
        *,
        exhaust_temperature_pu: float,
        **_: Any,
    ) -> float:
        self.validate_state(state)
        p = self.parameters
        error = self._temperature_error(exhaust_temperature_pu)
        integral = _number(state[0], "load_limiter_integral_output_pu")
        raw_request = p.load_proportional_gain * error + integral
        return min(1.0, raw_request)

    def summary(self) -> dict[str, Any]:
        p = self.parameters
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "parameters": p,
            "equations": {
                "temperature_reference_pu": "Wfnl + LDREF / Kturb",
                "error": "temperature_reference - TEXM",
                "integral_derivative": "Kiload * error",
                "FSRT": "min(1.0, Kpload * error + integral)",
            },
            "scope": (
                "PSS/E-derived OpenIPSL LoadLimiter topology; TEXM conditioning "
                "is supplied separately; OEM calibration and plant validation required"
            ),
        }


__all__ = ["GGOV1LoadLimiter"]
