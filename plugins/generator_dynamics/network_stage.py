"""Stage-scoped network solve contract for generator excitation handoff.

This plugin adapter gives a host network solver the exact AVR excitation
snapshot for one integrator stage. It does not own the DAE state vector,
network equations, terminal projection, or integration lifecycle.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

import numpy as np

from .excitation_handoff import GeneratorExcitationSnapshot


class GeneratorNetworkStageError(ValueError):
    """Raised when a stage request or network result is inconsistent."""


@dataclass(frozen=True, slots=True)
class GeneratorNetworkConvergenceEvidence:
    """Solver-reported convergence evidence for one algebraic network stage."""

    converged: bool
    iteration_count: int
    residual_norm: float
    tolerance: float
    solver_id: str

    def __post_init__(self) -> None:
        if type(self.converged) is not bool:
            raise GeneratorNetworkStageError("converged must be an explicit bool")
        if isinstance(self.iteration_count, bool) or not isinstance(self.iteration_count, int) or self.iteration_count < 0:
            raise GeneratorNetworkStageError("iteration_count must be a non-negative integer")
        for name, value in (("residual_norm", self.residual_norm), ("tolerance", self.tolerance)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise GeneratorNetworkStageError(f"{name} must be finite and non-negative")
        if self.tolerance <= 0:
            raise GeneratorNetworkStageError("tolerance must be greater than zero")
        if not isinstance(self.solver_id, str) or not self.solver_id.strip():
            raise GeneratorNetworkStageError("solver_id must be a non-empty identifier")
        if self.converged and self.residual_norm > self.tolerance:
            raise GeneratorNetworkStageError("converged network result exceeds its declared residual tolerance")


@dataclass(frozen=True, slots=True)
class GeneratorNetworkSolveResult:
    """Detached algebraic solution plus evidence reported by the host solver."""

    bus_voltages: Mapping[str, complex]
    convergence: GeneratorNetworkConvergenceEvidence

    def __post_init__(self) -> None:
        if not isinstance(self.bus_voltages, Mapping):
            raise GeneratorNetworkStageError("bus_voltages must be a mapping")
        if not isinstance(self.convergence, GeneratorNetworkConvergenceEvidence):
            raise TypeError("convergence must be GeneratorNetworkConvergenceEvidence")


@dataclass(frozen=True, slots=True)
class GeneratorNetworkStageRequest:
    """Detached machine state and excitation values for one network stage."""

    machine_state: np.ndarray
    excitation: GeneratorExcitationSnapshot

    def __post_init__(self) -> None:
        if not isinstance(self.excitation, GeneratorExcitationSnapshot):
            raise TypeError("excitation must be GeneratorExcitationSnapshot")
        try:
            state = np.asarray(self.machine_state, dtype=float).copy()
        except (TypeError, ValueError, OverflowError) as exc:
            raise GeneratorNetworkStageError("machine_state must be a finite numeric vector") from exc
        if state.ndim != 1 or state.size == 0 or not np.all(np.isfinite(state)):
            raise GeneratorNetworkStageError("machine_state must be a finite non-empty vector")
        state.setflags(write=False)
        object.__setattr__(self, "machine_state", state)


@dataclass(frozen=True, slots=True)
class GeneratorNetworkStageSolution:
    """Network result tagged with the exact excitation source snapshot."""

    simulation_time_s: float
    bus_voltages: Mapping[str, complex]
    field_voltages_pu: Mapping[str, float]
    component_ids_by_machine: Mapping[str, str]
    convergence: GeneratorNetworkConvergenceEvidence | None = None

    def __post_init__(self) -> None:
        time = self.simulation_time_s
        if self.convergence is not None and not isinstance(self.convergence, GeneratorNetworkConvergenceEvidence):
            raise TypeError("convergence must be GeneratorNetworkConvergenceEvidence when supplied")
        if isinstance(time, bool) or not isinstance(time, (int, float)) or not math.isfinite(float(time)) or time < 0:
            raise GeneratorNetworkStageError("simulation_time_s must be finite and non-negative")
        buses: dict[str, complex] = {}
        for bus_id, value in self.bus_voltages.items():
            if not isinstance(bus_id, str) or not bus_id.strip():
                raise GeneratorNetworkStageError("bus-voltage keys must be stable bus identities")
            try:
                voltage = complex(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise GeneratorNetworkStageError(f"invalid voltage for bus {bus_id!r}") from exc
            if not math.isfinite(voltage.real) or not math.isfinite(voltage.imag):
                raise GeneratorNetworkStageError(f"non-finite voltage for bus {bus_id!r}")
            buses[bus_id] = voltage
        fields = dict(self.field_voltages_pu)
        sources = dict(self.component_ids_by_machine)
        if not buses:
            raise GeneratorNetworkStageError("network stage must return at least one bus voltage")
        if not fields or set(fields) != set(sources):
            raise GeneratorNetworkStageError("field-voltage and AVR source identities must match and be non-empty")
        for machine_id, value in fields.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise GeneratorNetworkStageError("field-voltage keys must be machine identities")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
                raise GeneratorNetworkStageError(f"invalid field voltage for machine {machine_id!r}")
            source = sources[machine_id]
            if not isinstance(source, str) or not source.strip():
                raise GeneratorNetworkStageError(f"invalid AVR source for machine {machine_id!r}")
            fields[machine_id] = float(value)
        object.__setattr__(self, "simulation_time_s", float(time))
        object.__setattr__(self, "bus_voltages", MappingProxyType(buses))
        object.__setattr__(self, "field_voltages_pu", MappingProxyType(fields))
        object.__setattr__(self, "component_ids_by_machine", MappingProxyType(sources))


GeneratorNetworkSolver = Callable[
    [np.ndarray, float, Mapping[str, float]],
    Mapping[str, complex] | GeneratorNetworkSolveResult,
]


def solve_generator_network_stage(
    request: GeneratorNetworkStageRequest,
    network_solver: GeneratorNetworkSolver,
) -> GeneratorNetworkStageSolution:
    """Solve one network stage with the request's exact AVR excitation map.

    The callback receives a detached machine-state vector, the integrator's
    stage time, and the immutable machine-ID-to-field-voltage mapping. The
    returned solution retains the same excitation/source provenance so the
    host can pass that identical snapshot to machine electrical equations.
    """
    if not isinstance(request, GeneratorNetworkStageRequest):
        raise TypeError("request must be GeneratorNetworkStageRequest")
    if not callable(network_solver):
        raise TypeError("network_solver must be callable")
    snapshot = request.excitation
    try:
        raw = network_solver(
            request.machine_state.copy(),
            snapshot.simulation_time_s,
            snapshot.field_voltages_pu,
        )
    except Exception as exc:
        raise GeneratorNetworkStageError(
            f"generator network solve failed at t={snapshot.simulation_time_s:.12g} s"
        ) from exc
    if isinstance(raw, GeneratorNetworkSolveResult):
        bus_voltages = raw.bus_voltages
        convergence = raw.convergence
    elif isinstance(raw, Mapping):
        bus_voltages = raw
        convergence = None
    else:
        raise GeneratorNetworkStageError("network_solver must return a bus-voltage mapping or GeneratorNetworkSolveResult")
    if convergence is not None and not convergence.converged:
        raise GeneratorNetworkStageError(
            f"network solver {convergence.solver_id!r} reported non-convergence "
            f"after {convergence.iteration_count} iterations (residual={convergence.residual_norm:.6g})"
        )
    return GeneratorNetworkStageSolution(
        simulation_time_s=snapshot.simulation_time_s,
        bus_voltages=bus_voltages,
        convergence=convergence,
        field_voltages_pu=snapshot.field_voltages_pu,
        component_ids_by_machine=snapshot.component_ids_by_machine,
    )


__all__ = [
    "GeneratorNetworkStageError",
    "GeneratorNetworkStageRequest",
    "GeneratorNetworkStageSolution",
    "GeneratorNetworkSolver",
    "solve_generator_network_stage",
]
