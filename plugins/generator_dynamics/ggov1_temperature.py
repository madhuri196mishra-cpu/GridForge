"""GGOV1 modeled exhaust-temperature signal path.

The OpenIPSL/PSS/E topology derives a temperature proxy from net fuel flow
and the Dm-dependent speed factor, then cascades the Tsa/Tsb lead-lag with
the Tfload lag. The host integrator owns both states and all time history.
"""
from __future__ import annotations

from typing import Any, ClassVar

from plugins.dynamics.base import DynamicPlugin

from .controllers import GGOV1Parameters, _number


class GGOV1TemperatureMeasurement(DynamicPlugin):
    """Two-state GGOV1 temperature-proxy conditioner producing TEXM."""

    model_type = "PSS_E_GGOV1_TEMPERATURE_MEASUREMENT"
    state_names: ClassVar[tuple[str, ...]] = (
        "temperature_detection_lead_lag_state",
        "filtered_exhaust_temperature_proxy",
    )

    def __init__(self, parameters: GGOV1Parameters) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        if (
            parameters.temperature_lag_time_constant_s == 0.0
            and parameters.temperature_lead_time_constant_s > 0.0
        ):
            raise ValueError("positive Tsa requires positive Tsb for a proper lead-lag filter")
        self.parameters = parameters

    def _lead_lag_output(self, state: float, signal: float) -> float:
        p = self.parameters
        tsb = p.temperature_lag_time_constant_s
        if tsb == 0.0:
            return signal
        ratio = p.temperature_lead_time_constant_s / tsb
        return state + ratio * (signal - state)

    def initial_state(
        self,
        *,
        initial_temperature_proxy_input_pu: float,
        **_: Any,
    ) -> tuple[float, ...]:
        initial = _number(initial_temperature_proxy_input_pu, "initial_temperature_proxy_input_pu")
        return (initial, initial)

    def derivatives(
        self,
        state: tuple[float, ...] | list[float],
        *,
        temperature_proxy_input_pu: float,
        **_: Any,
    ) -> tuple[float, ...]:
        self.validate_state(state)
        p = self.parameters
        signal = _number(temperature_proxy_input_pu, "temperature_proxy_input_pu")
        lead_lag_state = _number(state[0], "temperature_detection_lead_lag_state")
        texm_state = _number(state[1], "filtered_exhaust_temperature_proxy")
        tsb = p.temperature_lag_time_constant_s
        tfload = p.load_filter_time_constant_s
        d_lead_lag = 0.0 if tsb == 0.0 else (signal - lead_lag_state) / tsb
        lead_lag_output = self._lead_lag_output(lead_lag_state, signal)
        d_texm = 0.0 if tfload == 0.0 else (lead_lag_output - texm_state) / tfload
        return (d_lead_lag, d_texm)

    def output(
        self,
        state: tuple[float, ...] | list[float],
        *,
        temperature_proxy_input_pu: float,
        **_: Any,
    ) -> float:
        self.validate_state(state)
        p = self.parameters
        signal = _number(temperature_proxy_input_pu, "temperature_proxy_input_pu")
        lead_lag_state = _number(state[0], "temperature_detection_lead_lag_state")
        texm_state = _number(state[1], "filtered_exhaust_temperature_proxy")
        if p.load_filter_time_constant_s == 0.0:
            return self._lead_lag_output(lead_lag_state, signal)
        return texm_state

    def summary(self) -> dict[str, Any]:
        p = self.parameters
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "transfer_function": "((1+s*Tsa)/(1+s*Tsb)) * 1/(1+s*Tfload)",
            "time_constants_s": {
                "Tsa": p.temperature_lead_time_constant_s,
                "Tsb": p.temperature_lag_time_constant_s,
                "Tfload": p.load_filter_time_constant_s,
            },
            "signal_source": "net fuel flow multiplied by Dm_select(speed)",
            "scope": (
                "PSS/E/OpenIPSL modeled TEXM proxy; not a thermocouple model or OEM firing-temperature map"
            ),
        }


__all__ = ["GGOV1TemperatureMeasurement"]
