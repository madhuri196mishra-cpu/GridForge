"""Pure evaluation boundary for registered dynamic-state extensions.

This module evaluates component derivatives from one detached global-state
snapshot and returns machine power handoffs plus terminal current injections. It deliberately does
not invoke machine equations, solve the network, integrate, or commit sampled
feedback; the host must coordinate those operations in one accepted-step-aware
DAE evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from core.model import EquipmentType

from .component_contract import (
    DynamicComponentEvaluation,
    DynamicComponentInputs,
    DynamicStateComponent,
    DynamicTerminalInjection,
    validate_dynamic_state_component,
)
from .dynamic_state_layout import DynamicSystemStateLayout


class DynamicComponentAssemblyError(ValueError):
    """Raised when extension bindings or their evaluated outputs conflict."""


@dataclass(frozen=True, slots=True)
class DynamicComponentBinding:
    """Bind a state component to one canonical equipment identity and terminal.

    Generator components retain the machine_id handoff. Motor and load
    components set machine_id=None and identify their Core equipment through
    equipment_type/equipment_id; they are never represented as synthetic
    synchronous machines.
    """

    component_id: str
    machine_id: str | None
    bus_id: str
    terminal_id: str
    equipment_type: EquipmentType = EquipmentType.GENERATOR
    equipment_id: str | None = None
    terminal_ids: tuple[str, ...] = ()
    terminal_bus_ids: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        for name in ("component_id", "bus_id", "terminal_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DynamicComponentAssemblyError(f"{name} must be a non-empty stable identity")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.equipment_type, EquipmentType):
            raise DynamicComponentAssemblyError("equipment_type must be canonical Core EquipmentType")
        if self.equipment_type not in {EquipmentType.GENERATOR, EquipmentType.MOTOR, EquipmentType.LOAD}:
            raise DynamicComponentAssemblyError("equipment_type must identify a generator, motor or load")
        if self.machine_id is not None:
            if not isinstance(self.machine_id, str) or not self.machine_id.strip():
                raise DynamicComponentAssemblyError("machine_id must be None or a non-empty identity")
            if self.equipment_type is not EquipmentType.GENERATOR:
                raise DynamicComponentAssemblyError("only generator bindings may drive a machine state block")
            object.__setattr__(self, "machine_id", self.machine_id.strip())
        elif self.equipment_type is EquipmentType.GENERATOR:
            raise DynamicComponentAssemblyError("generator bindings require a canonical machine_id")
        equipment_id = self.equipment_id
        if equipment_id is None:
            equipment_id = self.machine_id
        if not isinstance(equipment_id, str) or not equipment_id.strip():
            raise DynamicComponentAssemblyError("equipment_id must be a non-empty canonical Core identity")
        object.__setattr__(self, "equipment_id", equipment_id.strip())
        terminals = tuple(self.terminal_ids) if self.terminal_ids else (self.terminal_id,)
        if any(not isinstance(value, str) or not value.strip() for value in terminals):
            raise DynamicComponentAssemblyError("terminal_ids must contain non-empty Core terminal identities")
        terminals = tuple(value.strip() for value in terminals)
        if len(terminals) != len(set(terminals)):
            raise DynamicComponentAssemblyError("terminal_ids must be unique per equipment binding")
        if self.terminal_id not in terminals:
            raise DynamicComponentAssemblyError("terminal_ids must include the primary terminal_id")
        object.__setattr__(self, "terminal_ids", terminals)
        raw_bus_map = self.terminal_bus_ids
        if raw_bus_map is None:
            if len(terminals) != 1:
                raise DynamicComponentAssemblyError(
                    "multi-terminal bindings require an explicit terminal_bus_ids mapping"
                )
            raw_bus_map = {self.terminal_id: self.bus_id}
        if not isinstance(raw_bus_map, Mapping):
            raise DynamicComponentAssemblyError("terminal_bus_ids must be a mapping")
        bus_map: dict[str, str] = {}
        for terminal, bus in raw_bus_map.items():
            if not isinstance(terminal, str) or not terminal.strip():
                raise DynamicComponentAssemblyError("terminal_bus_ids keys must be stable terminal identities")
            if not isinstance(bus, str) or not bus.strip():
                raise DynamicComponentAssemblyError("terminal_bus_ids values must be stable bus identities")
            bus_map[terminal.strip()] = bus.strip()
        if set(bus_map) != set(terminals):
            raise DynamicComponentAssemblyError(
                "terminal_bus_ids must map every and only explicitly bound terminal"
            )
        if bus_map[self.terminal_id] != self.bus_id:
            raise DynamicComponentAssemblyError("primary terminal bus must match bus_id")
        object.__setattr__(self, "terminal_bus_ids", MappingProxyType(bus_map))


@dataclass(frozen=True, slots=True)
class DynamicComponentBatchEvaluation:
    """Immutable derivative blocks, machine handoffs and terminal injections."""

    component_derivatives: Mapping[str, tuple[float, ...]]
    mechanical_power_outputs_pu: Mapping[str, float]
    terminal_injections: tuple[DynamicTerminalInjection, ...] = ()

    def __post_init__(self) -> None:
        derivatives: dict[str, tuple[float, ...]] = {}
        for component_id, values in self.component_derivatives.items():
            if not isinstance(component_id, str) or not component_id.strip():
                raise DynamicComponentAssemblyError("component derivative keys must be stable identities")
            block = tuple(float(value) for value in values)
            if not block or not np.all(np.isfinite(block)):
                raise DynamicComponentAssemblyError("component derivative blocks must be non-empty and finite")
            derivatives[component_id] = block
        powers: dict[str, float] = {}
        for machine_id, value in self.mechanical_power_outputs_pu.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise DynamicComponentAssemblyError("mechanical power output keys must be stable machine identities")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DynamicComponentAssemblyError("mechanical power outputs must be finite numeric values")
            numeric = float(value)
            if not np.isfinite(numeric):
                raise DynamicComponentAssemblyError("mechanical power outputs must be finite numeric values")
            powers[machine_id] = numeric
        injections = tuple(self.terminal_injections)
        if any(not isinstance(item, DynamicTerminalInjection) for item in injections):
            raise DynamicComponentAssemblyError(
                "terminal_injections must contain DynamicTerminalInjection values"
            )
        injection_ids = [item.injection_id for item in injections]
        if len(injection_ids) != len(set(injection_ids)):
            raise DynamicComponentAssemblyError("terminal injection identities must be unique per batch")
        object.__setattr__(self, "component_derivatives", MappingProxyType(derivatives))
        object.__setattr__(self, "mechanical_power_outputs_pu", MappingProxyType(powers))
        object.__setattr__(self, "terminal_injections", injections)


def evaluate_dynamic_components(
    *,
    components: Sequence[DynamicStateComponent],
    bindings: Sequence[DynamicComponentBinding],
    layout: DynamicSystemStateLayout,
    global_state: Sequence[float] | np.ndarray,
    inputs: DynamicComponentInputs,
) -> DynamicComponentBatchEvaluation:
    """Evaluate all extension blocks against one consistent state/input snapshot.

    Each component receives its own immutable local state and the shared
    read-only study inputs. Its returned derivative must match its reserved
    state slice. A component may return mechanical power only for the machine
    to which it is bound. Motor/load components bind directly to their Core
    equipment identities and may emit terminal current injections only at the
    bound terminal. Multiple controllers may share one generator, but only one
    may provide its mechanical-power handoff in an evaluation.
    """
    if not isinstance(layout, DynamicSystemStateLayout):
        raise TypeError("layout must be DynamicSystemStateLayout")
    if not isinstance(inputs, DynamicComponentInputs):
        raise TypeError("inputs must be DynamicComponentInputs")
    components = tuple(components)
    bindings = tuple(bindings)
    _, component_states = layout.unpack(global_state)

    binding_by_id: dict[str, DynamicComponentBinding] = {}
    for binding in bindings:
        if not isinstance(binding, DynamicComponentBinding):
            raise DynamicComponentAssemblyError("bindings must contain DynamicComponentBinding values")
        if binding.component_id in binding_by_id:
            raise DynamicComponentAssemblyError(f"duplicate binding for component {binding.component_id!r}")
        binding_by_id[binding.component_id] = binding

    registered: dict[str, DynamicStateComponent] = {}
    for component in components:
        try:
            component_id, state_size = validate_dynamic_state_component(component)
        except (TypeError, ValueError) as exc:
            raise DynamicComponentAssemblyError(f"invalid component registration: {exc}") from exc
        if component_id in registered:
            raise DynamicComponentAssemblyError(f"duplicate component registration {component_id!r}")
        block = next((item for item in layout.component_blocks if item.owner_id == component_id), None)
        if block is None or block.size != state_size:
            raise DynamicComponentAssemblyError(f"component {component_id!r} does not match the global state layout")
        registered[component_id] = component

    if set(binding_by_id) != set(registered):
        raise DynamicComponentAssemblyError("bindings must match registered components exactly")
    machine_ids = {block.owner_id for block in layout.machine_blocks}
    component_derivatives: dict[str, tuple[float, ...]] = {}
    mechanical_outputs: dict[str, float] = {}
    terminal_injections: list[DynamicTerminalInjection] = []

    for component_id, component in registered.items():
        binding = binding_by_id[component_id]
        if binding.machine_id is not None:
            if binding.machine_id not in machine_ids:
                raise DynamicComponentAssemblyError(
                    f"component {component_id!r} references unknown machine {binding.machine_id!r}"
                )
            if binding.machine_id not in inputs.electrical_active_power_pu:
                raise DynamicComponentAssemblyError(
                    f"component {component_id!r} has no electrical-power input for machine {binding.machine_id!r}"
                )
        if binding.terminal_id not in inputs.terminal_voltages:
            raise DynamicComponentAssemblyError(
                f"component {component_id!r} references unavailable terminal {binding.terminal_id!r}"
            )
        local_state = component_states[component_id]
        result = component.evaluate(local_state, inputs)
        if not isinstance(result, DynamicComponentEvaluation):
            raise DynamicComponentAssemblyError(
                f"component {component_id!r} must return DynamicComponentEvaluation"
            )
        if len(result.state_derivative) != len(local_state):
            raise DynamicComponentAssemblyError(
                f"component {component_id!r} derivative dimension does not match its state slice"
            )
        component_derivatives[component_id] = result.state_derivative
        if binding.machine_id is None and result.mechanical_power_outputs_pu:
            raise DynamicComponentAssemblyError(
                f"non-generator component {component_id!r} cannot emit machine mechanical-power handoffs"
            )
        for machine_id, power in result.mechanical_power_outputs_pu.items():
            if machine_id != binding.machine_id:
                raise DynamicComponentAssemblyError(
                    f"component {component_id!r} cannot drive unbound machine {machine_id!r}"
                )
            if machine_id in mechanical_outputs:
                raise DynamicComponentAssemblyError(
                    f"multiple dynamic components drive machine {machine_id!r}; aggregation is undefined"
                )
            mechanical_outputs[machine_id] = power
        for injection in result.terminal_injections:
            if injection.terminal_id not in binding.terminal_ids:
                raise DynamicComponentAssemblyError(
                    f"component {component_id!r} cannot inject at unbound terminal {injection.terminal_id!r}"
                )
            terminal_injections.append(injection)

    return DynamicComponentBatchEvaluation(
        component_derivatives=component_derivatives,
        mechanical_power_outputs_pu=mechanical_outputs,
        terminal_injections=tuple(terminal_injections),
    )


def aggregate_dynamic_terminal_injections(
    injections: Sequence[DynamicTerminalInjection],
    *,
    current_bases_a: Mapping[tuple[str, str], float],
) -> Mapping[tuple[str, str], complex]:
    """Convert and sum device currents on each network terminal/phase base.

    Each source contribution carries its own ampere base. The host supplies
    the target network current base for each (terminal_id, phase_id), which
    permits correct aggregation when source/return terminals use different
    voltage bases. This function does not resolve terminal-to-bus topology or
    invoke a network solver.
    """
    if not isinstance(current_bases_a, Mapping):
        raise TypeError("current_bases_a must be a mapping keyed by terminal and phase")
    values = tuple(injections)
    if any(not isinstance(item, DynamicTerminalInjection) for item in values):
        raise DynamicComponentAssemblyError(
            "injections must contain DynamicTerminalInjection values"
        )
    ids = [item.injection_id for item in values]
    if len(ids) != len(set(ids)):
        raise DynamicComponentAssemblyError("terminal injection identities must be unique")
    grouped: dict[tuple[str, str], list[tuple[str, complex]]] = {}
    for item in values:
        key = (item.terminal_id, item.phase_id)
        base = current_bases_a.get(key)
        if isinstance(base, bool) or not isinstance(base, (int, float)) or not isfinite(float(base)) or base <= 0.0:
            raise DynamicComponentAssemblyError(
                f"network current base for terminal/phase {key!r} must be finite and positive"
            )
        amperes = item.current_injection_pu * item.current_base_a
        network_pu = amperes / float(base)
        if not (isfinite(network_pu.real) and isfinite(network_pu.imag)):
            raise DynamicComponentAssemblyError(
                f"aggregated current for terminal/phase {key!r} is non-finite"
            )
        grouped.setdefault(key, []).append((item.injection_id, network_pu))

    aggregated: dict[tuple[str, str], complex] = {}
    for key in sorted(grouped):
        ordered = sorted(grouped[key], key=lambda item: item[0])
        aggregated[key] = complex(
            fsum(value.real for _, value in ordered),
            fsum(value.imag for _, value in ordered),
        )
    return MappingProxyType(aggregated)


__all__ = [
    "DynamicComponentAssemblyError",
    "DynamicComponentBinding",
    "DynamicComponentBatchEvaluation",
    "evaluate_dynamic_components",
    "aggregate_dynamic_terminal_injections",
]
