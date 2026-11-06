# ============================================================
# File: core/solver/dynamics/transient_stability.py
# GridForge V2 — Transient Stability Solver
# Author: Subhendu Mishra
# ============================================================
"""Solver-level transient-stability simulation over the canonical DAE API."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Callable, Mapping

import numpy as np

from .dae_solver import AlgebraicNetworkSolution, DAESolution, DAESolver
from .events import EventExecution, EventManager


class TransientStabilityError(RuntimeError):
    """Raised when a transient-stability simulation cannot proceed."""

    def __init__(self, message: str, *, partial_result: "TransientStabilityResult | None" = None) -> None:
        super().__init__(message)
        self.partial_result = partial_result


@dataclass(frozen=True, slots=True)
class TransientDiscontinuity:
    """Immutable pre/post electrical sides of one effective event batch."""

    time: float
    event_ids: tuple[str, ...]
    event_executions: tuple[EventExecution, ...]
    pre_state: np.ndarray
    pre_terminal_voltages: Mapping[str, complex]
    pre_electrical_powers: Mapping[str, tuple[float, float]]
    pre_algebraic_quality: AlgebraicNetworkSolution | None
    pre_topology_revision: int | None
    post_state: np.ndarray | None
    post_terminal_voltages: Mapping[str, complex] | None
    post_electrical_powers: Mapping[str, tuple[float, float]] | None
    post_algebraic_quality: AlgebraicNetworkSolution | None
    post_topology_revision: int | None
    post_status: str

    def __post_init__(self) -> None:
        time = float(self.time)
        if not math.isfinite(time) or time < 0.0:
            raise ValueError("Discontinuity time must be finite and non-negative.")
        executions = tuple(self.event_executions)
        if not executions or any(not isinstance(item, EventExecution) for item in executions):
            raise TypeError("A discontinuity must retain its non-empty canonical event execution batch.")
        ids = tuple(str(value) for value in self.event_ids)
        if ids != tuple(item.event_id for item in executions):
            raise ValueError("Discontinuity event IDs must match ordered event executions.")
        if self.post_status not in {"succeeded", "dispatch_failed", "post_evaluation_failed"}:
            raise ValueError("Unsupported discontinuity post-state status.")
        pre_state = np.asarray(self.pre_state, dtype=float).copy()
        if pre_state.ndim != 1 or not np.all(np.isfinite(pre_state)):
            raise ValueError("Discontinuity pre-state must be a finite vector.")
        post_state = None if self.post_state is None else np.asarray(self.post_state, dtype=float).copy()
        if post_state is not None and (post_state.ndim != 1 or not np.all(np.isfinite(post_state))):
            raise ValueError("Discontinuity post-state must be a finite vector.")
        pre_quality = self.pre_algebraic_quality
        if pre_quality is not None and not isinstance(pre_quality, AlgebraicNetworkSolution):
            raise TypeError("Pre-event algebraic quality must be canonical.")
        post_quality = self.post_algebraic_quality
        if post_quality is not None and not isinstance(post_quality, AlgebraicNetworkSolution):
            raise TypeError("Post-event algebraic quality must be canonical.")
        if self.post_status == "succeeded":
            if post_state is None or self.post_terminal_voltages is None or self.post_electrical_powers is None or post_quality is None:
                raise ValueError("Successful discontinuities require a complete post-event DAE solution.")
        elif post_state is not None or self.post_terminal_voltages is not None or self.post_electrical_powers is not None or post_quality is not None:
            raise ValueError("Failed discontinuities cannot claim a post-event DAE solution.")
        for value in (pre_state, post_state):
            if value is not None:
                value.setflags(write=False)
        object.__setattr__(self, "time", time)
        object.__setattr__(self, "event_ids", ids)
        object.__setattr__(self, "event_executions", executions)
        object.__setattr__(self, "pre_state", pre_state)
        object.__setattr__(self, "pre_terminal_voltages", MappingProxyType({str(k): complex(v) for k, v in self.pre_terminal_voltages.items()}))
        object.__setattr__(self, "pre_electrical_powers", MappingProxyType({str(k): (float(v[0]), float(v[1])) for k, v in self.pre_electrical_powers.items()}))
        object.__setattr__(self, "post_state", post_state)
        if self.post_terminal_voltages is not None:
            object.__setattr__(self, "post_terminal_voltages", MappingProxyType({str(k): complex(v) for k, v in self.post_terminal_voltages.items()}))
        if self.post_electrical_powers is not None:
            object.__setattr__(self, "post_electrical_powers", MappingProxyType({str(k): (float(v[0]), float(v[1])) for k, v in self.post_electrical_powers.items()}))
        if post_state is not None and post_state.shape != pre_state.shape:
            raise ValueError("Pre/post dynamic states must have identical shapes.")
        if pre_quality is not None and (
            pre_quality.topology_revision != self.pre_topology_revision
            or pre_quality.simulation_time is None
            or not math.isclose(pre_quality.simulation_time, time, rel_tol=0.0, abs_tol=1e-12)
        ):
            raise ValueError("Pre-event algebraic quality must match its boundary time and topology revision.")
        if post_quality is not None and (
            post_quality.topology_revision != self.post_topology_revision
            or post_quality.simulation_time is None
            or not math.isclose(post_quality.simulation_time, time, rel_tol=0.0, abs_tol=1e-12)
        ):
            raise ValueError("Post-event algebraic quality must match its boundary time and topology revision.")
        for name, revision in (("pre", self.pre_topology_revision), ("post", self.post_topology_revision)):
            if revision is not None and (
                isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
            ):
                raise ValueError(f"{name.title()}-event topology revision must be a non-negative integer.")


@dataclass(frozen=True, slots=True)
class TransientStabilityResult:
    """Immutable coherent time/state/output samples."""

    time: np.ndarray
    states: np.ndarray
    terminal_voltages: tuple[Mapping[str, complex], ...] = ()
    electrical_powers: tuple[Mapping[str, tuple[float, float]], ...] = ()
    events: tuple[EventExecution, ...] = ()
    provenance: Mapping[str, object] = field(default_factory=dict)
    algebraic_quality: tuple[AlgebraicNetworkSolution, ...] = ()
    discontinuities: tuple[TransientDiscontinuity, ...] = ()

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
        if self.algebraic_quality and len(self.algebraic_quality) != time.shape[0]:
            raise ValueError("Every recorded time sample must have one algebraic quality record.")
        if self.algebraic_quality:
            if any(not isinstance(item, AlgebraicNetworkSolution) for item in self.algebraic_quality):
                raise TypeError("Transient algebraic quality samples must be canonical AlgebraicNetworkSolution records.")
            for sample_time, quality in zip(time, self.algebraic_quality):
                if quality.simulation_time is None or not np.isclose(
                    quality.simulation_time, sample_time, rtol=0.0, atol=1e-12
                ):
                    raise ValueError("Algebraic quality metadata must match its recorded simulation-time sample.")
        if not np.all(np.isfinite(time)) or not np.all(np.isfinite(states)):
            raise ValueError("Transient result contains non-finite time/state values.")
        if time.size and np.any(np.diff(time) <= 0.0):
            raise ValueError("Transient result time samples must be strictly increasing.")
        if any(not isinstance(sample, Mapping) for sample in self.terminal_voltages):
            raise TypeError("Transient terminal-voltage samples must be mappings.")
        if any(not isinstance(sample, Mapping) for sample in self.electrical_powers):
            raise TypeError("Transient electrical-power samples must be mappings.")
        discontinuities = tuple(self.discontinuities)
        if any(not isinstance(item, TransientDiscontinuity) for item in discontinuities):
            raise TypeError("Transient discontinuities must be canonical TransientDiscontinuity records.")
        if any(any(np.isclose(item.time, sample, rtol=0.0, atol=1e-12) for sample in time) is False for item in discontinuities):
            raise ValueError("Every discontinuity must correspond to a base-trajectory boundary time.")
        events = tuple(self.events)
        if any(not isinstance(event, EventExecution) for event in events):
            raise TypeError("Transient result events must contain canonical EventExecution records.")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("Transient result provenance must be a mapping.")
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
        object.__setattr__(self, "algebraic_quality", tuple(self.algebraic_quality))
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "discontinuities", discontinuities)
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

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
        provenance: Mapping[str, object] | None = None,
    ) -> None:
        if not isinstance(dae_solver, DAESolver):
            raise TypeError("dae_solver must be DAESolver instance.")
        self.dae_solver = dae_solver
        self.start_time = float(start_time)
        self.end_time = float(end_time)
        self.dt = float(dt if dt is not None else getattr(dae_solver.integrator, "dt", 0.01))
        self.event_manager = event_manager or EventManager()
        runtime_owner = getattr(dae_solver.network_solver, "__self__", None)
        runtime_provenance = getattr(runtime_owner, "provenance", {})
        self.provenance = dict(provenance if provenance is not None else runtime_provenance)
        if not all(math.isfinite(value) for value in (self.start_time, self.end_time, self.dt)):
            raise ValueError("start_time, end_time, and dt must be finite.")
        if self.start_time < 0.0:
            raise ValueError("start_time cannot be negative.")
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time.")
        if self.dt <= 0.0:
            raise ValueError("dt must be greater than zero.")
        self._state: np.ndarray | None = None
        self._time = self.start_time
        self._last_step_discontinuity: TransientDiscontinuity | None = None
        self._last_due_discontinuity: TransientDiscontinuity | None = None
        self._last_due_post_solution: DAESolution | None = None

    @property
    def initialized(self) -> bool:
        return self._state is not None

    @property
    def time(self) -> float:
        return self._time

    @property
    def last_step_discontinuity(self) -> TransientDiscontinuity | None:
        return self._last_step_discontinuity

    def initialize(self, state: np.ndarray, *, time: float | None = None) -> np.ndarray:
        """Initialize from one detached dynamic state vector."""
        candidate = self.dae_solver.machine_system.validate_global_state(np.asarray(state, dtype=float)).copy()
        self._time = self.start_time if time is None else float(time)
        if not math.isfinite(self._time):
            raise ValueError("initial time must be finite.")
        if self._time < self.start_time or self._time > self.end_time:
            raise ValueError("initial time must lie within [start_time, end_time].")
        self.dae_solver.evaluate(candidate, self._time)
        self._state = candidate
        self._last_step_discontinuity = None
        self._last_due_discontinuity = None
        self._last_due_post_solution = None
        return candidate.copy()

    def run(
        self,
        *,
        record_initial: bool = True,
        should_cancel: Callable[[], bool] | None = None,
    ) -> TransientStabilityResult:
        if self._state is None:
            raise TransientStabilityError("Transient-stability solver must be initialized before run().")

        times: list[float] = []
        states: list[np.ndarray] = []
        voltages: list[dict[str, complex]] = []
        powers: list[dict[str, tuple[float, float]]] = []
        algebraic_quality: list[AlgebraicNetworkSolution] = []
        executions: list[EventExecution] = []
        discontinuities: list[TransientDiscontinuity] = []

        if not (should_cancel is not None and should_cancel()):
            try:
                due_executions = self._process_due_events()
            except TransientStabilityError as exc:
                boundary = self._last_due_discontinuity
                if boundary is not None:
                    discontinuities.append(boundary)
                    executions.extend(boundary.event_executions)
                    self._append_discontinuity_pre_sample(boundary, times, states, voltages, powers, algebraic_quality)
                    partial = self._build_result(times, states, voltages, powers, algebraic_quality, executions, discontinuities)
                    raise TransientStabilityError(str(exc), partial_result=partial) from exc
                raise
            executions.extend(due_executions)
            if self._last_due_discontinuity is not None:
                discontinuities.append(self._last_due_discontinuity)
            if record_initial or self._last_due_post_solution is not None:
                initial = self._last_due_post_solution or self.dae_solver.evaluate(self._state, self._time)
                self._record_boundary_solution(initial, times, states, voltages, powers, algebraic_quality)

        while self._time < self.end_time - self.event_manager.tolerance:
            if should_cancel is not None and should_cancel():
                break
            # Dispatch events due at the current simulation time before asking
            # the scheduler for a strictly-future event boundary. This is
            # independent of whether the caller records an initial sample.
            try:
                due_executions = self._process_due_events()
            except TransientStabilityError as exc:
                boundary = self._last_due_discontinuity
                if boundary is not None:
                    discontinuities.append(boundary)
                    executions.extend(boundary.event_executions)
                    self._append_discontinuity_pre_sample(boundary, times, states, voltages, powers, algebraic_quality)
                    partial = self._build_result(times, states, voltages, powers, algebraic_quality, executions, discontinuities)
                    raise TransientStabilityError(str(exc), partial_result=partial) from exc
                raise
            executions.extend(due_executions)
            if self._last_due_discontinuity is not None:
                discontinuities.append(self._last_due_discontinuity)
                if self._last_due_post_solution is not None:
                    self._record_boundary_solution(self._last_due_post_solution, times, states, voltages, powers, algebraic_quality)
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

            if not math.isfinite(float(solution.time)):
                raise TransientStabilityError("Integrator returned a non-finite simulation time.")
            if solution.time <= previous_time:
                raise TransientStabilityError("Simulation time failed to advance.")
            if solution.time > target_time + self.event_manager.tolerance:
                raise TransientStabilityError(
                    "Integrator advanced beyond the requested event or end-time boundary."
                )
            self._state = solution.state.copy()
            self._time = float(solution.time)

            if event_boundary and next_event_time is not None and abs(self._time - next_event_time) <= self.event_manager.tolerance:
                pre_event_solution = solution
                history_start = len(self.event_manager.history)
                try:
                    event_batch = self.event_manager.process_interval(previous_time, self._time)
                except Exception as exc:
                    attempted = self.event_manager.history[history_start:]
                    executions.extend(attempted)
                    if attempted:
                        discontinuities.append(self._make_discontinuity(pre_event_solution, attempted, None, post_status="dispatch_failed"))
                    if not times or not math.isclose(times[-1], pre_event_solution.time, rel_tol=0.0, abs_tol=1e-12):
                        self._record(pre_event_solution, times, states, voltages, powers, algebraic_quality)
                    partial = self._build_result(times, states, voltages, powers, algebraic_quality, executions, discontinuities)
                    raise TransientStabilityError(
                        f"Event dispatch failed at t={self._time:.12g} s; partial trajectory retained.",
                        partial_result=partial,
                    ) from exc
                try:
                    solution = self.dae_solver.evaluate(self._state, self._time)
                except Exception as exc:
                    annotated = self.event_manager.annotate_post_event_re_evaluation(event_batch, succeeded=False, failure=exc)
                    executions.extend(annotated)
                    if event_batch:
                        discontinuities.append(self._make_discontinuity(pre_event_solution, annotated, None, post_status="post_evaluation_failed"))
                    if not times or not math.isclose(times[-1], pre_event_solution.time, rel_tol=0.0, abs_tol=1e-12):
                        self._record(pre_event_solution, times, states, voltages, powers, algebraic_quality)
                    partial = self._build_result(times, states, voltages, powers, algebraic_quality, executions, discontinuities)
                    raise TransientStabilityError(
                        f"Post-event algebraic re-evaluation failed at t={self._time:.12g} s; partial trajectory retained.",
                        partial_result=partial,
                    ) from exc
                if event_batch:
                    annotated = self.event_manager.annotate_post_event_re_evaluation(event_batch, succeeded=True)
                    executions.extend(annotated)
                    discontinuities.append(self._make_discontinuity(pre_event_solution, annotated, solution, post_status="succeeded"))

            self._record(solution, times, states, voltages, powers, algebraic_quality)

        cancelled = should_cancel is not None and should_cancel()
        if not cancelled and self._time < self.end_time - self.event_manager.tolerance:
            raise TransientStabilityError(
                f"Simulation ended at t={self._time:.12g} s before configured "
                f"end_time={self.end_time:.12g} s."
            )

        result_provenance = dict(self.provenance)
        runtime_owner = getattr(self.dae_solver.network_solver, "__self__", None)
        runtime_event_state = getattr(runtime_owner, "event_state", None)
        if runtime_event_state is not None:
            result_provenance["final_detached_network_revision"] = runtime_event_state.topology_revision
            result_provenance["final_switch_states"] = dict(runtime_event_state.breaker_states)
            result_provenance["switch_availability_states"] = dict(runtime_event_state.snapshot.switch_available_states)

        return self._build_result(times, states, voltages, powers, algebraic_quality, executions, discontinuities, provenance=result_provenance)

    def step(self, dt: float | None = None) -> DAESolution:
        if self._state is None:
            raise TransientStabilityError("Transient-stability solver must be initialized before step().")
        # Validate the public step request before clearing prior step evidence
        # or performing event/end-time boundary arithmetic. A non-finite value
        # must not be made to look valid by clipping it to the remaining horizon.
        try:
            requested_dt = self.dt if dt is None else float(dt)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("dt must be a finite number greater than zero.") from exc
        if not math.isfinite(requested_dt) or requested_dt <= 0.0:
            raise ValueError("dt must be a finite number greater than zero.")
        self._last_step_discontinuity = None
        if self._time >= self.end_time - self.event_manager.tolerance:
            raise TransientStabilityError("Simulation has reached end_time.")
        # Keep incremental stepping consistent with run(): dispatch events
        # already due (including the current-time tolerance window) first.
        try:
            self._process_due_events()
        except TransientStabilityError:
            self._last_step_discontinuity = self._last_due_discontinuity
            raise
        self._last_step_discontinuity = self._last_due_discontinuity
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
            pre_event_solution = solution
            history_start = len(self.event_manager.history)
            try:
                event_batch = self.event_manager.process_interval(previous_time, self._time)
            except Exception as exc:
                attempted = self.event_manager.history[history_start:]
                if attempted:
                    self._last_step_discontinuity = self._make_discontinuity(pre_event_solution, attempted, None, post_status="dispatch_failed")
                raise TransientStabilityError(
                    f"Event dispatch failed at t={self._time:.12g} s; inspect last_step_discontinuity."
                ) from exc
            try:
                solution = self.dae_solver.evaluate(self._state, self._time)
            except Exception as exc:
                annotated = self.event_manager.annotate_post_event_re_evaluation(event_batch, succeeded=False, failure=exc)
                if event_batch:
                    self._last_step_discontinuity = self._make_discontinuity(pre_event_solution, annotated, None, post_status="post_evaluation_failed")
                raise TransientStabilityError(f"Post-event algebraic re-evaluation failed at t={self._time:.12g} s.") from exc
            if event_batch:
                annotated = self.event_manager.annotate_post_event_re_evaluation(event_batch, succeeded=True)
                self._last_step_discontinuity = self._make_discontinuity(pre_event_solution, annotated, solution, post_status="succeeded")
        return solution

    def _process_due_events(self) -> tuple[EventExecution, ...]:
        """Dispatch events due at the current time and refresh algebraic state.

        EventManager.process() owns the tolerance policy: events at or before
        current_time + tolerance are due. Calling it before future-boundary
        lookup prevents an event at the initial/current time from being skipped.
        """
        if self._state is None:
            return ()
        self._last_due_discontinuity = None
        self._last_due_post_solution = None
        due = tuple(event for event in self.event_manager.pending_events if event.time <= self._time + self.event_manager.tolerance)
        if not due:
            return ()
        pre_solution = self.dae_solver.evaluate(self._state, self._time)
        history_start = len(self.event_manager.history)
        try:
            executions = self.event_manager.process(self._time)
        except Exception as exc:
            attempted = self.event_manager.history[history_start:]
            if attempted:
                self._last_due_discontinuity = self._make_discontinuity(pre_solution, attempted, None, post_status="dispatch_failed")
            raise TransientStabilityError(f"Current-time event dispatch failed at t={self._time:.12g} s.") from exc
        try:
            post_solution = self.dae_solver.evaluate(self._state, self._time)
        except Exception as exc:
            annotated = self.event_manager.annotate_post_event_re_evaluation(executions, succeeded=False, failure=exc)
            if annotated:
                self._last_due_discontinuity = self._make_discontinuity(pre_solution, annotated, None, post_status="post_evaluation_failed")
            raise TransientStabilityError(f"Current-time post-event algebraic re-evaluation failed at t={self._time:.12g} s.") from exc
        annotated = self.event_manager.annotate_post_event_re_evaluation(executions, succeeded=True)
        if annotated:
            self._last_due_discontinuity = self._make_discontinuity(pre_solution, annotated, post_solution, post_status="succeeded")
            self._last_due_post_solution = post_solution
        return annotated

    def reset(self) -> None:
        self._state = None
        self._time = self.start_time
        self._last_step_discontinuity = None
        self._last_due_discontinuity = None
        self._last_due_post_solution = None
        self.event_manager.reset()

    def _append_discontinuity_pre_sample(
        self,
        boundary: TransientDiscontinuity,
        times: list[float],
        states: list[np.ndarray],
        voltages: list[dict[str, complex]],
        powers: list[dict[str, tuple[float, float]]],
        algebraic_quality: list[AlgebraicNetworkSolution],
    ) -> None:
        if times and math.isclose(times[-1], boundary.time, rel_tol=0.0, abs_tol=1e-12):
            return
        times.append(boundary.time)
        states.append(boundary.pre_state.copy())
        voltages.append(dict(boundary.pre_terminal_voltages))
        powers.append(dict(boundary.pre_electrical_powers))
        algebraic_quality.append(boundary.pre_algebraic_quality)

    def _record_boundary_solution(
        self,
        solution: DAESolution,
        times: list[float],
        states: list[np.ndarray],
        voltages: list[dict[str, complex]],
        powers: list[dict[str, tuple[float, float]]],
        algebraic_quality: list[AlgebraicNetworkSolution],
    ) -> None:
        if times and math.isclose(times[-1], solution.time, rel_tol=0.0, abs_tol=1e-12):
            states[-1] = np.asarray(solution.state, dtype=float).copy()
            voltages[-1] = dict(solution.terminal_voltages)
            powers[-1] = self._electrical_powers(solution.state, solution.terminal_voltages)
            algebraic_quality[-1] = self._algebraic_quality(solution)
            return
        self._record(solution, times, states, voltages, powers, algebraic_quality)

    def _algebraic_quality(self, solution: DAESolution) -> AlgebraicNetworkSolution:
        quality = solution.algebraic_quality
        if quality is None:
            return AlgebraicNetworkSolution(
                bus_voltages=solution.terminal_voltages,
                status="UNVERIFIED",
                accepted=True,
                quality_verified=False,
                solver_name="legacy-DAESolution",
                bus_ids=tuple(solution.terminal_voltages),
                simulation_time=solution.time,
                diagnostics=("DAESolution did not carry algebraic quality metadata.",),
            )
        return quality

    def _make_discontinuity(
        self,
        pre: DAESolution,
        executions: tuple[EventExecution, ...],
        post: DAESolution | None,
        *,
        post_status: str,
    ) -> TransientDiscontinuity:
        pre_quality = self._algebraic_quality(pre)
        post_quality = self._algebraic_quality(post) if post is not None else None
        return TransientDiscontinuity(
            time=float(pre.time),
            event_ids=tuple(item.event_id for item in executions),
            event_executions=executions,
            pre_state=pre.state,
            pre_terminal_voltages=pre.terminal_voltages,
            pre_electrical_powers=self._electrical_powers(pre.state, pre.terminal_voltages),
            pre_algebraic_quality=pre_quality,
            pre_topology_revision=pre_quality.topology_revision,
            post_state=post.state if post is not None else None,
            post_terminal_voltages=post.terminal_voltages if post is not None else None,
            post_electrical_powers=self._electrical_powers(post.state, post.terminal_voltages) if post is not None else None,
            post_algebraic_quality=post_quality,
            post_topology_revision=post_quality.topology_revision if post_quality is not None else (
                executions[-1].post_event_revision if executions else None
            ),
            post_status=post_status,
        )

    def _build_result(
        self,
        times: list[float],
        states: list[np.ndarray],
        voltages: list[dict[str, complex]],
        powers: list[dict[str, tuple[float, float]]],
        algebraic_quality: list[AlgebraicNetworkSolution],
        executions: list[EventExecution],
        discontinuities: list[TransientDiscontinuity],
        *,
        provenance: Mapping[str, object] | None = None,
    ) -> TransientStabilityResult:
        result_provenance = dict(self.provenance if provenance is None else provenance)
        runtime_owner = getattr(self.dae_solver.network_solver, "__self__", None)
        runtime_event_state = getattr(runtime_owner, "event_state", None)
        if runtime_event_state is not None:
            result_provenance["final_detached_network_revision"] = runtime_event_state.topology_revision
            result_provenance["final_switch_states"] = dict(runtime_event_state.breaker_states)
            result_provenance["switch_availability_states"] = dict(runtime_event_state.snapshot.switch_available_states)
        return TransientStabilityResult(
            time=np.asarray(times, dtype=float),
            states=np.vstack(states) if states else np.empty((0, self.dae_solver.state_size), dtype=float),
            terminal_voltages=tuple(voltages),
            electrical_powers=tuple(powers),
            events=tuple(executions),
            algebraic_quality=tuple(algebraic_quality),
            discontinuities=tuple(discontinuities),
            provenance=result_provenance,
        )

    def _record(
        self,
        solution: DAESolution,
        times: list[float],
        states: list[np.ndarray],
        voltages: list[dict[str, complex]],
        powers: list[dict[str, tuple[float, float]]],
        algebraic_quality: list[AlgebraicNetworkSolution],
    ) -> None:
        times.append(float(solution.time))
        states.append(np.asarray(solution.state, dtype=float).copy())
        voltages.append(dict(solution.terminal_voltages))
        powers.append(self._electrical_powers(solution.state, solution.terminal_voltages))
        quality = solution.algebraic_quality
        if quality is None:
            quality = AlgebraicNetworkSolution(
                bus_voltages=solution.terminal_voltages,
                status="UNVERIFIED",
                accepted=True,
                quality_verified=False,
                solver_name="legacy-DAESolution",
                bus_ids=tuple(solution.terminal_voltages),
                simulation_time=solution.time,
                diagnostics=("DAESolution did not carry algebraic quality metadata.",),
            )
        algebraic_quality.append(quality)

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
            if not self.dae_solver.is_machine_connected(machine.machine_id):
                result[str(machine.machine_id)] = (0.0, 0.0)
                continue
            output = machine.electrical_output(machine_state, voltage)
            result[str(machine.machine_id)] = (float(output.active_power), float(output.reactive_power))
        return result


__all__ = [
    "TransientStabilityError",
    "TransientStabilityResult",
    "TransientDiscontinuity",
    "TransientStabilitySolver",
]
