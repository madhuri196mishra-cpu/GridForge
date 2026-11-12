"""Detached, archive-safe evidence for augmented generator transient studies.

The value implements Mapping so the canonical StudyResultArchive can persist
its primitive payload without importing plugin dataclasses. Its provenance
attribute also participates in StudyService's existing partial-result retention
contract when a cooperative cancellation returns an interrupted trajectory.
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from core.application.study import StudyExecutionContext, StudyRequest

from .integrator_adapter import GeneratorAugmentedStepResult


def _freeze_archive_value(value: object, *, path: str = "value") -> object:
    """Validate and recursively freeze the primitive archive contract."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain non-finite numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} mapping keys must be strings")
            frozen[key] = _freeze_archive_value(item, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_archive_value(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    raise TypeError(f"{path} contains unsupported archive value {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class GeneratorStudyCheckpointIdentity:
    """Stable host-supplied identity for safe augmented-study checkpoint reuse.

    Fingerprints are produced by the host from canonical study inputs and
    component/state-layout configuration. This contract deliberately does not
    hash arbitrary plugin or project objects.
    """

    study_id: str
    project_id: str
    activation_generation: int
    study_type: str
    model_revision: int
    topology_revision: int
    instrumentation_revision: int
    protection_equipment_revision: int
    protection_configuration_revision: int
    study_configuration_fingerprint: str
    state_layout_fingerprint: str
    solver_id: str

    def __post_init__(self) -> None:
        for name in (
            "study_id", "project_id", "study_type",
            "study_configuration_fingerprint", "state_layout_fingerprint", "solver_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        for name in (
            "activation_generation", "model_revision", "topology_revision",
            "instrumentation_revision", "protection_equipment_revision",
            "protection_configuration_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.activation_generation < 1:
            raise ValueError("activation_generation must be positive")

    @classmethod
    def from_execution(
        cls,
        request: StudyRequest,
        execution_context: StudyExecutionContext,
        *,
        study_configuration_fingerprint: str,
        state_layout_fingerprint: str,
        solver_id: str,
    ) -> "GeneratorStudyCheckpointIdentity":
        if not isinstance(request, StudyRequest):
            raise TypeError("request must be StudyRequest")
        if not isinstance(execution_context, StudyExecutionContext):
            raise TypeError("execution_context must be StudyExecutionContext")
        if (
            request.project_id != execution_context.project_id
            or request.activation_generation != execution_context.activation_generation
            or request.source_revision != execution_context.source_revision
        ):
            raise ValueError("checkpoint identity request/context provenance mismatch")
        return cls(
            study_id=str(request.study_id),
            project_id=request.project_id,
            activation_generation=request.activation_generation,
            study_type=request.study_type,
            model_revision=request.source_revision.model_revision,
            topology_revision=request.source_revision.topology_revision,
            instrumentation_revision=request.source_revision.instrumentation_revision,
            protection_equipment_revision=request.source_revision.protection_equipment_revision,
            protection_configuration_revision=request.source_revision.protection_configuration_revision,
            study_configuration_fingerprint=study_configuration_fingerprint,
            state_layout_fingerprint=state_layout_fingerprint,
            solver_id=solver_id,
        )

    def to_archive_payload(self) -> dict[str, object]:
        return {
            "study_id": self.study_id,
            "project_id": self.project_id,
            "activation_generation": self.activation_generation,
            "study_type": self.study_type,
            "model_revision": self.model_revision,
            "topology_revision": self.topology_revision,
            "instrumentation_revision": self.instrumentation_revision,
            "protection_equipment_revision": self.protection_equipment_revision,
            "protection_configuration_revision": self.protection_configuration_revision,
            "study_configuration_fingerprint": self.study_configuration_fingerprint,
            "state_layout_fingerprint": self.state_layout_fingerprint,
            "solver_id": self.solver_id,
        }


@dataclass(frozen=True, slots=True)
class GeneratorAugmentedArchivedCheckpoint:
    """Validated detached restart point reconstructed from an archive payload."""

    simulation_time_s: float
    accepted_state: tuple[float, ...]
    identity: GeneratorStudyCheckpointIdentity
    study_schema_version: int
    execution_status: str
    trajectory_status: str

    def __post_init__(self) -> None:
        if isinstance(self.simulation_time_s, bool) or not isinstance(self.simulation_time_s, (int, float)) or not math.isfinite(float(self.simulation_time_s)) or self.simulation_time_s < 0:
            raise ValueError("checkpoint time must be finite and non-negative")
        if not isinstance(self.identity, GeneratorStudyCheckpointIdentity):
            raise TypeError("identity must be GeneratorStudyCheckpointIdentity")
        if self.study_schema_version != 1:
            raise ValueError("unsupported generator study schema version")
        if (self.execution_status, self.trajectory_status) not in {
            ("cancelled", "interrupted"), ("failed", "incomplete")
        }:
            raise ValueError("archived execution/trajectory status pair is not resumable")
        state = tuple(self.accepted_state)
        if not state or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value)) for value in state
        ):
            raise ValueError("checkpoint state must be a non-empty finite numeric vector")
        object.__setattr__(self, "simulation_time_s", float(self.simulation_time_s))
        object.__setattr__(self, "accepted_state", tuple(float(value) for value in state))

    @classmethod
    def from_archive_payload(
        cls,
        payload: Mapping[str, object],
        *,
        expected_identity: GeneratorStudyCheckpointIdentity,
    ) -> "GeneratorAugmentedArchivedCheckpoint":
        """Validate the complete evidence envelope and reconstruct its last checkpoint."""
        if not isinstance(payload, Mapping):
            raise TypeError("archive payload must be a mapping")
        if not isinstance(expected_identity, GeneratorStudyCheckpointIdentity):
            raise TypeError("expected_identity must be GeneratorStudyCheckpointIdentity")
        frozen = _freeze_archive_value(payload, path="archive")
        if not isinstance(frozen, Mapping):
            raise TypeError("archive payload must be a mapping")
        if frozen.get("schema") != "gridforge.generator_augmented_study":
            raise ValueError("archive payload is not generator augmented study evidence")
        version = frozen.get("schema_version")
        if type(version) is not int or version != 1:
            raise ValueError(f"unsupported generator study schema version: {version!r}")
        raw_identity = frozen.get("checkpoint_identity")
        if not isinstance(raw_identity, Mapping):
            raise ValueError("archive payload has no checkpoint identity")
        identity_fields = {
            "study_id", "project_id", "activation_generation", "study_type",
            "model_revision", "topology_revision", "instrumentation_revision",
            "protection_equipment_revision", "protection_configuration_revision",
            "study_configuration_fingerprint", "state_layout_fingerprint", "solver_id",
        }
        if set(raw_identity) != identity_fields:
            raise ValueError("checkpoint identity fields are incomplete or unexpected")
        identity = GeneratorStudyCheckpointIdentity(**dict(raw_identity))
        if identity != expected_identity:
            raise ValueError("archived checkpoint identity does not match the requested study")
        provenance = frozen.get("provenance")
        if not isinstance(provenance, Mapping):
            raise ValueError("archive provenance is missing or malformed")
        execution_status = provenance.get("execution_status")
        trajectory_status = provenance.get("trajectory_status")
        steps = frozen.get("accepted_steps")
        if not isinstance(steps, tuple) or not steps:
            raise ValueError("archive contains no accepted steps")
        previous_time = -1.0
        last_time = None
        last_state = None
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping):
                raise ValueError(f"accepted step {index} is malformed")
            if step.get("schema") != "gridforge.generator_augmented_step" or type(step.get("schema_version")) is not int or step.get("schema_version") != 1:
                raise ValueError(f"accepted step {index} has an unsupported schema")
            step_time = step.get("simulation_time_s")
            state = step.get("accepted_state")
            final_stage = step.get("final_stage")
            if isinstance(step_time, bool) or not isinstance(step_time, (int, float)) or not math.isfinite(float(step_time)) or step_time <= previous_time:
                raise ValueError("archived accepted-step times must be finite and strictly increasing")
            if not isinstance(state, tuple) or not state or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value)) for value in state
            ):
                raise ValueError(f"accepted step {index} state vector is malformed")
            if not isinstance(final_stage, Mapping) or final_stage.get("simulation_time_s") != step_time:
                raise ValueError(f"accepted step {index} final stage time is inconsistent")
            sections = {
                name: final_stage.get(name)
                for name in ("excitation", "network", "inputs", "derivatives")
            }
            if any(not isinstance(value, Mapping) for value in sections.values()):
                raise ValueError(f"accepted step {index} has incomplete stage evidence")
            excitation = sections["excitation"]
            network = sections["network"]
            inputs = sections["inputs"]
            derivatives = sections["derivatives"]
            field_values = excitation.get("field_voltages_pu")
            avr_sources = excitation.get("component_ids_by_machine")
            buses = network.get("bus_voltages")
            terminal_voltages = inputs.get("terminal_voltages")
            state_derivatives = derivatives.get("state_derivatives")
            component_derivatives = derivatives.get("component_derivatives")
            if not isinstance(field_values, Mapping) or not field_values or not isinstance(avr_sources, Mapping) or set(field_values) != set(avr_sources):
                raise ValueError(f"accepted step {index} excitation provenance is malformed")
            if not isinstance(buses, Mapping) or not buses or not isinstance(terminal_voltages, Mapping) or not terminal_voltages:
                raise ValueError(f"accepted step {index} voltage evidence is incomplete")
            for voltage_map in (buses, terminal_voltages):
                for voltage in voltage_map.values():
                    if not isinstance(voltage, Mapping) or set(voltage) != {"real", "imag"}:
                        raise ValueError(f"accepted step {index} contains malformed complex voltage")
                    if any(
                        isinstance(part, bool) or not isinstance(part, (int, float))
                        or not math.isfinite(float(part))
                        for part in voltage.values()
                    ):
                        raise ValueError(f"accepted step {index} contains non-finite voltage")
            if not isinstance(state_derivatives, tuple) or len(state_derivatives) != len(state):
                raise ValueError(f"accepted step {index} derivative vector does not match its state")
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(float(value)) for value in state_derivatives
            ):
                raise ValueError(f"accepted step {index} has invalid state derivatives")
            if not isinstance(component_derivatives, Mapping):
                raise ValueError(f"accepted step {index} component derivatives are malformed")
            previous_time = float(step_time)
            last_time = previous_time
            last_state = tuple(float(value) for value in state)
        if (execution_status, trajectory_status) not in {("cancelled", "interrupted"), ("failed", "incomplete")}:
            raise ValueError("archive status is not eligible for restart")
        return cls(
            simulation_time_s=last_time,
            accepted_state=last_state,
            identity=identity,
            study_schema_version=version,
            execution_status=execution_status,
            trajectory_status=trajectory_status,
        )


