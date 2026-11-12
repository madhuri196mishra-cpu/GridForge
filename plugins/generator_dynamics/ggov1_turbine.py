"""GGOV1 turbine fuel-to-mechanical-power path.

The host integrator owns the state and time. Positive Teng is represented by
an explicit delayed-fuel-flow input supplied by the solver's delay facility;
this block does not keep a private clock, sample queue, or delay history.
"""
from __future__ import annotations

from typing import Any, ClassVar

from plugins.dynamics.base import DynamicPlugin

from .controllers import GGOV1Parameters, _number


class GGOV1TurbineFuelPath(DynamicPlugin):
    """One-state GGOV1 fuel/turbine response sub-block.

    Inputs are on the declared turbine per-unit base:
      * valve_stroke_pu: actual actuator output, not its command;
      * shaft_speed_pu: absolute shaft speed (nominal is 1.0);
      * speed_deviation_pu: shaft-speed deviation from nominal;
      * delayed_fuel_flow_pu: required only when Teng > 0, from a solver-owned
        transport-delay implementation.

    The state is the lagged net fuel flow (fuel flow minus Wfnl). The temperature-proxy input is the un-delayed net fuel flow multiplied by the Dm-dependent speed factor. For Tb > 0,
    the transfer (1+s*Tc)/(1+s*Tb) is represented by a first-order lag state
    and its algebraic lead term. Tb=Tc=0 is an algebraic bypass.
    """

    model_type = "PSS_E_GGOV1_TURBINE_FUEL_PATH"
    state_names: ClassVar[tuple[str, ...]] = ("net_fuel_flow_lag_pu",)

    def __init__(self, parameters: GGOV1Parameters) -> None:
        if not isinstance(parameters, GGOV1Parameters):
            raise TypeError("parameters must be GGOV1Parameters")
        self.parameters = parameters

    def _fuel_flow(
        self,
        *,
        valve_stroke_pu: float,
        shaft_speed_pu: float,
        delayed_fuel_flow_pu: float | None,
    ) -> float:
        p = self.parameters
        if p.engine_transport_time_constant_s > 0.0:
            if delayed_fuel_flow_pu is None:
                raise ValueError(
                    "delayed_fuel_flow_pu is required when Teng > 0; "
                    "the solver must provide the delayed signal"
                )
            return _number(delayed_fuel_flow_pu, "delayed_fuel_flow_pu")
        valve = _number(valve_stroke_pu, "valve_stroke_pu")
        speed = _number(shaft_speed_pu, "shaft_speed_pu")
        return valve * p.fuel_speed_multiplier(speed)

    def _dm_select_factor(self, speed_deviation_pu: float) -> float:
        speed_factor = 1.0 + _number(speed_deviation_pu, "speed_deviation_pu")
        if speed_factor <= 0.0:
            raise ValueError("absolute shaft speed (1 + SPEED) must be greater than zero")
        damping = self.parameters.mechanical_damping
        return speed_factor if damping >= 0.0 else speed_factor ** damping

    def temperature_proxy_input(
        self,
        *,
        valve_stroke_pu: float,
        speed_deviation_pu: float,
    ) -> float:
        """Return the pre-Tsa/Tsb fuel/temperature proxy input used by GGOV1."""
        p = self.parameters
        valve = _number(valve_stroke_pu, "valve_stroke_pu")
        speed = 1.0 + _number(speed_deviation_pu, "speed_deviation_pu")
        fuel_flow = valve * p.fuel_speed_multiplier(speed)
        net_fuel_flow = fuel_flow - p.no_load_fuel_flow_pu
        return net_fuel_flow * self._dm_select_factor(speed_deviation_pu)
    def initial_state(
        self,
        *,
        initial_valve_stroke_pu: float,
        initial_shaft_speed_pu: float = 1.0,
        initial_fuel_flow_pu: float | None = None,
        **_: Any,
    ) -> tuple[float, ...]:
        if initial_fuel_flow_pu is None:
            fuel_flow = self._fuel_flow(
                valve_stroke_pu=initial_valve_stroke_pu,
                shaft_speed_pu=initial_shaft_speed_pu,
                delayed_fuel_flow_pu=None,
            )
        else:
            fuel_flow = _number(initial_fuel_flow_pu, "initial_fuel_flow_pu")
        return (fuel_flow - self.parameters.no_load_fuel_flow_pu,)

    def derivatives(
        self,
        state: tuple[float, ...] | list[float],
        *,
        valve_stroke_pu: float,
        shaft_speed_pu: float = 1.0,
        delayed_fuel_flow_pu: float | None = None,
        **_: Any,
    ) -> tuple[float, ...]:
        self.validate_state(state)
        p = self.parameters
        lagged_net_fuel = _number(state[0], "net_fuel_flow_lag_pu")
        fuel_flow = self._fuel_flow(
            valve_stroke_pu=valve_stroke_pu,
            shaft_speed_pu=shaft_speed_pu,
            delayed_fuel_flow_pu=delayed_fuel_flow_pu,
        )
        if p.turbine_lag_time_constant_s == 0.0:
            return (0.0,)
        net_fuel = fuel_flow - p.no_load_fuel_flow_pu
        return ((net_fuel - lagged_net_fuel) / p.turbine_lag_time_constant_s,)

    def output(
        self,
        state: tuple[float, ...] | list[float],
        *,
        valve_stroke_pu: float,
        shaft_speed_pu: float = 1.0,
        speed_deviation_pu: float = 0.0,
        delayed_fuel_flow_pu: float | None = None,
        **_: Any,
    ) -> float:
        self.validate_state(state)
        p = self.parameters
        fuel_flow = self._fuel_flow(
            valve_stroke_pu=valve_stroke_pu,
            shaft_speed_pu=shaft_speed_pu,
            delayed_fuel_flow_pu=delayed_fuel_flow_pu,
        )
        net_fuel = fuel_flow - p.no_load_fuel_flow_pu
        if p.turbine_lag_time_constant_s == 0.0:
            turbine_response = net_fuel
        else:
            lagged_net_fuel = _number(state[0], "net_fuel_flow_lag_pu")
            lead_ratio = (
                p.turbine_lead_time_constant_s
                / p.turbine_lag_time_constant_s
            )
            turbine_response = (
                lead_ratio * (net_fuel - lagged_net_fuel) + lagged_net_fuel
            )
        damping_factor = self._dm_select_factor(speed_deviation_pu)
        mechanical_damping = p.mechanical_damping * damping_factor
        return p.turbine_gain * turbine_response - mechanical_damping

    def summary(self) -> dict[str, Any]:
        p = self.parameters
        return {
            "model_type": self.model_type,
            "state_names": self.state_names,
            "parameters": p,
            "fuel_speed_rule": "Flag=1: valve stroke times absolute shaft speed; Flag=0: valve stroke",
            "temperature_proxy": "net fuel flow multiplied by Dm_select(speed)",
            "transport_delay": (
                "solver-supplied delayed_fuel_flow_pu"
                if p.engine_transport_time_constant_s > 0.0
                else "bypassed (Teng=0)"
            ),
            "scope": "GGOV1 turbine/fuel response and temperature-proxy input sub-block; no limiter selection or DAE coupling",
        }


__all__ = ["GGOV1TurbineFuelPath"]
