"""Coordinated pure derivative assembly for machines and dynamic extensions.

The caller supplies one already-solved network snapshot and matching component
inputs. This boundary evaluates continuous extension components, applies their
explicit machine mechanical-power handoffs, and delegates machine equations to
the existing machine-system callback. It owns no network solve, integration,
event dispatch, or accepted-step feedback commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

import numpy as np

from .component_assembly import (
    DynamicComponentBinding,
    DynamicComponentBatchEvaluation,
    evaluate_dynamic_components,
)
from .component_contract import DynamicComponentInputs, DynamicStateComponent
from .dynamic_state_layout import DynamicSystemStateLayout


class DynamicDerivativeAssemblyError(ValueError):
    """Raised when coordinated machine/component derivative assembly fails."""


@dataclass(frozen=True, slots=True)
class DynamicSystemDerivativeAssembly:
    """Detached result of one complete differential-side assembly."""

    state_derivatives: np.ndarray
    mechanical_powers_pu: Mapping[str, float]
    component_evaluation: DynamicComponentBatchEvaluation

    def __post_init__(self) -> None:
        derivatives = np.asarray(self.state_derivatives, dtype=float).copy()
        if derivatives.ndim != 1 or derivatives.size == 0 or not np.all(np.isfinite(derivatives)):
            raise DynamicDerivativeAssemblyError("assembled state derivatives must be a finite non-empty vector")
        powers: dict[str, float] = {}
        for machine_id, value in self.mechanical_powers_pu.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise DynamicDerivativeAssemblyError("mechanical-power keys must be stable machine identities")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)):
                raise DynamicDerivativeAssemblyError("mechanical-power values must be finite numeric values")
            powers[machine_id] = float(value)
        if not isinstance(self.component_evaluation, DynamicComponentBatchEvaluation):
            raise TypeError("component_evaluation must be DynamicComponentBatchEvaluation")
        derivatives.setflags(write=False)
        object.__setattr__(self, "state_derivatives", derivatives)
        object.__setattr__(self, "mechanical_powers_pu", MappingProxyType(powers))

    @property
    def terminal_injections(self):
        """Phase-resolved equipment current contributions for network assembly."""
        return self.component_evaluation.terminal_injections


MachineDerivativeEvaluator = Callable[..., Sequence[float] | np.ndarray]


def evaluate_composed_dynamic_derivatives(
    *,
    layout: DynamicSystemStateLayout,
    machine_state: Sequence[float] | np.ndarray,
    component_states: Mapping[str, Sequence[float] | np.ndarray],
    components: Sequence[DynamicStateComponent],
    bindings: Sequence[DynamicComponentBinding],
    inputs: DynamicComponentInputs,
    bus_voltages: Mapping[str, complex],
    machine_bus_ids: Mapping[str, str],
    base_mechanical_powers_pu: Mapping[str, float],
    machine_derivative_evaluator: MachineDerivativeEvaluator,
    machine_connection_states: Mapping[str, bool] | None = None,
) -> DynamicSystemDerivativeAssembly:
    """Assemble one pure derivative sample from a coherent electrical snapshot.

    Phase order:
      1. Validate machine/component identities and the sample timestamp.
      2. Pack the existing machine vector with detached extension states.
      3. Evaluate continuous extension components from that same snapshot.
      4. Replace (never add to) base mechanical power for explicitly driven
         machines; untouched machines retain their base input.
      5. Delegate machine equations to the existing machine derivative API.
      6. Assemble machine and component derivatives using the frozen offsets.

    The network solver must run before this function. The supplied
    `inputs.terminal_voltages` are keyed by authoritative terminal IDs while
    `bus_voltages` are keyed by the bus IDs consumed by existing machine
    equations. Each binding explicitly connects all three identities.
    """
    if not isinstance(layout, DynamicSystemStateLayout):
        raise TypeError("layout must be DynamicSystemStateLayout")
    if not isinstance(inputs, DynamicComponentInputs):
        raise TypeError("inputs must be DynamicComponentInputs")
    if not callable(machine_derivative_evaluator):
        raise TypeError("machine_derivative_evaluator must be callable")
    machine_vector = np.asarray(machine_state, dtype=float)
    if machine_vector.ndim != 1 or machine_vector.size != layout.machine_state_size or not np.all(np.isfinite(machine_vector)):
        raise DynamicDerivativeAssemblyError("machine_state must match the finite machine state segment")

    machine_ids = {block.owner_id for block in layout.machine_blocks}
    if set(base_mechanical_powers_pu) != machine_ids:
        raise DynamicDerivativeAssemblyError("base mechanical-power identities must match machine state owners exactly")
    if set(machine_bus_ids) != machine_ids:
        raise DynamicDerivativeAssemblyError("machine bus identities must match machine state owners exactly")
    if any(not isinstance(bus_id, str) or not bus_id.strip() for bus_id in machine_bus_ids.values()):
        raise DynamicDerivativeAssemblyError("machine bus identities must be non-empty strings")
    base_powers: dict[str, float] = {}
    for machine_id, value in base_mechanical_powers_pu.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)):
            raise DynamicDerivativeAssemblyError(f"base mechanical power for {machine_id!r} must be finite numeric data")
        base_powers[machine_id] = float(value)
    if dict(inputs.mechanical_power_inputs_pu) != base_powers:
        raise DynamicDerivativeAssemblyError("component inputs and base mechanical-power snapshot must agree exactly")
    if machine_connection_states is not None:
        if set(machine_connection_states) != machine_ids or any(type(value) is not bool for value in machine_connection_states.values()):
            raise DynamicDerivativeAssemblyError("machine connection states must be explicit booleans for every machine")

    time = float(inputs.simulation_time_s)
    voltages: dict[str, complex] = {}
    for bus_id, value in bus_voltages.items():
        if not isinstance(bus_id, str) or not bus_id.strip():
            raise DynamicDerivativeAssemblyError("bus-voltage keys must be stable bus identities")
        voltage = complex(value)
        if not np.isfinite(voltage.real) or not np.isfinite(voltage.imag):
            raise DynamicDerivativeAssemblyError(f"bus voltage for {bus_id!r} must be finite")
        voltages[bus_id] = voltage

    bindings = tuple(bindings)
    for binding in bindings:
        if not isinstance(binding, DynamicComponentBinding):
            raise DynamicDerivativeAssemblyError("bindings must contain DynamicComponentBinding values")
        if binding.machine_id is not None:
            if binding.machine_id not in machine_ids:
                raise DynamicDerivativeAssemblyError(
                    f"component {binding.component_id!r} references unknown machine {binding.machine_id!r}"
                )
            if machine_bus_ids[binding.machine_id] != binding.bus_id:
                raise DynamicDerivativeAssemblyError(
                    f"component {binding.component_id!r} bus identity does not match its canonical machine"
                )
        if binding.bus_id not in voltages:
            raise DynamicDerivativeAssemblyError(
                f"component {binding.component_id!r} has no solved voltage for bus {binding.bus_id!r}"
            )
        if binding.terminal_id not in inputs.terminal_voltages:
            raise DynamicDerivativeAssemblyError(
                f"component {binding.component_id!r} has no voltage for terminal {binding.terminal_id!r}"
            )
        if not np.isclose(
            inputs.terminal_voltages[binding.terminal_id],
            voltages[binding.bus_id],
            rtol=1e-9,
            atol=1e-12,
        ):
            raise DynamicDerivativeAssemblyError(
                f"component {binding.component_id!r} terminal voltage is inconsistent with its solved bus voltage"
            )

    global_state = layout.pack(machine_vector, component_states)
    component_result = evaluate_dynamic_components(
        components=components,
        bindings=bindings,
        layout=layout,
        global_state=global_state,
        inputs=inputs,
    )
    effective_powers = dict(base_powers)
    effective_powers.update(component_result.mechanical_power_outputs_pu)

    try:
        machine_derivatives = np.asarray(
            machine_derivative_evaluator(
                state=machine_vector.copy(),
                terminal_voltages=MappingProxyType(voltages),
                mechanical_powers=MappingProxyType(effective_powers),
                machine_connection_states=machine_connection_states,
                time=time,
            ),
            dtype=float,
        )
    except Exception as exc:
        raise DynamicDerivativeAssemblyError(
            f"existing machine derivative evaluator failed at t={time:.12g} s"
        ) from exc

    if machine_derivatives.ndim != 1 or machine_derivatives.size != layout.machine_state_size:
        raise DynamicDerivativeAssemblyError("machine derivative vector does not match the machine state segment")
    if not np.all(np.isfinite(machine_derivatives)):
        raise DynamicDerivativeAssemblyError("machine derivative vector contains non-finite values")

    assembled = layout.assemble_derivatives(
        machine_derivatives,
        component_result.component_derivatives,
    )
    if assembled.shape != (layout.state_size,):
        raise DynamicDerivativeAssemblyError("assembled derivative vector does not match the composed global layout")
    return DynamicSystemDerivativeAssembly(
        state_derivatives=assembled,
        mechanical_powers_pu=effective_powers,
        component_evaluation=component_result,
    )


__all__ = [
    "DynamicDerivativeAssemblyError",
    "DynamicSystemDerivativeAssembly",
    "MachineDerivativeEvaluator",
    "evaluate_composed_dynamic_derivatives",
]
