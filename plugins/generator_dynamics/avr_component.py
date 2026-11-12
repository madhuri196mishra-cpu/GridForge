"""Core dynamic-state adapter for the generator plugin's first-order AVR."""
from __future__ import annotations

import math
from typing import Sequence

from core.solver.dynamics.component_contract import (
    DynamicComponentEvaluation,
    DynamicComponentInputs,
)
from plugins.generator_dynamics.controllers import FirstOrderAVR


class AVRComponentBindingError(ValueError):
    """Raised when an AVR component cannot be evaluated from its bound terminal."""


class FirstOrderAVRStateComponent:
    """Expose a FirstOrderAVR as a pure, solver-owned dynamic state component.

    The wrapper owns only immutable configuration and stable identities.
    Continuous state remains in the host's global DAE vector. Terminal voltage
    is supplied by the same-stage algebraic network solution through
    DynamicComponentInputs.
    """

    def __init__(
        self,
        *,
        component_id: str,
        machine_id: str,
        terminal_id: str,
        avr: FirstOrderAVR,
        voltage_reference_pu: float,
        initial_field_voltage_pu: float,
        stabilizing_signal_pu: float = 0.0,
        initial_regulator_output_pu: float | None = None,
    ) -> None:
        for name, value in (
            ("component_id", component_id),
            ("machine_id", machine_id),
            ("terminal_id", terminal_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise AVRComponentBindingError(f"{name} must be a non-empty stable identity")
        if not isinstance(avr, FirstOrderAVR):
            raise TypeError("avr must be FirstOrderAVR")
        for name, value in (
            ("voltage_reference_pu", voltage_reference_pu),
            ("initial_field_voltage_pu", initial_field_voltage_pu),
            ("stabilizing_signal_pu", stabilizing_signal_pu),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be finite numeric data")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite numeric data")
        if initial_regulator_output_pu is not None:
            if isinstance(initial_regulator_output_pu, bool) or not isinstance(initial_regulator_output_pu, (int, float)):
                raise TypeError("initial_regulator_output_pu must be finite numeric data")
            if not math.isfinite(float(initial_regulator_output_pu)):
                raise ValueError("initial_regulator_output_pu must be finite numeric data")
        self._component_id = component_id
        self.machine_id = machine_id
        self.terminal_id = terminal_id
        self.avr = avr
        self.voltage_reference_pu = float(voltage_reference_pu)
        self.initial_field_voltage_pu = float(initial_field_voltage_pu)
        self.stabilizing_signal_pu = float(stabilizing_signal_pu)
        self.initial_regulator_output_pu = (
            None if initial_regulator_output_pu is None else float(initial_regulator_output_pu)
        )

    @property
    def component_id(self) -> str:
        return self._component_id

    @property
    def state_size(self) -> int:
        return len(self.avr.state_names)

    def initial_state(self, inputs: DynamicComponentInputs) -> Sequence[float]:
        if not isinstance(inputs, DynamicComponentInputs):
            raise TypeError("inputs must be DynamicComponentInputs")
        if self.terminal_id not in inputs.terminal_voltages:
            raise AVRComponentBindingError(
                f"initial AVR state requires terminal voltage {self.terminal_id!r}"
            )
        terminal_voltage = abs(inputs.terminal_voltages[self.terminal_id])
        return self.avr.initial_state(
            terminal_voltage_pu=terminal_voltage,
            field_voltage_pu=self.initial_field_voltage_pu,
            regulator_output_pu=self.initial_regulator_output_pu,
        )

    def evaluate(
        self,
        state: Sequence[float],
        inputs: DynamicComponentInputs,
    ) -> DynamicComponentEvaluation:
        if not isinstance(inputs, DynamicComponentInputs):
            raise TypeError("inputs must be DynamicComponentInputs")
        if self.terminal_id not in inputs.terminal_voltages:
            raise AVRComponentBindingError(
                f"AVR evaluation requires terminal voltage {self.terminal_id!r}"
            )
        derivative = self.avr.derivatives(
            state,
            terminal_voltage_pu=abs(inputs.terminal_voltages[self.terminal_id]),
            voltage_reference_pu=self.voltage_reference_pu,
            stabilizing_signal_pu=self.stabilizing_signal_pu,
        )
        return DynamicComponentEvaluation(
            state_derivative=tuple(derivative),
            mechanical_power_outputs_pu={},
        )

    def field_voltage_pu(self, state: Sequence[float]) -> float:
        """Read the state-owned AVR output used to construct the network stage."""
        return self.avr.output(state)


__all__ = ["AVRComponentBindingError", "FirstOrderAVRStateComponent"]
