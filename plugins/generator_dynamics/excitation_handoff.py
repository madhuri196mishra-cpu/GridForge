"""Immutable AVR-to-machine excitation snapshot contract.

Build this snapshot before algebraic network solution. The same field-voltage
mapping must be supplied to network current-injection assembly and the
MultiMachineSystem electrical-output/derivative calls for that solver stage.
This module does not solve the network or advance AVR state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Sequence

from .controllers import FirstOrderAVR


class ExcitationHandoffError(ValueError):
    """Raised when AVR outputs cannot be bound to the active machine set."""


@dataclass(frozen=True, slots=True)
class AVRGeneratorBinding:
    """Bind one AVR component to an existing Core machine and its terminals."""

    component_id: str
    machine_id: str
    bus_id: str
    terminal_id: str

    def __post_init__(self) -> None:
        for name in ("component_id", "machine_id", "bus_id", "terminal_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ExcitationHandoffError(f"{name} must be a non-empty stable identity")


@dataclass(frozen=True, slots=True)
class GeneratorExcitationSnapshot:
    """One immutable, time-stamped AVR output snapshot for a network stage."""

    simulation_time_s: float
    field_voltages_pu: Mapping[str, float]
    component_ids_by_machine: Mapping[str, str]

    def __post_init__(self) -> None:
        time = self.simulation_time_s
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(float(time)) or time < 0.0:
            raise ExcitationHandoffError("simulation_time_s must be finite and non-negative")
        fields: dict[str, float] = {}
        for machine_id, value in self.field_voltages_pu.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise ExcitationHandoffError("field-voltage keys must be machine identities")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0.0:
                raise ExcitationHandoffError(f"field voltage for {machine_id!r} must be finite and non-negative")
            fields[machine_id] = float(value)
        sources = dict(self.component_ids_by_machine)
        if set(sources) != set(fields):
            raise ExcitationHandoffError("AVR source identities must match the field-voltage machine identities")
        if any(not isinstance(source, str) or not source.strip() for source in sources.values()):
            raise ExcitationHandoffError("AVR source component identities must be non-empty strings")
        object.__setattr__(self, "simulation_time_s", float(time))
        object.__setattr__(self, "field_voltages_pu", MappingProxyType(fields))
        object.__setattr__(self, "component_ids_by_machine", MappingProxyType(sources))


def build_generator_excitation_snapshot(
    *,
    simulation_time_s: float,
    bindings: Sequence[AVRGeneratorBinding],
    avrs_by_component: Mapping[str, FirstOrderAVR],
    states_by_component: Mapping[str, Sequence[float]],
) -> GeneratorExcitationSnapshot:
    """Read state-owned AVR field outputs before the algebraic network solve.

    The supplied AVR objects and states must match the binding set exactly.
    One AVR may drive only one machine and each machine may have only one AVR.
    """
    binding_values = tuple(bindings)
    if not binding_values or any(not isinstance(item, AVRGeneratorBinding) for item in binding_values):
        raise ExcitationHandoffError("at least one valid AVRGeneratorBinding is required")
    component_ids = [item.component_id for item in binding_values]
    machine_ids = [item.machine_id for item in binding_values]
    if len(set(component_ids)) != len(component_ids):
        raise ExcitationHandoffError("each AVR component may be bound only once")
    if len(set(machine_ids)) != len(machine_ids):
        raise ExcitationHandoffError("a machine may have only one AVR field-voltage driver")
    expected = set(component_ids)
    if set(avrs_by_component) != expected or set(states_by_component) != expected:
        raise ExcitationHandoffError("AVR objects and state snapshots must match the binding component set exactly")

    field_voltages: dict[str, float] = {}
    sources: dict[str, str] = {}
    for binding in binding_values:
        avr = avrs_by_component[binding.component_id]
        if not isinstance(avr, FirstOrderAVR):
            raise TypeError(f"component {binding.component_id!r} must use FirstOrderAVR")
        value = float(avr.output(states_by_component[binding.component_id]))
        if not math.isfinite(value) or value < 0.0:
            raise ExcitationHandoffError(f"AVR {binding.component_id!r} returned invalid field voltage")
        field_voltages[binding.machine_id] = value
        sources[binding.machine_id] = binding.component_id

    return GeneratorExcitationSnapshot(
        simulation_time_s=simulation_time_s,
        field_voltages_pu=field_voltages,
        component_ids_by_machine=sources,
    )


__all__ = [
    "ExcitationHandoffError",
    "AVRGeneratorBinding",
    "GeneratorExcitationSnapshot",
    "build_generator_excitation_snapshot",
]
