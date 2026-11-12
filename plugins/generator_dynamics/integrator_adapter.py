"""Opt-in augmented-state integrator adapter for generator dynamics.

This adapter composes the existing Core MultiMachineSystem and Integrator
with the generator plugin's stage evaluator. It does not modify DAESolver's
legacy machine-only API or create another machine/solver authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

from core.solver.dynamics.component_assembly import DynamicComponentBinding
from core.solver.dynamics.component_contract import DynamicComponentInputs, DynamicStateComponent
from core.solver.dynamics.dynamic_state_layout import DynamicSystemStateLayout
from core.solver.dynamics.integrator import Integrator
from core.solver.dynamics.multimachine import MultiMachineSystem

from .avr_component import FirstOrderAVRStateComponent
from .network_stage import GeneratorNetworkSolver
from .stage_evaluation import (
    GeneratorDynamicStageResult,
    TerminalVoltageProjector,
    evaluate_generator_dynamic_stage,
)


class GeneratorAugmentedIntegratorError(ValueError):
    """Raised when the augmented generator integration boundary is invalid."""


MachineConnectionResolver = Callable[[str], bool]
AcceptedStepObserver = Callable[["GeneratorAugmentedStepResult"], None]


@dataclass(frozen=True, slots=True)
class GeneratorAugmentedStepResult:
    """Accepted augmented state and its final-time algebraic/derivative sample."""

    state: np.ndarray
    simulation_time_s: float
    final_stage: GeneratorDynamicStageResult
    derivative_stages: tuple[GeneratorDynamicStageResult, ...] = ()

    def __post_init__(self) -> None:
        state = np.asarray(self.state, dtype=float).copy()
        if state.ndim != 1 or state.size == 0 or not np.all(np.isfinite(state)):
            raise GeneratorAugmentedIntegratorError("accepted state must be a finite non-empty vector")
        if not np.isfinite(float(self.simulation_time_s)):
            raise GeneratorAugmentedIntegratorError("accepted simulation time must be finite")
        if self.final_stage.component_inputs.simulation_time_s != float(self.simulation_time_s):
            raise GeneratorAugmentedIntegratorError("final stage time must match accepted simulation time")
        stages = tuple(self.derivative_stages)
        if any(not isinstance(stage, GeneratorDynamicStageResult) for stage in stages):
            raise GeneratorAugmentedIntegratorError(
                "derivative_stages must contain GeneratorDynamicStageResult evidence"
            )
        # These are solver trial evaluations (RK4 stages / Newton iterates),
        # not accepted states or durable checkpoints.
        object.__setattr__(self, "derivative_stages", stages)
        state.setflags(write=False)
        object.__setattr__(self, "state", state)
        object.__setattr__(self, "simulation_time_s", float(self.simulation_time_s))

    def to_archive_payload(self) -> dict[str, object]:
        """Return a versioned JSON-compatible evidence snapshot for StudyResult.

        The payload intentionally contains primitives, lists and string-keyed
        mappings only. Core's StudyResultArchive can persist it without
        importing plugin classes or expanding its trusted dataclass allowlist.
        Derivative stages are explicitly labelled as solver evaluations, not
        accepted trajectory points; the accepted vector and final algebraic
        stage remain separate fields.
        """
        def stage_payload(stage: GeneratorDynamicStageResult) -> dict[str, object]:
            convergence = stage.network.convergence
            return {
                "simulation_time_s": stage.component_inputs.simulation_time_s,
                "excitation": {
                    "field_voltages_pu": dict(stage.excitation.field_voltages_pu),
                    "component_ids_by_machine": dict(stage.excitation.component_ids_by_machine),
                },
                "network": {
                    "bus_voltages": {
                        bus_id: {"real": voltage.real, "imag": voltage.imag}
                        for bus_id, voltage in stage.network.bus_voltages.items()
                    },
                    "convergence": None if convergence is None else {
                        "converged": convergence.converged,
                        "iteration_count": convergence.iteration_count,
                        "residual_norm": convergence.residual_norm,
                        "tolerance": convergence.tolerance,
                        "solver_id": convergence.solver_id,
                    },
                },
                "inputs": {
                    "terminal_voltages": {
                        terminal_id: {"real": voltage.real, "imag": voltage.imag}
                        for terminal_id, voltage in stage.component_inputs.terminal_voltages.items()
                    },
                    "electrical_active_power_pu": dict(stage.component_inputs.electrical_active_power_pu),
                    "mechanical_power_inputs_pu": dict(stage.component_inputs.mechanical_power_inputs_pu),
                },
                "derivatives": {
                    "state_derivatives": stage.derivative_assembly.state_derivatives.tolist(),
                    "mechanical_powers_pu": dict(stage.derivative_assembly.mechanical_powers_pu),
                    "component_derivatives": {
                        component_id: list(values)
                        for component_id, values in stage.derivative_assembly.component_evaluation.component_derivatives.items()
                    },
                    "mechanical_power_outputs_pu": dict(
                        stage.derivative_assembly.component_evaluation.mechanical_power_outputs_pu
                    ),
                },
            }

        return {
            "schema": "gridforge.generator_augmented_step",
            "schema_version": 1,
            "simulation_time_s": self.simulation_time_s,
            "accepted_state": self.state.tolist(),
            "final_stage": stage_payload(self.final_stage),
            "derivative_stage_semantics": "solver_evaluations_not_accepted_states",
            "derivative_stages": [stage_payload(stage) for stage in self.derivative_stages],
        }


class GeneratorAugmentedDAEAdapter:
    """Run augmented machine-plus-extension state through Core's Integrator.

    The caller constructs the global initial vector with
    DynamicSystemStateLayout.pack(). At every derivative callback (including
    RK4 intermediate states and trapezoidal Newton evaluations), this adapter
    performs a fresh AVR snapshot, network solve, terminal/electrical projection
    and composed derivative evaluation. It reuses the registered
    MultiMachineSystem and Integrator; DAESolver remains unchanged.
    """

    def __init__(
        self,
        *,
        machine_system: MultiMachineSystem,
        integrator: Integrator,
        layout: DynamicSystemStateLayout,
        components: Sequence[DynamicStateComponent],
        component_bindings: Sequence[DynamicComponentBinding],
        avr_components: Sequence[FirstOrderAVRStateComponent],
        network_solver: GeneratorNetworkSolver,
        terminal_voltage_projector: TerminalVoltageProjector,
        base_mechanical_powers_pu: Mapping[str, float],
        machine_connection_state: MachineConnectionResolver | None = None,
        require_network_convergence_evidence: bool = False,
    ) -> None:
        if not isinstance(machine_system, MultiMachineSystem):
            raise TypeError("machine_system must be the canonical MultiMachineSystem")
        if not isinstance(integrator, Integrator):
            raise TypeError("integrator must be the canonical Core Integrator")
        if not isinstance(layout, DynamicSystemStateLayout):
            raise TypeError("layout must be DynamicSystemStateLayout")
        if not callable(network_solver) or not callable(terminal_voltage_projector):
            raise TypeError("network solver and terminal voltage projector must be callable")
        if machine_connection_state is not None and not callable(machine_connection_state):
            raise TypeError("machine_connection_state must be callable when supplied")
        if type(require_network_convergence_evidence) is not bool:
            raise TypeError("require_network_convergence_evidence must be an explicit bool")

        canonical_slices = tuple(
            (item.machine_id, item.start, item.stop)
            for item in machine_system.state_layout
        )
        composed_slices = tuple(
            (item.owner_id, item.start, item.stop)
            for item in layout.machine_blocks
        )
        if layout.machine_state_size != machine_system.state_size or composed_slices != canonical_slices:
            raise GeneratorAugmentedIntegratorError(
                "composed machine prefix must exactly preserve MultiMachineSystem state ownership and offsets"
            )
        machine_ids = {machine.machine_id for machine in machine_system.machines}
        if set(base_mechanical_powers_pu) != machine_ids:
            raise GeneratorAugmentedIntegratorError(
                "base mechanical-power inputs must match the canonical machine set exactly"
            )
        self.machine_system = machine_system
        self.integrator = integrator
        self.layout = layout
        self.components = tuple(components)
        self.component_bindings = tuple(component_bindings)
        self.avr_components = tuple(avr_components)
        self.network_solver = network_solver
        self.terminal_voltage_projector = terminal_voltage_projector
        self.machine_bus_ids = {machine.machine_id: machine.bus_id for machine in machine_system.machines}
        self.base_mechanical_powers_pu = dict(base_mechanical_powers_pu)
        self.machine_connection_state = machine_connection_state
        self.require_network_convergence_evidence = require_network_convergence_evidence

    def restore_checkpoint_state(
        self,
        checkpoint: object,
        *,
        expected_identity: object,
    ) -> np.ndarray:
        """Restore only a validated archive checkpoint matching this study identity."""
        from .study_evidence import (
            GeneratorAugmentedArchivedCheckpoint,
            GeneratorStudyCheckpointIdentity,
        )

        if not isinstance(checkpoint, GeneratorAugmentedArchivedCheckpoint):
            raise TypeError("checkpoint must be a validated GeneratorAugmentedArchivedCheckpoint")
        if not isinstance(expected_identity, GeneratorStudyCheckpointIdentity):
            raise TypeError("expected_identity must be GeneratorStudyCheckpointIdentity")
        if checkpoint.identity != expected_identity:
            raise GeneratorAugmentedIntegratorError(
                "archived checkpoint identity does not match the active study"
            )
        state = np.asarray(checkpoint.accepted_state, dtype=float)
        if state.ndim != 1 or state.size != self.layout.state_size or not np.all(np.isfinite(state)):
            raise GeneratorAugmentedIntegratorError(
                "archived checkpoint state does not match the configured augmented state layout"
            )
        return state.copy()

    def continue_from_checkpoint(
        self,
        checkpoint: object,
        *,
        expected_identity: object,
        step_sizes_s: Sequence[float],
        accepted_step_observer: AcceptedStepObserver | None = None,
        continue_predicate: Callable[[float, int], bool] | None = None,
    ) -> tuple[GeneratorAugmentedStepResult, ...]:
        """Continue a validated archive checkpoint using this configured Core integrator.

        The host supplies the active identity (including solver and layout
        fingerprints), the remaining step schedule, and an optional boundary
        predicate. The predicate is evaluated only between accepted steps;
        returning False stops cleanly without creating a synthetic checkpoint.
        This method does not restore solver internals or own study cancellation.
        """
        if isinstance(step_sizes_s, (str, bytes)) or not isinstance(step_sizes_s, Sequence):
            raise TypeError("step_sizes_s must be an ordered sequence")
        intervals = tuple(step_sizes_s)
        if not intervals:
            raise ValueError("step_sizes_s must contain at least one continuation interval")
        if continue_predicate is not None and not callable(continue_predicate):
            raise TypeError("continue_predicate must be callable when supplied")
        validated_intervals: list[float] = []
        for index, raw_interval in enumerate(intervals):
            if (
                isinstance(raw_interval, bool)
                or not isinstance(raw_interval, (int, float))
                or not np.isfinite(float(raw_interval))
                or float(raw_interval) <= 0.0
            ):
                raise GeneratorAugmentedIntegratorError(
                    f"continuation step size at index {index} must be finite and positive"
                )
            validated_intervals.append(float(raw_interval))
        state = self.restore_checkpoint_state(
            checkpoint,
            expected_identity=expected_identity,
        )
        current_time = float(checkpoint.simulation_time_s)
        accepted: list[GeneratorAugmentedStepResult] = []
        for index, interval in enumerate(validated_intervals):
            if continue_predicate is not None and not continue_predicate(current_time, index):
                break
            result = self.step(
                state,
                current_time,
                interval,
                accepted_step_observer=accepted_step_observer,
            )
            if result.simulation_time_s <= current_time:
                raise GeneratorAugmentedIntegratorError(
                    "continued accepted-step time did not advance beyond its checkpoint"
                )
            accepted.append(result)
            state = result.state.copy()
            current_time = result.simulation_time_s
        return tuple(accepted)

    def initialize_state(
        self,
        machine_state: Sequence[float] | np.ndarray,
        operating_point_inputs: DynamicComponentInputs,
    ) -> np.ndarray:
        """Build the augmented initial vector from a host-validated operating point.

        The host must first solve/validate the initial algebraic network state
        and supply terminal voltages and machine powers on the study base.
        Every registered continuous component contributes its own initial
        state; no controller state is guessed from nameplate defaults.
        """
        if not isinstance(operating_point_inputs, DynamicComponentInputs):
            raise TypeError("operating_point_inputs must be DynamicComponentInputs")
        machine = np.asarray(machine_state, dtype=float)
        if machine.ndim != 1 or machine.size != self.machine_system.state_size or not np.all(np.isfinite(machine)):
            raise GeneratorAugmentedIntegratorError(
                "machine_state must match the canonical finite machine state vector"
            )
        if dict(operating_point_inputs.mechanical_power_inputs_pu) != self.base_mechanical_powers_pu:
            raise GeneratorAugmentedIntegratorError(
                "operating-point mechanical powers must match the configured study inputs exactly"
            )
        if set(operating_point_inputs.electrical_active_power_pu) != set(self.machine_bus_ids):
            raise GeneratorAugmentedIntegratorError(
                "operating-point electrical powers must cover the canonical machine set exactly"
            )
        component_ids = {getattr(component, "component_id", None) for component in self.components}
        if component_ids != {block.owner_id for block in self.layout.component_blocks}:
            raise GeneratorAugmentedIntegratorError(
                "registered component identities must match the composed layout exactly"
            )
        states: dict[str, Sequence[float]] = {}
        for component in self.components:
            component_id = component.component_id
            try:
                initial = component.initial_state(operating_point_inputs)
            except Exception as exc:
                raise GeneratorAugmentedIntegratorError(
                    f"component {component_id!r} could not initialize from the supplied operating point"
                ) from exc
            states[component_id] = initial
        try:
            return self.layout.pack(machine, states)
        except Exception as exc:
            raise GeneratorAugmentedIntegratorError(
                "operating-point component states do not satisfy the composed layout"
            ) from exc

    def evaluate_stage(
        self,
        global_state: Sequence[float] | np.ndarray,
        simulation_time_s: float,
    ) -> GeneratorDynamicStageResult:
        """Evaluate the complete composed system at one integrator stage."""
        connection_states = None
        if self.machine_connection_state is not None:
            connection_states = {
                machine.machine_id: self.machine_connection_state(machine.machine_id)
                for machine in self.machine_system.machines
            }
            if any(type(value) is not bool for value in connection_states.values()):
                raise GeneratorAugmentedIntegratorError(
                    "machine connection resolver must return explicit bool values"
                )

        def electrical_power_evaluator(state, bus_voltages, field_voltages):
            return self.machine_system.electrical_powers(
                state,
                bus_voltages,
                field_voltages=field_voltages,
            )

        def machine_derivative_evaluator(
            *,
            state,
            terminal_voltages,
            mechanical_powers,
            machine_connection_states,
            time,
            field_voltages,
        ):
            return self.machine_system.derivatives(
                state=state,
                terminal_voltages=terminal_voltages,
                mechanical_powers=mechanical_powers,
                machine_connection_states=machine_connection_states,
                field_voltages=field_voltages,
                time=time,
            )

        try:
            stage = evaluate_generator_dynamic_stage(
                global_state=global_state,
                simulation_time_s=simulation_time_s,
                layout=self.layout,
                components=self.components,
                component_bindings=self.component_bindings,
                avr_components=self.avr_components,
                network_solver=self.network_solver,
                terminal_voltage_projector=self.terminal_voltage_projector,
                electrical_power_evaluator=electrical_power_evaluator,
                machine_bus_ids=self.machine_bus_ids,
                base_mechanical_powers_pu=self.base_mechanical_powers_pu,
                machine_derivative_evaluator=machine_derivative_evaluator,
                machine_connection_states=connection_states,
            )
            if self.require_network_convergence_evidence and stage.network.convergence is None:
                raise GeneratorAugmentedIntegratorError(
                    "host network solver omitted required algebraic convergence evidence"
                )
            return stage
        except Exception as exc:
            raise GeneratorAugmentedIntegratorError(
                f"augmented generator stage failed at t={float(simulation_time_s):.12g} s"
            ) from exc

    def derivatives(
        self,
        global_state: Sequence[float] | np.ndarray,
        simulation_time_s: float,
    ) -> np.ndarray:
        """Integrator-compatible derivative callback: (global_state, time)."""
        return self.evaluate_stage(global_state, simulation_time_s).derivative_assembly.state_derivatives.copy()

    def step(
        self,
        global_state: Sequence[float] | np.ndarray,
        simulation_time_s: float,
        dt: float,
        *,
        accepted_step_observer: AcceptedStepObserver | None = None,
    ) -> GeneratorAugmentedStepResult:
        """Advance one step and optionally publish its accepted checkpoint.

        The observer runs exactly once, after Core's integrator has returned a
        finite state and the final-time algebraic stage has succeeded. It never
        sees RK/Newton trial states. The caller remains responsible for checking
        StudyCancellationToken at the boundary between calls and for persisting
        the supplied versioned archive payload through Application StudyService.
        """
        if accepted_step_observer is not None and not callable(accepted_step_observer):
            raise TypeError("accepted_step_observer must be callable when supplied")
        state = np.asarray(global_state, dtype=float)
        if state.ndim != 1 or state.size != self.layout.state_size or not np.all(np.isfinite(state)):
            raise GeneratorAugmentedIntegratorError("global_state must match the finite augmented state layout")
        try:
            start = float(simulation_time_s)
            interval = float(dt)
        except (TypeError, ValueError, OverflowError) as exc:
            raise GeneratorAugmentedIntegratorError("simulation time and dt must be numeric") from exc
        if not np.isfinite(start) or not np.isfinite(interval) or interval <= 0.0:
            raise GeneratorAugmentedIntegratorError("simulation time must be finite and dt must be positive")

        derivative_stages: list[GeneratorDynamicStageResult] = []

        def derivative(stage_state: np.ndarray, stage_time: float) -> np.ndarray:
            # Keep this collector local to one step: it captures every actual
            # integrator callback without introducing shared mutable adapter
            # state or treating Newton/RK trial points as accepted checkpoints.
            stage = self.evaluate_stage(stage_state, stage_time)
            derivative_stages.append(stage)
            return stage.derivative_assembly.state_derivatives.copy()

        try:
            accepted_state = self.integrator.step(
                x=state.copy(),
                derivative=derivative,
                t=start,
                dt=interval,
            )
        except Exception as exc:
            raise GeneratorAugmentedIntegratorError(
                f"augmented generator integration failed over [{start:.12g}, {start + interval:.12g}] s"
            ) from exc

        accepted_state = np.asarray(accepted_state, dtype=float)
        if accepted_state.shape != (self.layout.state_size,) or not np.all(np.isfinite(accepted_state)):
            raise GeneratorAugmentedIntegratorError("integrator returned an invalid augmented state")
        final_time = start + interval
        final_stage = self.evaluate_stage(accepted_state, final_time)
        result = GeneratorAugmentedStepResult(
            state=accepted_state,
            simulation_time_s=final_time,
            final_stage=final_stage,
            derivative_stages=tuple(derivative_stages),
        )
        if accepted_step_observer is not None:
            try:
                accepted_step_observer(result)
            except Exception as exc:
                # The numerical step is already accepted. Preserve its exact
                # evidence on the raised exception so the Application host can
                # distinguish checkpoint failure from solver rejection.
                failure = GeneratorAugmentedIntegratorError(
                    "accepted generator step could not be published to its host observer"
                )
                failure.accepted_step_result = result
                failure.cause_type = type(exc).__name__
                raise failure from exc
        return result


__all__ = [
    "GeneratorAugmentedIntegratorError",
    "GeneratorAugmentedStepResult",
    "GeneratorAugmentedDAEAdapter",
]