class GeneratorAugmentedStudyEvidence(Mapping[str, object]):
    """Immutable accepted-step trajectory evidence returned by a host solver.

    Only accepted step results are included. Derivative-stage trial evaluations
    remain nested inside each step payload and are explicitly labelled there.
    The host supplies execution/trajectory status; Application still decides
    the terminal StudyResult status and owns durable persistence.
    """

    __slots__ = ("_payload", "_accepted_steps", "provenance", "time", "checkpoint_identity")

    def __init__(
        self,
        accepted_steps: Sequence[GeneratorAugmentedStepResult],
        *,
        execution_status: str,
        trajectory_status: str,
        termination: Mapping[str, object] | None = None,
        event_schedule: Sequence[Mapping[str, object]] = (),
        resume_provenance: Mapping[str, object] | None = None,
        solver_restart_metadata: Mapping[str, object] | None = None,
        termination_history: Sequence[Mapping[str, object]] = (),
        checkpoint_identity: GeneratorStudyCheckpointIdentity | None = None,
    ) -> None:
        if isinstance(accepted_steps, (str, bytes)) or not isinstance(accepted_steps, Sequence):
            raise TypeError("accepted_steps must be an ordered sequence")
        steps = tuple(accepted_steps)
        if any(not isinstance(step, GeneratorAugmentedStepResult) for step in steps):
            raise TypeError("accepted_steps must contain GeneratorAugmentedStepResult values")
        if execution_status not in {"completed", "cancelled", "failed"}:
            raise ValueError("execution_status must be completed, cancelled, or failed")
        if trajectory_status not in {"complete", "interrupted", "incomplete"}:
            raise ValueError("trajectory_status must be complete, interrupted, or incomplete")
        if execution_status == "completed" and trajectory_status != "complete":
            raise ValueError("completed execution requires a complete trajectory")
        if execution_status == "cancelled" and trajectory_status != "interrupted":
            raise ValueError("cancelled execution requires an interrupted trajectory")
        times = tuple(step.simulation_time_s for step in steps)
        if any(right <= left for left, right in zip(times, times[1:])):
            raise ValueError("accepted step times must be strictly increasing")
        if any(not isinstance(item, Mapping) for item in event_schedule):
            raise TypeError("event_schedule entries must be mappings")
        if resume_provenance is not None and not isinstance(resume_provenance, Mapping):
            raise TypeError("resume_provenance must be a mapping when supplied")
        if solver_restart_metadata is not None and not isinstance(solver_restart_metadata, Mapping):
            raise TypeError("solver_restart_metadata must be a mapping when supplied")
        if any(not isinstance(item, Mapping) for item in termination_history):
            raise TypeError("termination_history entries must be mappings")
        if checkpoint_identity is not None and not isinstance(checkpoint_identity, GeneratorStudyCheckpointIdentity):
            raise TypeError("checkpoint_identity must be GeneratorStudyCheckpointIdentity")
        provenance_data = _freeze_archive_value({
            "execution_status": execution_status,
            "trajectory_status": trajectory_status,
            "termination": dict(termination or {}),
            "termination_history": tuple(dict(item) for item in termination_history),
            "event_schedule": tuple(dict(item) for item in event_schedule),
            "resume": dict(resume_provenance or {}),
            "solver_restart_metadata": dict(solver_restart_metadata or {}),
        }, path="provenance")
        payload = _freeze_archive_value({
            "schema": "gridforge.generator_augmented_study",
            "schema_version": 1,
            "provenance": provenance_data,
            "checkpoint_identity": None if checkpoint_identity is None else checkpoint_identity.to_archive_payload(),
            "accepted_steps": tuple(step.to_archive_payload() for step in steps),
        }, path="study_evidence")
        if not isinstance(provenance_data, Mapping) or not isinstance(payload, Mapping):
            raise TypeError("study evidence payload must be a mapping")
        self.provenance = provenance_data
        self.time = times
        self._payload = payload
        self._accepted_steps = steps
        self.checkpoint_identity = checkpoint_identity

    @classmethod
    def _from_frozen_payload(
        cls,
        payload: Mapping[str, object],
        *,
        checkpoint_identity: GeneratorStudyCheckpointIdentity | None,
    ) -> "GeneratorAugmentedStudyEvidence":
        """Rehydrate immutable evidence after its archive envelope is validated."""
        provenance = payload.get("provenance")
        accepted_steps = payload.get("accepted_steps")
        if not isinstance(provenance, Mapping) or not isinstance(accepted_steps, tuple):
            raise ValueError("rehydrated evidence payload is incomplete")
        times: list[float] = []
        for index, step in enumerate(accepted_steps):
            if not isinstance(step, Mapping):
                raise ValueError(f"rehydrated accepted step {index} is malformed")
            value = step.get("simulation_time_s")
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"rehydrated accepted step {index} has an invalid time")
            times.append(float(value))
        if not times or any(right <= left for left, right in zip(times, times[1:])):
            raise ValueError("rehydrated accepted-step times must be non-empty and strictly increasing")
        instance = cls.__new__(cls)
        instance._payload = payload
        instance._accepted_steps = ()
        instance.provenance = provenance
        instance.time = tuple(times)
        instance.checkpoint_identity = checkpoint_identity
        return instance

    @classmethod
    def from_archive_payload(
        cls,
        payload: Mapping[str, object],
        *,
        expected_identity: GeneratorStudyCheckpointIdentity,
    ) -> "GeneratorAugmentedStudyEvidence":
        """Restore the full immutable evidence envelope for post-restart continuation."""
        checkpoint = GeneratorAugmentedArchivedCheckpoint.from_archive_payload(
            payload,
            expected_identity=expected_identity,
        )
        frozen = _freeze_archive_value(payload, path="archive")
        if not isinstance(frozen, Mapping):
            raise TypeError("archive payload must be a mapping")
        return cls._from_frozen_payload(
            frozen,
            checkpoint_identity=checkpoint.identity,
        )

    @classmethod
    def resume_from_checkpoint(
        cls,
        previous: "GeneratorAugmentedStudyEvidence",
        resumed_steps: Sequence[GeneratorAugmentedStepResult],
        *,
        resume_state: Sequence[float],
        execution_status: str,
        trajectory_status: str,
        termination: Mapping[str, object] | None = None,
        event_schedule: Sequence[Mapping[str, object]] = (),
        checkpoint_identity: GeneratorStudyCheckpointIdentity | None = None,
        solver_restart_metadata: Mapping[str, object] | None = None,
    ) -> "GeneratorAugmentedStudyEvidence":
        """Build a continued trajectory only from the exact last accepted state and identity."""
        if not isinstance(previous, cls):
            raise TypeError("previous must be GeneratorAugmentedStudyEvidence")
        if previous.provenance.get("execution_status") not in {"cancelled", "failed"}:
            raise ValueError("only interrupted or failed evidence can be resumed")
        if not previous.time:
            raise ValueError("cannot resume evidence without an accepted checkpoint")
        if previous.checkpoint_identity is not None and checkpoint_identity is None:
            raise ValueError("resume requires the original checkpoint identity")
        if checkpoint_identity is not None and not isinstance(checkpoint_identity, GeneratorStudyCheckpointIdentity):
            raise TypeError("checkpoint_identity must be GeneratorStudyCheckpointIdentity")
        if previous.checkpoint_identity != checkpoint_identity:
            raise ValueError("resume checkpoint identity does not match the original study inputs")
        if isinstance(resume_state, (str, bytes)) or not isinstance(resume_state, Sequence):
            raise TypeError("resume_state must be an ordered numeric sequence")
        prior_steps = previous._payload.get("accepted_steps")
        if not isinstance(prior_steps, tuple) or not prior_steps:
            raise ValueError("previous evidence has no archived accepted-step payload")
        last_payload = prior_steps[-1]
        if not isinstance(last_payload, Mapping):
            raise ValueError("previous checkpoint payload is malformed")
        accepted_state = last_payload.get("accepted_state")
        if not isinstance(accepted_state, tuple):
            raise ValueError("previous checkpoint has no accepted state vector")
        supplied_state = tuple(resume_state)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in supplied_state
        ):
            raise TypeError("resume_state must contain only finite numeric values")
        if len(supplied_state) != len(accepted_state):
            raise ValueError("resume_state dimension does not match the accepted checkpoint")
        if supplied_state != accepted_state:
            raise ValueError("resume_state must exactly match the last accepted checkpoint state")
        continuation = tuple(resumed_steps)
        if not continuation:
            raise ValueError("resumed_steps must contain at least one accepted step")
        if any(not isinstance(step, GeneratorAugmentedStepResult) for step in continuation):
            raise TypeError("resumed_steps must contain GeneratorAugmentedStepResult values")
        if continuation[0].simulation_time_s <= previous.time[-1]:
            raise ValueError("resumed steps must begin after the prior checkpoint time")
        prior_events = previous.provenance.get("event_schedule", ())
        if not isinstance(prior_events, tuple) or any(not isinstance(item, Mapping) for item in prior_events):
            raise ValueError("previous event schedule is malformed")
        prior_termination = previous.provenance.get("termination", {})
        if not isinstance(prior_termination, Mapping):
            raise ValueError("previous termination provenance is malformed")
        prior_history = previous.provenance.get("termination_history", ())
        if not isinstance(prior_history, tuple) or any(not isinstance(item, Mapping) for item in prior_history):
            raise ValueError("previous termination history is malformed")
        termination_history = tuple(prior_history)
        if prior_termination:
            termination_history = (*termination_history, dict(prior_termination))
        prior_resume = previous.provenance.get("resume", {})
        if not isinstance(prior_resume, Mapping):
            raise ValueError("previous resume provenance is malformed")
        prior_restart_metadata = previous.provenance.get("solver_restart_metadata", {})
        if not isinstance(prior_restart_metadata, Mapping):
            raise ValueError("previous solver restart metadata is malformed")
        effective_restart_metadata = (
            prior_restart_metadata if solver_restart_metadata is None else solver_restart_metadata
        )
        continued = cls(
            continuation,
            execution_status=execution_status,
            trajectory_status=trajectory_status,
            termination=termination,
            termination_history=termination_history,
            event_schedule=(*prior_events, *tuple(event_schedule)),
            checkpoint_identity=checkpoint_identity,
            solver_restart_metadata=effective_restart_metadata,
            resume_provenance={
                "previous_schema": "gridforge.generator_augmented_study",
                "previous_schema_version": 1,
                "previous_execution_status": previous.provenance["execution_status"],
                "checkpoint_time_s": previous.time[-1],
                "resumed_step_count": len(continuation),
                "previous_resume": dict(prior_resume),
                "previous_solver_restart_metadata": dict(prior_restart_metadata),
            },
        )
        combined_payload = _freeze_archive_value({
            "schema": "gridforge.generator_augmented_study",
            "schema_version": 1,
            "provenance": dict(continued.provenance),
            "checkpoint_identity": None if checkpoint_identity is None else checkpoint_identity.to_archive_payload(),
            "accepted_steps": (*prior_steps, *(step.to_archive_payload() for step in continuation)),
        }, path="continued_study_evidence")
        if not isinstance(combined_payload, Mapping):
            raise TypeError("continued study evidence payload must be a mapping")
        return cls._from_frozen_payload(
            combined_payload,
            checkpoint_identity=checkpoint_identity,
        )

    @classmethod
    def from_checkpoint_publication_failure(
        cls,
        accepted_steps: Sequence[GeneratorAugmentedStepResult],
        failure: BaseException,
        *,
        termination: Mapping[str, object] | None = None,
        event_schedule: Sequence[Mapping[str, object]] = (),
        checkpoint_identity: GeneratorStudyCheckpointIdentity | None = None,
    ) -> "GeneratorAugmentedStudyEvidence":
        """Retain a numerically accepted step when its checkpoint observer fails."""
        if not isinstance(failure, BaseException):
            raise TypeError("failure must be an exception")
        accepted_step = getattr(failure, "accepted_step_result", None)
        if not isinstance(accepted_step, GeneratorAugmentedStepResult):
            raise ValueError(
                "failure does not carry an accepted GeneratorAugmentedStepResult"
            )
        prior_steps = tuple(accepted_steps)
        if any(not isinstance(step, GeneratorAugmentedStepResult) for step in prior_steps):
            raise TypeError("accepted_steps must contain GeneratorAugmentedStepResult values")
        if prior_steps and accepted_step.simulation_time_s <= prior_steps[-1].simulation_time_s:
            raise ValueError("failed checkpoint time must follow all previously accepted steps")
        if termination is not None and not isinstance(termination, Mapping):
            raise TypeError("termination must be a mapping when supplied")
        details = dict(termination or {})
        details.update({
            "reason": "checkpoint_publication_failed",
            "failing_time": accepted_step.simulation_time_s,
            "cause_type": str(getattr(failure, "cause_type", type(failure).__name__)),
            "diagnostic_message": str(failure),
        })
        return cls(
            (*prior_steps, accepted_step),
            execution_status="failed",
            trajectory_status="incomplete",
            termination=details,
            event_schedule=event_schedule,
            checkpoint_identity=checkpoint_identity,
        )
    def __getitem__(self, key: str) -> object:
        return self._payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)


__all__ = [
    "GeneratorAugmentedStudyEvidence",
    "GeneratorStudyCheckpointIdentity",
    "GeneratorAugmentedArchivedCheckpoint",
]
