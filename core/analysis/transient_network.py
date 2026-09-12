"""Detached transient-network state and machine/network algebraic coupling."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np

from core.analysis.dynamic_initial_state import (
    DynamicInitialStatePreparation,
    DynamicMachineModelDefinition,
)
from core.analysis.dynamic_model_association import DynamicMachineModelRegistry
from core.analysis.power_flow_preparation import (
    PreparedBranch,
    PreparedPowerFlow,
    PreparedShunt,
    PreparedTransformer,
)
from core.numerical.ybus import YBusBuilder
from core.solver.dynamics.machine_models import ClassicalSynchronousMachine
from core.solver.dynamics.multimachine import MultiMachineSystem
from core.solver.power_flow.result import PowerFlowResult


@dataclass(frozen=True, slots=True)
class DetachedTransientNetworkState:
    """Immutable passive/network switching snapshot owned by one study run."""

    bus_ids: tuple[str, ...]
    branches: tuple[PreparedBranch, ...] = ()
    transformers: tuple[PreparedTransformer, ...] = ()
    shunts: tuple[PreparedShunt, ...] = ()
    breaker_states: Mapping[str, bool] = MappingProxyType({})
    equipment_states: Mapping[str, bool] = MappingProxyType({})
    active_fault: object | None = None
    topology_revision: int | None = None

    def __post_init__(self) -> None:
        bus_ids = tuple(str(bus_id) for bus_id in self.bus_ids)
        if not bus_ids or len(set(bus_ids)) != len(bus_ids):
            raise ValueError("Transient network requires unique bus IDs.")
        object.__setattr__(self, "bus_ids", bus_ids)
        object.__setattr__(self, "branches", tuple(self.branches))
        object.__setattr__(self, "transformers", tuple(self.transformers))
        object.__setattr__(self, "shunts", tuple(self.shunts))
        object.__setattr__(self, "breaker_states", MappingProxyType({str(k): bool(v) for k, v in self.breaker_states.items()}))
        object.__setattr__(self, "equipment_states", MappingProxyType({str(k): bool(v) for k, v in self.equipment_states.items()}))

    def is_element_conducting(self, element_id: str, default: bool = True) -> bool:
        return self.equipment_states.get(str(element_id), default)


@dataclass(frozen=True, slots=True)
class PreparedTransientStability:
    """Complete detached input bundle for one transient-stability execution."""

    network: DetachedTransientNetworkState
    machines: tuple[ClassicalSynchronousMachine, ...]
    mechanical_powers: Mapping[str, float]
    initial_state: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "machines", tuple(self.machines))
        object.__setattr__(self, "mechanical_powers", MappingProxyType({str(k): float(v) for k, v in self.mechanical_powers.items()}))
        object.__setattr__(self, "initial_state", tuple(float(v) for v in self.initial_state))

    @classmethod
    def from_power_flow(
        cls,
        prepared_power_flow: PreparedPowerFlow,
        power_flow_result: PowerFlowResult,
        dynamic_models: DynamicMachineModelRegistry,
    ) -> "PreparedTransientStability":
        if not isinstance(prepared_power_flow, PreparedPowerFlow):
            raise TypeError("prepared_power_flow must be PreparedPowerFlow.")
        if not isinstance(power_flow_result, PowerFlowResult):
            raise TypeError("power_flow_result must be PowerFlowResult.")
        if not isinstance(dynamic_models, DynamicMachineModelRegistry):
            raise TypeError("dynamic_models must be DynamicMachineModelRegistry.")

        definitions: list[DynamicMachineModelDefinition] = []
        for association in dynamic_models.all():
            definitions.append(
                DynamicMachineModelDefinition(
                    machine_id=association.machine_id,
                    bus_id=association.bus_id,
                    parameters=association.parameters,
                    mechanical_power=association.mechanical_power,
                )
            )

        machines: list[ClassicalSynchronousMachine] = []
        initial_states: list[float] = []
        mechanical: dict[str, float] = {}
        for definition in definitions:
            prepared_initial = DynamicInitialStatePreparation.prepare(
                power_flow_result,
                prepared_power_flow.input,
                definition,
            )
            machines.append(
                ClassicalSynchronousMachine(
                    definition.machine_id,
                    definition.bus_id,
                    H=definition.parameters.H,
                    Xd_prime=definition.parameters.Xd_prime,
                    D=definition.parameters.D,
                    Efd=definition.parameters.Efd,
                    initial_delta=prepared_initial.rotor_angle,
                    initial_omega=prepared_initial.speed_deviation,
                )
            )
            initial_states.extend(prepared_initial.state_vector)
            mechanical[definition.machine_id] = definition.mechanical_power

        network = DetachedTransientNetworkState(
            bus_ids=prepared_power_flow.bus_ids,
            branches=prepared_power_flow.branches,
            transformers=prepared_power_flow.transformers,
            shunts=prepared_power_flow.shunts,
            equipment_states={
                element.branch_id: bool(element.in_service)
                for element in (*prepared_power_flow.branches, *prepared_power_flow.transformers)
            } | {
                element.shunt_id: bool(element.in_service)
                for element in prepared_power_flow.shunts
            },
            topology_revision=prepared_power_flow.topology_revision,
        )
        return cls(network, tuple(machines), mechanical, tuple(initial_states))


class _PreparedTransientPassiveView:
    def __init__(self, state: DetachedTransientNetworkState) -> None:
        self.bus_ids = state.bus_ids
        self.branches = tuple(branch for branch in state.branches if state.is_element_conducting(branch.branch_id, branch.in_service))
        self.transformers = tuple(transformer for transformer in state.transformers if state.is_element_conducting(transformer.branch_id, transformer.in_service))
        self.shunts = tuple(shunt for shunt in state.shunts if state.is_element_conducting(shunt.shunt_id, shunt.in_service))
        self.topology_revision = state.topology_revision


class TransientNetworkSolver:
    """Solve detached passive network plus classical-machine Norton injections."""

    def __init__(self, network_state: DetachedTransientNetworkState, machine_system: MultiMachineSystem) -> None:
        if not isinstance(network_state, DetachedTransientNetworkState):
            raise TypeError("network_state must be DetachedTransientNetworkState.")
        if not isinstance(machine_system, MultiMachineSystem):
            raise TypeError("machine_system must be MultiMachineSystem.")
        self.network_state = network_state
        self.machine_system = machine_system

    def solve(self, state: np.ndarray, time: float) -> dict[str, complex]:
        del time
        dynamic_state = self.machine_system.validate_global_state(np.asarray(state, dtype=float))
        passive = YBusBuilder().build(_PreparedTransientPassiveView(self.network_state))
        matrix = passive.matrix.toarray().astype(np.complex128, copy=True)
        source = np.zeros(len(passive.bus_ids), dtype=np.complex128)

        offset = 0
        for machine in self.machine_system.machines:
            local_state = dynamic_state[offset:offset + machine.state_size]
            offset += machine.state_size
            bus_id = str(machine.bus_id)
            if bus_id not in passive.bus_ids:
                raise ValueError(f"Machine '{machine.machine_id}' references unknown bus '{bus_id}'.")
            index = passive.index_of(bus_id)
            delta = float(local_state[0])
            e_internal = float(machine.parameters.Efd) * np.exp(1j * delta)
            admittance = 1.0 / (1j * float(machine.parameters.Xd_prime))
            matrix[index, index] += admittance
            source[index] += admittance * e_internal

        try:
            voltages = np.linalg.solve(matrix, source)
        except np.linalg.LinAlgError as exc:
            raise ValueError("Transient algebraic network matrix is singular.") from exc

        if not np.all(np.isfinite(voltages.real)) or not np.all(np.isfinite(voltages.imag)):
            raise ValueError("Transient algebraic network solution contains non-finite voltages.")
        return {bus_id: complex(voltages[index]) for index, bus_id in enumerate(passive.bus_ids)}


__all__ = ["DetachedTransientNetworkState", "PreparedTransientStability", "TransientNetworkSolver"]
