"""Injection-aware algebraic network-stage contract for composed dynamics.

The contract is deliberately solver-neutral: Core owns the request shape and
quality/provenance validation, while the Application composition supplies the
network solver and topology snapshot. It does not implement a network equation
or claim that the existing machine-only DAESolver consumes this protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, Protocol, Sequence, runtime_checkable

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



from .component_assembly import aggregate_dynamic_terminal_injections
from .component_contract import DynamicTerminalInjection


@dataclass(frozen=True, slots=True)
class DynamicNetworkStageResult:
    """Converged network/current fixed-point evidence for one trial stage."""

    request: DynamicNetworkSolveRequest
    solution: AlgebraicNetworkSolution
    terminal_injections: tuple[DynamicTerminalInjection, ...]
    aggregated_terminal_currents_pu: Mapping[tuple[str, str], complex]
    iteration_count: int
    injection_residual_norm: float
    scaled_injection_residual: float

    def __post_init__(self) -> None:
        if not isinstance(self.request, DynamicNetworkSolveRequest):
            raise TypeError("request must be DynamicNetworkSolveRequest")
        validate_dynamic_network_solution(self.request, self.solution)
        injections = tuple(self.terminal_injections)
        if any(not isinstance(item, DynamicTerminalInjection) for item in injections):
            raise DynamicNetworkCouplingError("terminal_injections must contain typed contributions")
        currents: dict[tuple[str, str], complex] = {}
        for key, value in self.aggregated_terminal_currents_pu.items():
            if not isinstance(key, tuple) or len(key) != 2:
                raise DynamicNetworkCouplingError("aggregated current keys must be terminal/phase pairs")
            current = complex(value)
            if not np.isfinite(current.real) or not np.isfinite(current.imag):
                raise DynamicNetworkCouplingError("aggregated currents must be finite")
            currents[(str(key[0]), str(key[1]))] = current
        if type(self.iteration_count) is not int or self.iteration_count < 1:
            raise DynamicNetworkCouplingError("iteration_count must be a positive integer")
        for name, value in (
            ("injection_residual_norm", self.injection_residual_norm),
            ("scaled_injection_residual", self.scaled_injection_residual),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)) or value < 0:
                raise DynamicNetworkCouplingError(f"{name} must be finite and non-negative")
        if self.scaled_injection_residual > 1.0:
            raise DynamicNetworkCouplingError("stage result cannot be published above the injection convergence threshold")
        object.__setattr__(self, "terminal_injections", injections)
        object.__setattr__(self, "aggregated_terminal_currents_pu", MappingProxyType(currents))


TerminalInjectionEvaluator = Callable[
    [AlgebraicNetworkSolution], Sequence[DynamicTerminalInjection]
]


def solve_dynamic_network_stage(
    *,
    machine_state: np.ndarray,
    simulation_time_s: float,
    topology_revision: int,
    generator_field_voltages_pu: Mapping[str, float],
    network_solver: DynamicNetworkSolver,
    terminal_injection_evaluator: TerminalInjectionEvaluator,
    network_current_bases_a: Mapping[tuple[str, str], float],
    initial_terminal_currents_pu: Mapping[tuple[str, str], complex] | None = None,
    absolute_tolerance: float = 1e-9,
    relative_tolerance: float = 1e-7,
    max_iterations: int = 20,
) -> DynamicNetworkStageResult:
    """Iterate network voltages and device injections to a consistent stage.

    The callback evaluates every registered device against the supplied
    quality-verified network solution and returns its typed current
    contributions. It must be pure: no equipment mutation, sample publication
    or accepted-state commit is permitted. Iteration k solves with I[k], then
    computes I[k+1]. A result is returned only when their per-terminal/phase
    difference meets the absolute-plus-relative tolerance.
    """
    if not callable(getattr(network_solver, "solve", None)):
        raise TypeError("network_solver must implement solve(request)")
    if not callable(terminal_injection_evaluator):
        raise TypeError("terminal_injection_evaluator must be callable")
    if not isinstance(network_current_bases_a, Mapping):
        raise TypeError("network_current_bases_a must be a terminal/phase base mapping")
    normalized_bases: dict[tuple[str, str], float] = {}
    for key, value in network_current_bases_a.items():
        if not isinstance(key, tuple) or len(key) != 2 or any(
            not isinstance(part, str) or not part.strip() for part in key
        ):
            raise DynamicNetworkCouplingError("network current-base keys must be (terminal_id, phase_id)")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)) or value <= 0:
            raise DynamicNetworkCouplingError("network current bases must be finite and positive")
        normalized = (key[0].strip(), key[1].strip())
        if normalized in normalized_bases:
            raise DynamicNetworkCouplingError("network current-base keys must be unique after normalization")
        normalized_bases[normalized] = float(value)
    if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 1:
        raise DynamicNetworkCouplingError("max_iterations must be a positive integer")
    for name, value in (("absolute_tolerance", absolute_tolerance), ("relative_tolerance", relative_tolerance)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(float(value)) or value < 0:
            raise DynamicNetworkCouplingError(f"{name} must be finite and non-negative")
    if absolute_tolerance == 0 and relative_tolerance == 0:
        raise DynamicNetworkCouplingError("at least one injection tolerance must be positive")

    seed_request = DynamicNetworkSolveRequest(
        machine_state=machine_state,
        simulation_time_s=simulation_time_s,
        topology_revision=topology_revision,
        terminal_current_injections_pu=initial_terminal_currents_pu or {},
        generator_field_voltages_pu=generator_field_voltages_pu,
    )
    current_guess = dict(seed_request.terminal_current_injections_pu)
    missing_seed_bases = set(current_guess) - set(normalized_bases)
    if missing_seed_bases:
        raise DynamicNetworkCouplingError(
            f"initial current estimate has no network base for {sorted(missing_seed_bases)!r}"
        )

    residual_norm = float("inf")
    scaled_residual = float("inf")
    for iteration in range(1, max_iterations + 1):
        request = DynamicNetworkSolveRequest(
            machine_state=machine_state,
            simulation_time_s=simulation_time_s,
            topology_revision=topology_revision,
            terminal_current_injections_pu=current_guess,
            generator_field_voltages_pu=generator_field_voltages_pu,
        )
        try:
            solution = network_solver.solve(request)
        except Exception as exc:
            raise DynamicNetworkCouplingError(
                f"network solver failed at t={request.simulation_time_s:.12g} s, iteration {iteration}"
            ) from exc
        validate_dynamic_network_solution(request, solution)

        try:
            injections = tuple(terminal_injection_evaluator(solution))
            updated = dict(aggregate_dynamic_terminal_injections(
                injections,
                current_bases_a=normalized_bases,
            ))
        except Exception as exc:
            raise DynamicNetworkCouplingError(
                f"terminal injection evaluation failed at iteration {iteration}"
            ) from exc

        residual_norm = 0.0
        scaled_residual = 0.0
        for key in set(current_guess) | set(updated):
            previous = complex(current_guess.get(key, 0j))
            candidate = complex(updated.get(key, 0j))
            difference = abs(candidate - previous)
            allowed = float(absolute_tolerance) + float(relative_tolerance) * max(
                abs(previous), abs(candidate)
            )
            residual_norm = max(residual_norm, difference)
            ratio = difference / allowed if allowed > 0.0 else (0.0 if difference == 0.0 else float("inf"))
            scaled_residual = max(scaled_residual, ratio)

        if scaled_residual <= 1.0:
            return DynamicNetworkStageResult(
                request=request,
                solution=solution,
                terminal_injections=injections,
                aggregated_terminal_currents_pu=updated,
                iteration_count=iteration,
                injection_residual_norm=residual_norm,
                scaled_injection_residual=scaled_residual,
            )
        current_guess = updated

    raise DynamicNetworkCouplingError(
        f"network/device injection coupling did not converge in {max_iterations} iterations; "
        f"last scaled residual={scaled_residual:.6g}, absolute residual={residual_norm:.6g}"
    )


__all__ = [
    "DynamicNetworkCouplingError",
    "DynamicNetworkSolveRequest",
    "DynamicNetworkSolver",
    "validate_dynamic_network_solution",
    "DynamicNetworkStageResult",
    "TerminalInjectionEvaluator",
    "solve_dynamic_network_stage",
]
