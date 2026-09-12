# ============================================================
# File: core/solver/dynamics/transient_stability.py
# GridForge V2 — Transient Stability Solver
# Author: Subhendu Mishra
# ============================================================
"""Solver-level transient-stability simulation over the canonical DAE API."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np

from .dae_solver import DAESolution, DAESolver
from .events import EventExecution, EventManager


class TransientStabilityError(RuntimeError):
    """Raised when a transient-stability simulation cannot proceed."""


@dataclass(frozen=True, slots=True)
class TransientStabilityResult:
    """Immutable coherent time/state/output samples."""

    time: np.ndarray
    states: np.ndarray
    terminal_voltages: tuple[Mapping[str, complex], ...] = ()
    electrical_powers: tuple[Mapping[str, tuple[float, float]], ...] = ()
    events: tuple[EventExecution, ...] = ()

    def __post_init__(self) -> None:
        time = np.asarray(self.time, dtype=float).copy()
        states = np.asarray(self.states, dtype=float).copy()
        if time.ndim != 1:
            raise ValueError("Transient result time must be one-dimensional.")
        if states.ndim != 2:
            raise ValueError("Transient result states must be two-dimensional.")
        if states.shape[0] != time.shape[0]:
            raise ValueError("Every recorded time sample must have one state sample.")
        if len(self.terminal_voltages) != time.shape[0]:
            raise ValueError("Every recorded time sample must have one terminal-voltage output sample.")
        if len(self.electrical_powers) != time.shape[0]:
            raise ValueError("Every recorded time sample must have one electrical-power output sample.")
        if not np.all(np.isfinite(time)) or not np.all(np.isfinite(states)):
            raise ValueError("Transient result contains non-finite time/state values.")
        if time.size and np.any(np.diff(time) <= 0.0):
            raise ValueError("Transient result time samples must be strictly increasing.")
        immutable_voltages = tuple(
            MappingProxyType({str(bus_id): complex(value) for bus_id, value in sample.items()})
            for sample in self.terminal_voltages
        )
        immutable_powers = tuple(
            MappingProxyType({str(machine_id): (float(values[0]), float(values[1])) for machine_id, values in sample.items()})
            for sample in self.electrical_powers
        )
        time.setflags(write=False)
        states.setflags(write=False)
        object.__setattr__(self, "time", time)
        object.__setattr__(self, "states", states)
        object.__setattr__(self, "terminal_voltages", immutable_voltages)
        object.__setattr__(self, "electrical_powers", immutable_powers)
        object.__setattr__(self, "events", tuple(self.events))

    @property
    def final_time(self) -> float:
        return float(self.time[-1]) if self.time.size else 0.0

    @property
    def final_state(self) -> np.ndarray:
        return self.states[-1].copy() if self.states.size else np.empty(0, dtype=float)

    @property
    def number_of_steps(self) -> int:
        return max(0, self.time.size - 1)

    @property
    def number_of_events(self) -> int:
        return len(self.events)


class TransientStabilitySolver:
    """Coordinate a transient-stability run using one configured DAESolver."""

    def __init__(
        self,
        dae_solver: DAESolver,
        *,
        start_time: float = 0.0,
        end_time: float = 10.0,
        dt: float | None = None,
        event_manager: EventManager | None = None,
    ) -> None:
        if not isinstance(dae_solver, DAESolver):
            raise TypeError("dae_solver must be DAESolver instance.")
        self.dae_solver = dae_solver
        self.start_time = float(start_time)
        self.end_time = float(end_time)
        self.dt = float(dt if dt is not None else getattr(dae_solver.integrator, "dt", 0.01))
        self.event_manager = event_manager or EventManager()
        if self.start_time < 0.0:
            raise ValueError("start_time cannot be negative.")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time.")
        if self.dt <= 0.0:
            raise ValueError("dt must be greater than zero.")
        self._state: np.ndarray | None = None
        self._time = self.start_time

    @property
    def initialized(self) -> bool:
        return self._state is not None

    @property
    def time(self) -> float:
        return self._time

    def initialize(self, state: np.ndarray, *, time: float | None = None) -> np.ndarray:
        """Initialize from one detached dynamic state vector."""
        candidate = self.dae_solver.machine_system.validate_global_state(np.asarray(state, dtype=float)).copy()
        self._time = self.start_time if time is None else float(time)
        if self._time < 0.0:
            raise ValueError("initial time cannot be negative.")
        self.dae_solver.evaluate(candidate, self._time)
        self._state = candidate
        return candidate.copy()

    def run(self, *, record_initial: bool = True) -> TransientStabilityResult:
        if self._state is None:
            raise TransientStabilityError("Transient-stability solver must be initialized before run().")

        times: list[float] = []
        states: list[np.ndarray] = []
        voltages: list[dict[str, complex]] = []
        powers: list[dict[str, tuple[float, float]]] = []
        executions: list[EventExecution] = []

        if record_initial:
            executions.extend(self.event_manager.process(self._time))
            initial = self.dae_solver.evaluate(self._state, self._time)
            self._record(initial, times, states, voltages, powers)

        while self._time < self.end_time - self.event_manager.tolerance:
            previous_time = self._time
            next_event_time = self.event_manager.next_event_time(previous_time)
            target_time = min(previous_time + self.dt, self.end_time)
            event_boundary = (
                next_event_time is not None
                and next_event_time <= target_time + self.event_manager.tolerance
                and next_event_time <= self.end_time + self.event_manager.tolerance
            )
            if event_boundary:
                target_time = min(float(next_event_time), self.end_time)
            step_dt = target_time - previous_time
            if step_dt <= self.event_manager.tolerance:
                raise TransientStabilityError("Simulation failed to advance toward the next event or end time.")

            try:
                solution = self.dae_solver.step(self._state, previous_time, step_dt)
            except Exception as exc:
                raise TransientStabilityError(
                    f"Transient-stability simulation failed at t={previous_time:.12g} s."
                ) from exc

            if solution.time <= previous_time:
                raise TransientStabilityError("Simulation time failed to advance.")
            self._state = solution.state.copy()
            self._time = float(solution.time)

            if event_boundary and next_event_time is not None and abs(self._time - next_event_time) <= self.event_manager.tolerance:
                executions.extend(self.event_manager.process_interval(previous_time, self._time))
                solution = self.dae_solver.evaluate(self._state, self._time)

            self._record(solution, times, states, voltages, powers)

        return TransientStabilityResult(
            time=np.asarray(times, dtype=float),
            states=np.vstack(states) if states else np.empty((0, self.dae_solver.state_size), dtype=float),
            terminal_voltages=tuple(voltages),
            electrical_powers=tuple(powers),
            events=tuple(executions),
        )

    def step(self, dt: float | None = None) -> DAESolution:
        if self._state is None:
            raise TransientStabilityError("Transient-stability solver must be initialized before step().")
        requested_dt = self.dt if dt is None else float(dt)
        if requested_dt <= 0.0:
            raise ValueError("dt must be greater than zero.")
        if self._time >= self.end_time - self.event_manager.tolerance:
            raise TransientStabilityError("Simulation has reached end_time.")
        previous_time = self._time
        step_end = min(previous_time + requested_dt, self.end_time)
        next_event_time = self.event_manager.next_event_time(previous_time)
        if next_event_time is not None and next_event_time <= step_end + self.event_manager.tolerance:
            step_end = min(float(next_event_time), self.end_time)
        step_dt = step_end - previous_time
        if step_dt <= self.event_manager.tolerance:
            raise TransientStabilityError("Simulation failed to advance toward the next event or end time.")
        solution = self.dae_solver.step(self._state, previous_time, step_dt)
        self._state = solution.state.copy()
        self._time = float(solution.time)
        if next_event_time is not None and abs(self._time - next_event_time) <= self.event_manager.tolerance:
            self.event_manager.process_interval(previous_time, self._time)
            solution = self.dae_solver.evaluate(self._state, self._time)
        return solution

    def reset(self) -> None:
        self._state = None
        self._time = self.start_time
        self.event_manager.reset()

    def _record(
        self,
        solution: DAESolution,
        times: list[float],
        states: list[np.ndarray],
        voltages: list[dict[str, complex]],
        powers: list[dict[str, tuple[float, float]]],
    ) -> None:
        times.append(float(solution.time))
        states.append(np.asarray(solution.state, dtype=float).copy())
        voltages.append(dict(solution.terminal_voltages))
        powers.append(self._electrical_powers(solution.state, solution.terminal_voltages))

    def _electrical_powers(
        self,
        state: np.ndarray,
        voltages: Mapping[str, complex],
    ) -> dict[str, tuple[float, float]]:
        result: dict[str, tuple[float, float]] = {}
        offset = 0
        for machine in self.dae_solver.machines:
            machine_state = state[offset: offset + machine.state_size]
            offset += machine.state_size
            voltage = voltages.get(str(machine.bus_id))
            if voltage is None:
                continue
            output = machine.electrical_output(machine_state, voltage)
            result[str(machine.machine_id)] = (float(output.active_power), float(output.reactive_power))
        return result


__all__ = [
    "TransientStabilityError",
    "TransientStabilityResult",
    "TransientStabilitySolver",
]
