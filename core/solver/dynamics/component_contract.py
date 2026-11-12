"""Side-effect-free contract for solver-owned dynamic state components.

The contract separates extension-owned dynamic state from canonical Core equipment
identity. Generator controller components may hand off mechanical power;
motor/load components may contribute phase-resolved terminal current while
Core remains responsible for equipment, terminals and network topology.

This module defines the boundary only. MultiMachineSystem and DAESolver do
not yet consume it; wiring requires a coordinated global-state and
accepted/rejected-step extension.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Protocol, Sequence, runtime_checkable


class DynamicComponentContractError(ValueError):
    """Raised when a dynamic-component contract value is inconsistent."""


def _finite_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DynamicComponentContractError(f"{field_name} must be finite numeric data")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise DynamicComponentContractError(f"{field_name} must be finite numeric data")
    return numeric


def _finite_vector(values: Sequence[float], field_name: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise DynamicComponentContractError(f"{field_name} must be a numeric sequence")
    try:
        result = tuple(_finite_number(value, field_name) for value in values)
    except TypeError as exc:
        raise DynamicComponentContractError(f"{field_name} must be a numeric sequence") from exc
    return result


@dataclass(frozen=True, slots=True)
class DynamicComponentInputs:
    """Read-only operating-point inputs supplied by one DAE evaluation.

    Voltages are keyed by authoritative terminal identity, not inferred from
    component IDs. Electrical and mechanical powers use canonical machine
    identities and the caller's declared study per-unit base.
    """

    simulation_time_s: float
    terminal_voltages: Mapping[str, complex]
    electrical_active_power_pu: Mapping[str, float]
    mechanical_power_inputs_pu: Mapping[str, float]

    def __post_init__(self) -> None:
        time_value = _finite_number(self.simulation_time_s, "simulation_time_s")
        if time_value < 0.0:
            raise DynamicComponentContractError("simulation_time_s must be non-negative")
        voltages: dict[str, complex] = {}
        for terminal_id, value in self.terminal_voltages.items():
            if not isinstance(terminal_id, str) or not terminal_id.strip():
                raise DynamicComponentContractError("terminal voltage keys must be non-empty identities")
            try:
                voltage = complex(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise DynamicComponentContractError("terminal voltages must be finite complex values") from exc
            if not math.isfinite(voltage.real) or not math.isfinite(voltage.imag):
                raise DynamicComponentContractError("terminal voltages must be finite complex values")
            voltages[terminal_id] = voltage

        def freeze_power_map(source: Mapping[str, float], name: str) -> Mapping[str, float]:
            result: dict[str, float] = {}
            for machine_id, value in source.items():
                if not isinstance(machine_id, str) or not machine_id.strip():
                    raise DynamicComponentContractError(f"{name} keys must be non-empty machine identities")
                result[machine_id] = _finite_number(value, f"{name}[{machine_id}]")
            return MappingProxyType(result)

        object.__setattr__(self, "simulation_time_s", time_value)
        object.__setattr__(self, "terminal_voltages", MappingProxyType(voltages))
        object.__setattr__(
            self,
            "electrical_active_power_pu",
            freeze_power_map(self.electrical_active_power_pu, "electrical_active_power_pu"),
        )
        object.__setattr__(
            self,
            "mechanical_power_inputs_pu",
            freeze_power_map(self.mechanical_power_inputs_pu, "mechanical_power_inputs_pu"),
        )


@dataclass(frozen=True, slots=True)
class DynamicTerminalInjection:
    """One phase-resolved current contribution into the solved network.

    current_injection_pu uses the study's declared per-unit current base and
    the network-injection sign convention: positive current is injected from
    equipment into the network. phase_id is a canonical host-supplied label;
    this DTO does not infer phase mapping.
    """

    injection_id: str
    terminal_id: str
    phase_id: str
    current_injection_pu: complex
    current_base_a: float

    def __post_init__(self) -> None:
        for name in ("injection_id", "terminal_id", "phase_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DynamicComponentContractError(f"{name} must be a non-empty stable identity")
            object.__setattr__(self, name, value.strip())
        try:
            current = complex(self.current_injection_pu)
        except (TypeError, ValueError, OverflowError) as exc:
            raise DynamicComponentContractError(
                "current_injection_pu must be a finite complex value"
            ) from exc
        if not math.isfinite(current.real) or not math.isfinite(current.imag):
            raise DynamicComponentContractError(
                "current_injection_pu must be a finite complex value"
            )
        object.__setattr__(self, "current_injection_pu", current)
        base = _finite_number(self.current_base_a, "current_base_a")
        if base <= 0.0:
            raise DynamicComponentContractError("current_base_a must be positive")
        object.__setattr__(self, "current_base_a", base)


@dataclass(frozen=True, slots=True)
class DynamicComponentEvaluation:
    """Pure derivative, optional machine handoff and terminal injections."""

    state_derivative: tuple[float, ...]
    mechanical_power_outputs_pu: Mapping[str, float]
    terminal_injections: tuple[DynamicTerminalInjection, ...] = ()

    def __post_init__(self) -> None:
        derivative = _finite_vector(self.state_derivative, "state_derivative")
        outputs: dict[str, float] = {}
        for machine_id, value in self.mechanical_power_outputs_pu.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise DynamicComponentContractError(
                    "mechanical_power_outputs_pu keys must be non-empty machine identities"
                )
            outputs[machine_id] = _finite_number(value, f"mechanical_power_outputs_pu[{machine_id}]")
        injections = tuple(self.terminal_injections)
        if any(not isinstance(item, DynamicTerminalInjection) for item in injections):
            raise DynamicComponentContractError(
                "terminal_injections must contain DynamicTerminalInjection values"
            )
        injection_ids = [item.injection_id for item in injections]
        if len(injection_ids) != len(set(injection_ids)):
            raise DynamicComponentContractError("terminal injection identities must be unique per component")
        object.__setattr__(self, "state_derivative", derivative)
        object.__setattr__(self, "mechanical_power_outputs_pu", MappingProxyType(outputs))
        object.__setattr__(self, "terminal_injections", injections)


def validate_dynamic_state_component(component: object) -> tuple[str, int]:
    """Validate structural identity and state dimension before host registration."""
    component_id = getattr(component, "component_id", None)
    state_size = getattr(component, "state_size", None)
    if not isinstance(component_id, str) or not component_id.strip():
        raise DynamicComponentContractError("component_id must be a non-empty stable identity")
    if type(state_size) is not int or state_size <= 0:
        raise DynamicComponentContractError("state_size must be a positive integer")
    if not callable(getattr(component, "initial_state", None)):
        raise DynamicComponentContractError("component must provide initial_state(inputs)")
    if not callable(getattr(component, "evaluate", None)):
        raise DynamicComponentContractError("component must provide evaluate(state, inputs)")
    return component_id, state_size


@runtime_checkable
class DynamicStateComponent(Protocol):
    """Structural protocol for solver-owned equipment/control dynamic components.

    Implementations must be referentially transparent: initial_state and
    evaluate may not advance clocks, mutate component state, dispatch events,
    or commit sampled controller feedback. Any sampled/discrete feedback is
    passed as immutable input and committed only by an accepted-step owner.
    """

    @property
    def component_id(self) -> str:
        """Stable unique component identity within the study."""

    @property
    def state_size(self) -> int:
        """Positive number of continuous states contributed by the component."""

    def initial_state(self, inputs: DynamicComponentInputs) -> Sequence[float]:
        """Return the initial continuous state snapshot."""

    def evaluate(
        self,
        state: Sequence[float],
        inputs: DynamicComponentInputs,
    ) -> DynamicComponentEvaluation:
        """Return derivatives and optional canonical machine power outputs."""


__all__ = [
    "DynamicComponentContractError",
    "DynamicComponentInputs",
    "DynamicTerminalInjection",
    "DynamicComponentEvaluation",
    "DynamicStateComponent",
    "validate_dynamic_state_component",
]
