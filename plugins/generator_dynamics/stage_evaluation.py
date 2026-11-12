"""Coordinated generator AVR/network/derivative evaluation for one DAE stage.

The host supplies authoritative terminal projection, electrical-power
calculation and the existing machine derivative evaluator. This adapter
coordinates their ordering around one immutable excitation/network snapshot;
it does not integrate, commit state, or replace DAESolver.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

from core.solver.dynamics.component_assembly import DynamicComponentBinding
from core.solver.dynamics.component_contract import DynamicComponentInputs, DynamicStateComponent
from core.solver.dynamics.derivative_assembly import (
    DynamicSystemDerivativeAssembly,
    evaluate_composed_dynamic_derivatives,
)
from core.solver.dynamics.dynamic_state_layout import DynamicSystemStateLayout

from .avr_component import FirstOrderAVRStateComponent
from .excitation_handoff import (
    AVRGeneratorBinding,
    GeneratorExcitationSnapshot,
    build_generator_excitation_snapshot,
)
from .network_stage import (
    GeneratorNetworkStageRequest,
    GeneratorNetworkStageSolution,
    GeneratorNetworkSolver,
    solve_generator_network_stage,
)


class GeneratorDynamicStageError(ValueError):
    """Raised when coordinated generator stage evaluation is inconsistent."""


TerminalVoltageProjector = Callable[
    [Mapping[str, complex], Sequence[DynamicComponentBinding]],
    Mapping[str, complex],
]
ElectricalPowerEvaluator = Callable[
    [np.ndarray, Mapping[str, complex], Mapping[str, float]],
    Mapping[str, float],
]
GeneratorMachineDerivativeEvaluator = Callable[..., Sequence[float] | np.ndarray]


@dataclass(frozen=True, slots=True)
class GeneratorDynamicStageResult:
    """Immutable evidence from one coordinated generator DAE stage."""

    excitation: GeneratorExcitationSnapshot
    network: GeneratorNetworkStageSolution
    component_inputs: DynamicComponentInputs
    derivative_assembly: DynamicSystemDerivativeAssembly

    def __post_init__(self) -> None:
        if self.excitation.simulation_time_s != self.network.simulation_time_s:
            raise GeneratorDynamicStageError("excitation and network stage times differ")
        if dict(self.excitation.field_voltages_pu) != dict(self.network.field_voltages_pu):
            raise GeneratorDynamicStageError("network result does not retain the exact excitation snapshot")
        if dict(self.excitation.component_ids_by_machine) != dict(self.network.component_ids_by_machine):
            raise GeneratorDynamicStageError("network result AVR provenance differs from excitation snapshot")
        if self.component_inputs.simulation_time_s != self.network.simulation_time_s:
            raise GeneratorDynamicStageError("component input time differs from network stage time")


def evaluate_generator_dynamic_stage(
    *,
    global_state: Sequence[float] | np.ndarray,
    simulation_time_s: float,
    layout: DynamicSystemStateLayout,
    components: Sequence[DynamicStateComponent],
    component_bindings: Sequence[DynamicComponentBinding],
    avr_components: Sequence[FirstOrderAVRStateComponent],
    network_solver: GeneratorNetworkSolver,
    terminal_voltage_projector: TerminalVoltageProjector,
    electrical_power_evaluator: ElectricalPowerEvaluator,
    machine_bus_ids: Mapping[str, str],
    base_mechanical_powers_pu: Mapping[str, float],
    machine_derivative_evaluator: GeneratorMachineDerivativeEvaluator,
    machine_connection_states: Mapping[str, bool] | None = None,
) -> GeneratorDynamicStageResult:
    """Evaluate a single continuous DAE stage in excitation/network order.

    The machine derivative callback is called with the same field-voltage map
    that was used for the network solve. It must forward that map to the
    canonical MultiMachineSystem derivative API (whose field-voltage override
    is optional for legacy callers).
    """
    if not isinstance(layout, DynamicSystemStateLayout):
        raise TypeError("layout must be DynamicSystemStateLayout")
    vector = np.asarray(global_state, dtype=float)
    if vector.ndim != 1 or vector.size != layout.state_size or not np.all(np.isfinite(vector)):
        raise GeneratorDynamicStageError("global_state must match the finite composed state layout")
    if isinstance(simulation_time_s, bool) or not isinstance(simulation_time_s, (int, float)) or not np.isfinite(float(simulation_time_s)) or simulation_time_s < 0:
        raise GeneratorDynamicStageError("simulation_time_s must be finite and non-negative")
    if not callable(terminal_voltage_projector) or not callable(electrical_power_evaluator):
        raise TypeError("terminal voltage and electrical power evaluators must be callable")
    if not callable(machine_derivative_evaluator):
        raise TypeError("machine_derivative_evaluator must be callable")

    machine_state, component_states = layout.unpack(vector)
    component_values = tuple(components)
    binding_values = tuple(component_bindings)
    avr_values = tuple(avr_components)
    if not avr_values or any(not isinstance(component, FirstOrderAVRStateComponent) for component in avr_values):
        raise TypeError("avr_components must contain at least one FirstOrderAVRStateComponent")
    if any(not isinstance(binding, DynamicComponentBinding) for binding in binding_values):
        raise TypeError("component_bindings must contain DynamicComponentBinding values")
    by_component = {component.component_id: component for component in avr_values}
    if len(by_component) != len(avr_values):
        raise GeneratorDynamicStageError("AVR component identities must be unique")
    registered_by_id = {getattr(component, "component_id", None): component for component in component_values}
    if any(registered_by_id.get(key) is not value for key, value in by_component.items()):
        raise GeneratorDynamicStageError("each AVR adapter must be the exact component registered for its state block")
    if set(by_component) - set(component_states):
        raise GeneratorDynamicStageError("every AVR component must have a state block in the global layout")

    binding_by_id = {binding.component_id: binding for binding in binding_values}
    if len(binding_by_id) != len(binding_values):
        raise GeneratorDynamicStageError("component bindings must have unique component identities")
    avr_bindings: list[AVRGeneratorBinding] = []
    for component_id, component in by_component.items():
        binding = binding_by_id.get(component_id)
        if binding is None:
            raise GeneratorDynamicStageError(f"AVR {component_id!r} has no dynamic component binding")
        if (binding.machine_id, binding.terminal_id) != (component.machine_id, component.terminal_id):
            raise GeneratorDynamicStageError(f"AVR {component_id!r} identity differs from its component binding")
        avr_bindings.append(AVRGeneratorBinding(
            component_id=component_id,
            machine_id=component.machine_id,
            bus_id=binding.bus_id,
            terminal_id=component.terminal_id,
        ))

    snapshot = build_generator_excitation_snapshot(
        simulation_time_s=float(simulation_time_s),
        bindings=avr_bindings,
        avrs_by_component={key: value.avr for key, value in by_component.items()},
        states_by_component={key: component_states[key] for key in by_component},
    )
    network = solve_generator_network_stage(
        GeneratorNetworkStageRequest(machine_state=machine_state, excitation=snapshot),
        network_solver,
    )
    terminal_voltages = terminal_voltage_projector(network.bus_voltages, binding_values)
    if not isinstance(terminal_voltages, Mapping) or not terminal_voltages:
        raise GeneratorDynamicStageError("terminal_voltage_projector must return a non-empty terminal mapping")
    electrical_powers = electrical_power_evaluator(
        machine_state.copy(),
        network.bus_voltages,
        network.field_voltages_pu,
    )
    if not isinstance(electrical_powers, Mapping):
        raise GeneratorDynamicStageError("electrical_power_evaluator must return a machine-power mapping")
    inputs = DynamicComponentInputs(
        simulation_time_s=float(simulation_time_s),
        terminal_voltages=terminal_voltages,
        electrical_active_power_pu=electrical_powers,
        mechanical_power_inputs_pu=base_mechanical_powers_pu,
    )

    def evaluate_machine_derivatives(**kwargs):
        return machine_derivative_evaluator(
            **kwargs,
            field_voltages_pu=network.field_voltages_pu,
        )

    assembly = evaluate_composed_dynamic_derivatives(
        layout=layout,
        machine_state=machine_state,
        component_states=component_states,
        components=component_values,
        bindings=binding_values,
        inputs=inputs,
        bus_voltages=network.bus_voltages,
        machine_bus_ids=machine_bus_ids,
        base_mechanical_powers_pu=base_mechanical_powers_pu,
        machine_derivative_evaluator=evaluate_machine_derivatives,
        machine_connection_states=machine_connection_states,
    )
    return GeneratorDynamicStageResult(
        excitation=snapshot,
        network=network,
        component_inputs=inputs,
        derivative_assembly=assembly,
    )


__all__ = [
    "GeneratorDynamicStageError",
    "GeneratorDynamicStageResult",
    "TerminalVoltageProjector",
    "ElectricalPowerEvaluator",
    "GeneratorMachineDerivativeEvaluator",
    "evaluate_generator_dynamic_stage",
]
