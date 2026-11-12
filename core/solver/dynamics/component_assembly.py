"""Pure evaluation boundary for registered dynamic-state extensions.

This module evaluates component derivatives from one detached global-state
snapshot and returns canonical machine power handoffs. It deliberately does
not invoke machine equations, solve the network, integrate, or commit sampled
feedback; the host must coordinate those operations in one accepted-step-aware
DAE evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

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
    equipment_kind/equipment_id; they are never represented as synthetic
    synchronous machines.
    """

    component_id: str
    machine_id: str | None
    bus_id: str
    terminal_id: str
    equipment_kind: str = "generator"
    equipment_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("component_id", "bus_id", "terminal_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise DynamicComponentAssemblyError(f"{name} must be a non-empty stable identity")
            object.__setattr__(self, name, value.strip())
        if self.equipment_kind not in {"generator", "motor", "load"}:
            raise DynamicComponentAssemblyError("equipment_kind must be generator, motor or load")
        if self.machine_id is not None:
            if not isinstance(self.machine_id, str) or not self.machine_id.strip():
                raise DynamicComponentAssemblyError("machine_id must be None or a non-empty identity")
            if self.equipment_kind != "generator":
                raise DynamicComponentAssemblyError("only generator bindings may drive a machine state block")
            object.__setattr__(self, "machine_id", self.machine_id.strip())
        elif self.equipment_kind == "generator":
            raise DynamicComponentAssemblyError("generator bindings require a canonical machine_id")
        equipment_id = self.equipment_id
        if equipment_id is None:
            equipment_id = self.machine_id
        if not isinstance(equipment_id, str) or not equipment_id.strip():
            raise DynamicComponentAssemblyError("equipment_id must be a non-empty canonical Core identity")
        object.__setattr__(self, "equipment_id", equipment_id.strip())


@dataclass(frozen=True, slots=True)
class DynamicComponentBatchEvaluation:
    """Immutable component derivative blocks and machine power handoffs."""

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
    to which it is bound. Multiple components may not drive the same machine
    in one evaluation; the host must define an explicit aggregation policy
    before such a topology can be supported.
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
    equipment_keys: set[tuple[str, str]] = set()

    for component_id, component in registered.items():
        binding = binding_by_id[component_id]
        equipment_key = (binding.equipment_kind, binding.equipment_id)
        if equipment_key in equipment_keys:
            raise DynamicComponentAssemblyError(
                f"canonical equipment {equipment_key!r} is bound more than once"
            )
        equipment_keys.add(equipment_key)
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
            if injection.terminal_id != binding.terminal_id:
                raise DynamicComponentAssemblyError(
                    f"component {component_id!r} cannot inject at unbound terminal {injection.terminal_id!r}"
                )
            terminal_injections.append(injection)

    return DynamicComponentBatchEvaluation(
        component_derivatives=component_derivatives,
        mechanical_power_outputs_pu=mechanical_outputs,
        terminal_injections=tuple(terminal_injections),
    )


__all__ = [
    "DynamicComponentAssemblyError",
    "DynamicComponentBinding",
    "DynamicComponentBatchEvaluation",
    "evaluate_dynamic_components",
]
