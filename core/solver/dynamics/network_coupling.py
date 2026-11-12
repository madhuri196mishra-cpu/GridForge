"""Injection-aware algebraic network-stage contract for composed dynamics.

The contract is deliberately solver-neutral: Core owns the request shape and
quality/provenance validation, while the Application composition supplies the
network solver and topology snapshot. It does not implement a network equation
or claim that the existing machine-only DAESolver consumes this protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Protocol, runtime_checkable

import numpy as np

from .dae_solver import AlgebraicNetworkSolution


class DynamicNetworkCouplingError(ValueError):
    """Raised when a composed network-stage request or result is inconsistent."""


@dataclass(frozen=True, slots=True)
class DynamicNetworkSolveRequest:
    """Detached inputs for one algebraic solve within a composed DAE stage.

    Terminal currents are already aggregated by Core onto the host network's
    declared per-unit current base. Keys are (canonical terminal ID, phase ID).
    Positive current is equipment-to-network. Field voltages preserve the
    generator excitation handoff; non-generator participants do not populate it.
    """

    machine_state: np.ndarray
    simulation_time_s: float
    topology_revision: int
    terminal_current_injections_pu: Mapping[tuple[str, str], complex]
    generator_field_voltages_pu: Mapping[str, float]

    def __post_init__(self) -> None:
        try:
            state = np.asarray(self.machine_state, dtype=float).copy()
        except (TypeError, ValueError, OverflowError) as exc:
            raise DynamicNetworkCouplingError("machine_state must be a finite numeric vector") from exc
        if state.ndim != 1 or state.size == 0 or not np.all(np.isfinite(state)):
            raise DynamicNetworkCouplingError("machine_state must be a finite non-empty vector")
        if isinstance(self.simulation_time_s, bool) or not isinstance(
            self.simulation_time_s, (int, float)
        ) or not np.isfinite(float(self.simulation_time_s)) or self.simulation_time_s < 0:
            raise DynamicNetworkCouplingError("simulation_time_s must be finite and non-negative")
        if type(self.topology_revision) is not int or self.topology_revision < 0:
            raise DynamicNetworkCouplingError("topology_revision must be a non-negative integer")

        currents: dict[tuple[str, str], complex] = {}
        for key, value in self.terminal_current_injections_pu.items():
            if not isinstance(key, tuple) or len(key) != 2 or any(
                not isinstance(part, str) or not part.strip() for part in key
            ):
                raise DynamicNetworkCouplingError("current keys must be (terminal_id, phase_id) identities")
            try:
                current = complex(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise DynamicNetworkCouplingError("terminal currents must be finite complex values") from exc
            if not np.isfinite(current.real) or not np.isfinite(current.imag):
                raise DynamicNetworkCouplingError("terminal currents must be finite complex values")
            normalized = (key[0].strip(), key[1].strip())
            if normalized in currents:
                raise DynamicNetworkCouplingError("terminal current keys must be unique after normalization")
            currents[normalized] = current

        fields: dict[str, float] = {}
        for machine_id, value in self.generator_field_voltages_pu.items():
            if not isinstance(machine_id, str) or not machine_id.strip():
                raise DynamicNetworkCouplingError("generator field-voltage keys must be machine identities")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)):
                raise DynamicNetworkCouplingError("generator field voltages must be finite numeric values")
            if float(value) < 0.0:
                raise DynamicNetworkCouplingError("generator field voltages must be non-negative")
            fields[machine_id.strip()] = float(value)

        state.setflags(write=False)
        object.__setattr__(self, "machine_state", state)
        object.__setattr__(self, "simulation_time_s", float(self.simulation_time_s))
        object.__setattr__(self, "terminal_current_injections_pu", MappingProxyType(currents))
        object.__setattr__(self, "generator_field_voltages_pu", MappingProxyType(fields))


@runtime_checkable
class DynamicNetworkSolver(Protocol):
    """Application-supplied solver for injection-aware algebraic stages."""

    def solve(self, request: DynamicNetworkSolveRequest) -> AlgebraicNetworkSolution:
        """Return the quality-checked network solution for the exact request."""


def validate_dynamic_network_solution(
    request: DynamicNetworkSolveRequest,
    solution: AlgebraicNetworkSolution,
) -> AlgebraicNetworkSolution:
    """Fail closed unless network quality, time and topology match the request."""
    if not isinstance(request, DynamicNetworkSolveRequest):
        raise TypeError("request must be DynamicNetworkSolveRequest")
    if not isinstance(solution, AlgebraicNetworkSolution):
        raise TypeError("solution must be AlgebraicNetworkSolution")
    if not solution.accepted or not solution.quality_verified or solution.status != "ACCEPTED":
        raise DynamicNetworkCouplingError(
            "composed dynamics require an accepted, quality-verified algebraic network solution"
        )
    if solution.topology_revision != request.topology_revision:
        raise DynamicNetworkCouplingError(
            "network solution topology revision does not match the stage request"
        )
    if solution.simulation_time is None or not np.isclose(
        solution.simulation_time, request.simulation_time_s, rtol=0.0, atol=1e-12
    ):
        raise DynamicNetworkCouplingError(
            "network solution time does not match the composed stage request"
        )
    if not solution.bus_voltages:
        raise DynamicNetworkCouplingError("accepted network solution must contain bus voltages")
    return solution


__all__ = [
    "DynamicNetworkCouplingError",
    "DynamicNetworkSolveRequest",
    "DynamicNetworkSolver",
    "validate_dynamic_network_solution",
]
