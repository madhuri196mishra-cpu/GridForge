# ============================================================
# File: core/application/application.py
# GridForge V2 — Headless Application Facade
# Author: Subhendu Mishra
# ============================================================

"""Stable public Application facade for commands, reads, events, history, project lifecycle, and studies."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping
from uuid import UUID, uuid4

from core.control.context import ControlExecutionContext
from core.control.engine import ControlEngine
from core.model import EndpointReference, Terminal

from .command import Command
from .creation import CreationCommitIntent, CreationCommandPreparer
from .engineering_configuration import EngineeringUpdatePreparer
from .command_manager import CommandManager
from .commands.sld_commands import AddSLDNodeCommand, AddSLDConnectionCommand, RemoveSLDNodeCommand, RemoveSLDConnectionCommand
from .commands.draft_commands import CommitNetworkCommand, UpdateDraftEquipmentCommand
from .commands.insertion_commands import INSERT_EQUIPMENT_INTO_CONNECTION
from .commands.control_commands import (
    ADD_CONTROL_COMPONENT, REMOVE_CONTROL_COMPONENT,
    CONNECT_CONTROL_SIGNALS, DISCONNECT_CONTROL_SIGNALS,
    ADD_LADDER_RUNG, REMOVE_LADDER_RUNG, MOVE_LADDER_ELEMENT,
    ADD_LOGIC_DEPENDENCY, REMOVE_LOGIC_DEPENDENCY,
    ADD_CONTROL_ACTION_BINDING, REMOVE_CONTROL_ACTION_BINDING, ADD_CONTROL_INTERLOCK, REMOVE_CONTROL_INTERLOCK,
    ADD_DYNAMIC_CONTROL_ASSOCIATION, REMOVE_DYNAMIC_CONTROL_ASSOCIATION,
)
from .control_cycle import ControlCycleResult, ControlCycleService
from .control_signal_mapping import ControlSignalMapping, ControlSignalResolutionError
from .control_dispatch import ControlCommandDispatcher
from .control_execution import ControlExecutionService
from .control_events import (
    ControlComponentCreated, ControlComponentRemoved,
    ControlConnectionCreated, ControlConnectionRemoved,
    ControlProgramChanged, ControlExecutionStarted, ControlExecutionCompleted, ControlExecutionFailed, ControlStateChanged,
)
from .event_bus import ApplicationEventBus
from .events import (
    ElementCreated, ElementRemoved, ElementUpdated, NetworkCommitted,
    NetworkChanged, ProjectClosed, ProjectLoaded, ProjectSaved,
    SLDPresentationChanged, TopologyChanged, ProtectionChanged, ValidationChanged, DraftChanged,
    SimpleWireConnectionCreated, SimpleWireConnectionRemoved, JunctionCreated, JunctionRemoved,
)
from .project import ProjectContext, ProjectSnapshot
from .project_lifecycle import ProjectLifecycleService
from .project_transition import ProjectTransitionDecision, ProjectTransitionRequired
from .read_models import ElementReadModel, NetworkReadModel, ProtectionReadModel, RelayReadModel, SimpleWireReadModel
from .read_service import ProtectionReadService, ReadService, StudyReadService
from .results import ApplicationResult
from core.persistence.network_serializer import deserialize_network, serialize_network
from .revision import ProjectRevision
from .revision_service import RevisionService
from .sld_command_handlers import SLDCommandHandlers
from .services.sld_service import SLDService
from .services.measurement_channel_service import MeasurementChannelService
from .protection_execution import ProtectionExecutionResult, ProtectionExecutionService
from core.protection.context import ProtectionContext
from core.protection.runtime import ProtectionRuntime
from core.protection.directional.directional_relay import _create_authoritative_directional_snapshot
from core.protection.input_contracts import validate_protection_input_contracts, validate_directional_context, validate_sample_representation, input_contract_for
from core.measurement.measurement_channel import MeasurementQuality, MeasurementValidity
from math import isfinite, atan2, degrees
from .services.control_service import ControlApplicationService
from .services.validation_service import ValidationService
from .study import StudyCaseDefinition, StudyExecutionContext, StudyRequest, StudyResult, StudyService
from .validation import ValidationResult


class Application:
    """Public headless GridForge Application facade."""

    _NETWORK_ELEMENT_CREATE_DELETE_TYPES = frozenset({
        "bus", "line", "cable", "transformer", "switch", "breaker", "disconnector", "fuse", "load",
        "generator", "synchronous_machine", "motor", "shunt", "capacitor", "reactor", "solar", "battery", "grid",
    })
    _STATE_CHANGE_FIELDS = frozenset({"closed", "in_service", "tripped", "blown", "status"})
    POST_NETWORK_ACTIVATION_READ_ONLY_AUDIT = "READ_ONLY_ACTIVATION_AUDIT"

    # Immutable Application exposure derived from the Core Terminal contract.
    # UI/bootstrap consumers receive values only; no live Core objects cross this boundary.
    TERMINAL_ROLES_BY_TYPE = Terminal.ROLE_CONTRACT_BY_TYPE

    def __init__(self, command_manager: CommandManager, read_service: ReadService | None = None,
                 event_bus: ApplicationEventBus | None = None,
                 protection_read_service: ProtectionReadService | None = None,
                 validation_service: ValidationService | None = None,
                 sld_service: SLDService | None = None,
                 measurement_channel_service: MeasurementChannelService | None = None,
                 control_service: ControlApplicationService | None = None,
                 control_signal_mapping: ControlSignalMapping | None = None) -> None:
        if not isinstance(command_manager, CommandManager): raise TypeError("Application command_manager must be a CommandManager.")
        if read_service is not None and not isinstance(read_service, ReadService): raise TypeError("Application read_service must implement ReadService.")
        if control_signal_mapping is not None and not isinstance(control_signal_mapping, ControlSignalMapping): raise TypeError("Application control_signal_mapping must be a ControlSignalMapping or None.")
        if control_signal_mapping is not None and not isinstance(read_service, ReadService): raise TypeError("Application read_service is required when control_signal_mapping is configured.")
        if event_bus is not None and not isinstance(event_bus, ApplicationEventBus): raise TypeError("Application event_bus must be an ApplicationEventBus.")
        if protection_read_service is not None and not isinstance(protection_read_service, ProtectionReadService): raise TypeError("Application protection_read_service must be a ProtectionReadService.")
        if validation_service is not None and not isinstance(validation_service, ValidationService): raise TypeError("Application validation_service must be a ValidationService.")
        if sld_service is not None and not isinstance(sld_service, SLDService): raise TypeError("Application sld_service must be an SLDService.")
        if measurement_channel_service is not None and not isinstance(measurement_channel_service, MeasurementChannelService): raise TypeError("Application measurement_channel_service must be a MeasurementChannelService.")
        self._command_manager = command_manager
        self._read_service = read_service
        self._protection_read_service = protection_read_service
        self._validation_service = validation_service
        self._sld_service = sld_service
        self._control_service = control_service
        self._control_signal_mapping = control_signal_mapping
        if control_signal_mapping is not None and control_service is not None:
            self._validate_control_signal_mapping(control_signal_mapping, control_service.configuration)
        self._measurement_channel_service = measurement_channel_service
        self._sld_activation_diagnostics: tuple[dict[str, Any], ...] = ()
        self._event_bus = event_bus if event_bus is not None else ApplicationEventBus()
        self._project_lifecycle: ProjectLifecycleService | None = None
        self._draft_network: Any | None = None
        self._revision_service = RevisionService()
        self._study_service = StudyService(self._event_bus)
        self._study_cases: dict[UUID, StudyCaseDefinition] = {}
        self._study_read_service = StudyReadService(self._study_service)
        self._control_execution = ControlExecutionService(ControlCommandDispatcher(command_manager, command_executor=self.execute))
        self._control_engine: ControlEngine | None = None
        self._control_cycle: ControlCycleService | None = None
        if control_service is not None:
            self._control_engine = ControlEngine(control_service.program.engine, control_service.configuration)
            self._control_cycle = ControlCycleService(
                self._control_engine,
                self._control_execution,
                signal_mapping=self._control_signal_mapping,
                read_service=read_service,
            )
        self._command_manager.set_pre_commit_hook(self._coordinate_pre_commit)
        if self._sld_service is not None:
            self._register_sld_handlers(self._sld_service)

    @property
    def terminal_roles_by_type(self) -> Mapping[str, tuple[str, ...]]:
        """Return the immutable Core-derived terminal-role contract."""
        return self.TERMINAL_ROLES_BY_TYPE

    @property
    def draft_network(self) -> Any | None:
        return self._draft_network

    def set_draft_network(self, draft_network: Any | None) -> None:
        self._draft_network = draft_network

    @property
    def event_bus(self) -> ApplicationEventBus: return self._event_bus
    @property
    def control_execution(self) -> ControlExecutionService: return self._control_execution

    @property
    def control_engine(self) -> ControlEngine:
        if self._control_engine is None:
            raise RuntimeError("Application Control runtime is not configured.")
        return self._control_engine

    @property
    def control_cycle(self) -> ControlCycleService:
        if self._control_cycle is None:
            raise RuntimeError("Application Control-cycle runtime is not configured.")
        return self._control_cycle

    @property
    def control_signal_mapping(self) -> ControlSignalMapping | None:
        """Return the Application-owned immutable engineering signal mapping."""
        return self._control_signal_mapping

    def _validate_control_signal_mapping(self, mapping: ControlSignalMapping, configuration: Any) -> None:
        """Validate binding destinations against the active project Control graph."""
        if self._control_service is None:
            raise RuntimeError("Control service is required to validate signal mapping destinations.")
        if configuration is not self._control_service.configuration:
            raise ValueError("Signal mapping must be validated against the active Control configuration.")
        records = {record.component_id: record.component for record in configuration.program.engine.records()}
        action_ids = {binding.control_id for binding in configuration.action_bindings}
        for binding in mapping.bindings:
            source = binding.source
            destination = binding.destination
            if source.domain != "core":
                raise ValueError(f"Unsupported signal domain {source.domain!r} for {source.element_type}:{source.object_id}:{source.signal}.")
            component = records.get(destination.component_id)
            if component is None:
                raise ValueError(f"Signal mapping destination component {destination.component_id!r} does not exist.")
            if destination.control_id not in action_ids:
                raise ValueError(f"Signal mapping control/action identity {destination.control_id!r} does not exist.")
            input_definition = next(
                (item for item in component.input_definition() if item.name == destination.input_name),
                None,
            )
            if input_definition is None:
                raise ValueError(
                    f"Signal mapping destination input {destination.component_id}.{destination.input_name} does not exist."
                )
            expected = source.expected_type
            if expected is not None:
                accepted = expected if isinstance(expected, tuple) else (expected,)
                if input_definition.value_type not in accepted:
                    names = ", ".join(item.__name__ for item in accepted)
                    raise TypeError(
                        f"Signal mapping type constraint for {destination.component_id}.{destination.input_name} "
                        f"({input_definition.value_type.__name__}) is incompatible with {names}."
                    )

    def configure_control_signal_mapping(self, mapping: ControlSignalMapping | None) -> None:
        """Configure or clear project-scoped signal bindings through Application.

        UI edits use SetControlSignalMappingCommand so configuration participates
        in command history and dirty-state revision tracking. This direct setter
        is reserved for composition and transactional project activation/rollback.
        """
        if mapping is not None and not isinstance(mapping, ControlSignalMapping):
            raise TypeError("mapping must be a ControlSignalMapping or None.")
        if mapping is not None and not isinstance(self._read_service, ReadService):
            raise TypeError("Application read_service is required when control signal mapping is configured.")
        if mapping is not None and self._control_service is not None:
            self._validate_control_signal_mapping(mapping, self._control_service.configuration)
        next_cycle = None
        if self._control_engine is not None:
            next_cycle = ControlCycleService(
                self._control_engine,
                self._control_execution,
                signal_mapping=mapping,
                read_service=self._read_service,
            )
        self._control_signal_mapping = mapping
        if next_cycle is not None:
            self._control_cycle = next_cycle
    @property
    def control_service(self) -> ControlApplicationService:
        if self._control_service is None: raise RuntimeError("Application Control service is not configured.")
        return self._control_service
    @property
    def study_service(self) -> StudyService: return self._study_service

    @property
    def study_cases(self) -> tuple[StudyCaseDefinition, ...]:
        """Return the immutable Application-owned runnable Study Case definitions."""
        return tuple(self._study_cases.values())

    def study_case(self, study_id: UUID) -> StudyCaseDefinition:
        """Resolve one structured Study Case without exposing Core state to UI."""
        if not isinstance(study_id, UUID):
            try:
                study_id = UUID(str(study_id))
            except (TypeError, ValueError) as exc:
                raise ValueError("study_id must be a valid UUID.") from exc
        try:
            return self._study_cases[study_id]
        except KeyError as exc:
            raise KeyError(f"Study Case {study_id} is not registered.") from exc

    def _remember_study_case(self, request: StudyRequest) -> None:
        """Capture a validated request as the Application-owned runnable Study Case."""
        try:
            case = StudyCaseDefinition.from_request(request)
        except (TypeError, ValueError):
            # Existing non-UI callers retain the StudyService contract; only
            # requests that satisfy the structured Study Case contract become
            # re-runnable presentation cases.
            return
        self._study_cases[case.study_id] = case

    def execute_study_case(self, study_id: UUID) -> StudyResult:
        """Rebuild an immutable StudyRequest from an Application-owned Study Case."""
        case = self.study_case(study_id)
        lifecycle = self.project_lifecycle
        context = lifecycle.context
        if context is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            raise RuntimeError("Cannot run a Study Case without a valid active project activation.")
        if case.project_id != context.project_id or case.activation_generation != lifecycle.activation_generation:
            raise ValueError("Study Case project scope does not match the active project generation.")
        request = StudyRequest(
            study_id=case.study_id,
            project_id=context.project_id,
            activation_generation=lifecycle.activation_generation,
            source_revision=self.revision,
            study_type=case.study_type,
            configuration=case.configuration,
        )
        return self.execute_study(request)
    @property
    def project_lifecycle(self) -> ProjectLifecycleService:
        if self._project_lifecycle is None: raise RuntimeError("Application project lifecycle is not configured.")
        return self._project_lifecycle
    @property
    def revision(self) -> ProjectRevision: return self._revision_service.revision
    @property
    def is_dirty(self) -> bool: return self._revision_service.is_dirty
    @property
    def revision_service(self) -> RevisionService: return self._revision_service
    @property
    def validation_service(self) -> ValidationService:
        if self._validation_service is None: raise RuntimeError("Application validation service is not configured.")
        return self._validation_service
    @property
    def sld_service(self) -> SLDService:
        if self._sld_service is None: raise RuntimeError("Application SLD service is not configured.")
        return self._sld_service
    def activate_presentation(self, presentation: Any) -> Any:
        """Make an already-open project presentation the Application-active presentation."""
        if presentation is None:
            raise TypeError("presentation must not be None.")
        lifecycle = self.project_lifecycle
        previous = lifecycle.activate_presentation(presentation)
        try:
            if self._sld_service is not None:
                self._sld_service.bind_document(presentation)
        except BaseException:
            lifecycle.activate_presentation(previous)
            if previous is not None and self._sld_service is not None:
                try:
                    self._sld_service.bind_document(previous)
                except BaseException:
                    pass
            raise
        return presentation

    @property
    def presentation(self) -> Any: return self.project_lifecycle.presentation
    @property
    def measurement_channel_service(self) -> MeasurementChannelService:
        if self._measurement_channel_service is None: raise RuntimeError("Application measurement channel service is not configured.")
        return self._measurement_channel_service

    def evaluate_protection_cycle(
        self,
        evaluation_time: float,
        *,
        action_resolver: Any | None = None,
        evaluation_metadata: Mapping[str, Any] | None = None,
    ) -> ProtectionExecutionResult:
        """Evaluate the active project's protection system at an explicit time.

        Required configured inputs fail closed before any protection element
        executes. Action dispatch is optional and must be explicitly configured;
        this method never infers a physical target from a protection decision.
        """
        lifecycle = self.project_lifecycle
        project = lifecycle.context
        if project is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            raise RuntimeError("Protection evaluation requires an active project.")
        if isinstance(evaluation_time, bool):
            raise TypeError("evaluation_time must be an explicit finite numeric timestamp.")
        try:
            evaluation_time = float(evaluation_time)
        except (TypeError, ValueError) as exc:
            raise TypeError("evaluation_time must be an explicit finite numeric timestamp.") from exc
        if not isfinite(evaluation_time) or evaluation_time < 0:
            raise ValueError("evaluation_time must be finite and non-negative.")

        service = self.measurement_channel_service
        runtime = getattr(self, "protection_runtime", None)
        configuration = getattr(self, "protection_configuration_service", None)
        if runtime is None or configuration is None:
            raise RuntimeError("Active project protection runtime/configuration is not available.")
        if runtime.network is not lifecycle.network:
            raise RuntimeError("Protection runtime network is stale for the active project.")
        active_configuration = configuration.configuration
        if active_configuration is None:
            raise RuntimeError("Active project protection configuration is unavailable.")
        if runtime.configuration is not active_configuration:
            raise RuntimeError(
                "Protection runtime configuration is not the exact active Application configuration."
            )
        if not runtime.configuration_matches_composition:
            return ProtectionExecutionResult(
                diagnostics=(
                    f"project={project.project_id!r}: protection configuration changed after runtime "
                    "composition; runtime is stale and evaluation was refused.",
                )
            )
        if runtime.configuration.project_id != project.project_id:
            raise RuntimeError("Protection runtime project identity does not match the active project.")
        if service.project_id != project.project_id:
            raise RuntimeError("Measurement channel registry belongs to a different project.")
        if service.activation_generation != lifecycle.activation_generation:
            raise RuntimeError("Measurement channel registry activation generation is stale.")

        channels = service.channels
        if evaluation_metadata is not None and not isinstance(evaluation_metadata, Mapping):
            raise TypeError("evaluation_metadata must be a mapping of explicit protection evaluation inputs.")
        diagnostics: list[str] = []
        validated_sample_values: dict[tuple[str, str], tuple[Any, Any, float]] = {}
        diagnostics.extend(
            f"project={project.project_id!r}: {message}"
            for message in validate_protection_input_contracts(runtime.configuration, channels)
        )
        for element in runtime.configuration.elements:
            if not element.enabled:
                continue
            for input_name, channel_id in element.input_channel_ids.items():
                prefix = (
                    f"project={project.project_id!r}, element={element.element_id!r}, "
                    f"input={input_name!r}, channel={channel_id!r}"
                )
                channel = channels.get(channel_id)
                if channel is None:
                    diagnostics.append(f"{prefix}: required measurement channel is missing.")
                    continue
                if getattr(channel, "id", None) != channel_id:
                    diagnostics.append(f"{prefix}: channel identity does not match its registry key.")
                    continue
                timestamp = getattr(channel, "timestamp", None)
                if timestamp is None:
                    diagnostics.append(f"{prefix}: sample timestamp is missing; freshness cannot be established.")
                    continue
                try:
                    if isinstance(timestamp, bool):
                        raise TypeError("boolean timestamp is not valid")
                    timestamp = float(timestamp)
                    value = channel.engineering_value
                    requirement = input_contract_for(runtime.configuration, element, input_name)
                    if requirement is not None:
                        rejected_representation = validate_sample_representation(value, requirement)
                        if rejected_representation is not None:
                            diagnostics.append(
                                f"{prefix}, function={element.function_code!r}: "
                                f"engineering sample representation rejected: {rejected_representation}"
                            )
                            continue
                    elif element.function_code == "67":
                        # ANSI 67 also consumes angle inputs; no unsupported representation
                        # or caller-provided metadata may bypass the directional fail-closed gate.
                        diagnostics.append(
                            f"{prefix}, function='67': no supported canonical input representation "
                            "is available for this configured directional input."
                        )
                        continue
                    finite_value = (
                        isfinite(float(value.real)) and isfinite(float(value.imag))
                        if isinstance(value, complex) else isfinite(float(value))
                    )
                except (TypeError, ValueError, OverflowError, AttributeError):
                    diagnostics.append(
                        f"{prefix}: live sample value or timestamp violates the numeric representation contract."
                    )
                    continue
                if not isfinite(timestamp) or timestamp > evaluation_time:
                    diagnostics.append(f"{prefix}: sample timestamp is non-finite or later than evaluation time.")
                    continue
                quality = getattr(channel, "quality", None)
                if quality is not MeasurementQuality.GOOD:
                    diagnostics.append(
                        f"{prefix}: quality {getattr(quality, 'value', quality)!r} is not permitted; "
                        "only GOOD quality is accepted by the protection evaluation policy."
                    )
                    continue
                if not bool(getattr(channel, "available", False)):
                    diagnostics.append(f"{prefix}: measurement channel is unavailable.")
                    continue
                if not finite_value:
                    diagnostics.append(f"{prefix}: engineering sample is non-finite.")
                    continue
                stale_after = getattr(channel, "stale_after", None)
                if stale_after is None:
                    diagnostics.append(f"{prefix}: stale_after freshness limit is not configured.")
                    continue
                validity = channel.validity(current_time=evaluation_time)
                if validity is not MeasurementValidity.VALID:
                    diagnostics.append(f"{prefix}: measurement validity is {getattr(validity, 'value', validity)!r}.")
                    continue
                # Runtime composition records the exact objects it bound into
                # RelayInput instances. Compare by identity, not merely channel ID.
                if runtime.channels.get(channel_id) is not channel:
                    diagnostics.append(f"{prefix}: protection runtime retains a stale channel object.")
                    continue
                validated_sample_values[(element.element_id, input_name)] = (channel, value, timestamp)

        directional_snapshots: dict[str, Any] = {}
        for element in runtime.configuration.elements:
            if not element.enabled or str(element.function_code).strip().upper() != "67":
                continue
            prefix = f"project={project.project_id!r}, element={element.element_id!r}, function='67'"
            voltage_id = element.input_channel_ids.get("voltage")
            current_id = element.input_channel_ids.get("current")
            voltage_sample = validated_sample_values.get((element.element_id, "voltage"))
            current_sample = validated_sample_values.get((element.element_id, "current"))
            if voltage_sample is None or current_sample is None:
                diagnostics.append(
                    f"{prefix}: validated voltage/current samples are unavailable; directional evaluation is refused."
                )
                continue
            voltage_channel, voltage_value, voltage_timestamp = voltage_sample
            current_channel, current_value, current_timestamp = current_sample
            if runtime.channels.get(voltage_id) is not voltage_channel or channels.get(voltage_id) is not voltage_channel:
                diagnostics.append(f"{prefix}: configured voltage channel object changed after validation.")
                continue
            if runtime.channels.get(current_id) is not current_channel or channels.get(current_id) is not current_channel:
                diagnostics.append(f"{prefix}: configured current channel object changed after validation.")
                continue
            if voltage_channel is current_channel or voltage_id == current_id:
                diagnostics.append(f"{prefix}: voltage and current must be distinct configured channel identities.")
                continue
            if voltage_channel.phase is not current_channel.phase:
                diagnostics.append(
                    f"{prefix}: voltage phase {getattr(voltage_channel.phase, 'value', voltage_channel.phase)!r} "
                    f"does not match current phase {getattr(current_channel.phase, 'value', current_channel.phase)!r}."
                )
                continue
            if voltage_timestamp != current_timestamp:
                diagnostics.append(
                    f"{prefix}: voltage/current sample timestamps differ; a coherent phasor evaluation cycle is required."
                )
                continue
            if not isinstance(voltage_value, complex) or not isinstance(current_value, complex):
                diagnostics.append(
                    f"{prefix}: voltage and current must both be complex phasor samples; scalar magnitudes cannot establish phase angle."
                )
                continue
            if abs(voltage_value) == 0.0 or abs(current_value) == 0.0:
                diagnostics.append(
                    f"{prefix}: zero-magnitude voltage/current phasor has no defined phase angle."
                )
                continue
            voltage_angle = degrees(atan2(voltage_value.imag, voltage_value.real))
            current_angle = degrees(atan2(current_value.imag, current_value.real))
            if not isfinite(voltage_angle) or not isfinite(current_angle):
                diagnostics.append(f"{prefix}: derived phasor angle is non-finite.")
                continue
            # Seal the exact validated samples and their internally-derived angles.
            # The relay consumes current from this snapshot, never a later live RelayInput read.
            directional_snapshots[element.element_id] = _create_authoritative_directional_snapshot(
                element_id=element.element_id,
                voltage_value=voltage_value,
                current_value=current_value,
                voltage_angle=voltage_angle,
                current_angle=current_angle,
                voltage_channel_id=voltage_id,
                current_channel_id=current_id,
                voltage_channel_identity=id(voltage_channel),
                current_channel_identity=id(current_channel),
                sample_timestamp=voltage_timestamp,
                project_id=str(project.project_id),
                activation_generation=lifecycle.activation_generation,
                configuration_identity=id(runtime.configuration),
                runtime_identity=id(runtime),
            )

        if diagnostics:
            return ProtectionExecutionResult(diagnostics=tuple(diagnostics))

        context_metadata = dict(evaluation_metadata or {})
        context_metadata.update({
            "project_id": project.project_id,
            "activation_generation": lifecycle.activation_generation,
        })
        # Only Application-created sealed snapshots authorize ANSI 67. Caller
        # metadata remains ordinary context and cannot supply phasors or provenance.
        for element in runtime.configuration.elements:
            if element.enabled and str(element.function_code).strip().upper() == "67":
                if element.element_id not in directional_snapshots:
                    return ProtectionExecutionResult(
                        diagnostics=(f"project={project.project_id!r}, element={element.element_id!r}: authoritative directional phasor snapshot is unavailable.",)
                    )
        if directional_snapshots:
            context_metadata["directional_measurement_snapshots_by_element"] = directional_snapshots
        context = ProtectionContext(time=evaluation_time, metadata=context_metadata)
        decisions = runtime.system.evaluate(context)
        if action_resolver is None:
            return ProtectionExecutionResult(decisions=decisions)
        execution = ProtectionExecutionService(
            self.control_execution.dispatcher,
            action_resolver=action_resolver,
        )
        return execution.execute(decisions)

    def _register_sld_handlers(self, service: SLDService) -> None:
        for command_type, handler in SLDCommandHandlers(service).handlers().items():
            self._command_manager.register_handler(command_type, handler)

    def attach_project_lifecycle(self, service: ProjectLifecycleService) -> None:
        if not isinstance(service, ProjectLifecycleService): raise TypeError("service must be a ProjectLifecycleService.")
        if self._project_lifecycle is not None and self._project_lifecycle is not service: raise RuntimeError("Application project lifecycle is already configured.")
        self._project_lifecycle = service
        if self._sld_service is not None:
            service.configure_presentation_activator(
                lambda context, presentation: self._bind_sld_transactionally(self._sld_service, presentation, context)
            )
            service.configure_post_network_activator(
                lambda context, loaded, network, generation: self._reconcile_sld_after_network_activation(network),
                mode=self.POST_NETWORK_ACTIVATION_READ_ONLY_AUDIT,
            )

    def configure_project_presentation(self, *, presentation: Any, serializer: Any, deserializer: Any) -> None:
        self.project_lifecycle.configure_presentation(presentation=presentation, serializer=serializer, deserializer=deserializer)
        if self._sld_service is not None:
            self.project_lifecycle.configure_presentation_activator(
                lambda context, value: self._bind_sld_transactionally(self._sld_service, value, context)
            )

    def configure_project_presentation_contract(self, *, factory: Any, serializer: Any, deserializer: Any) -> None:
        self.project_lifecycle.configure_presentation_contract(factory=factory, serializer=serializer, deserializer=deserializer)

    def configure_presentation_activator(self, activator: Any) -> None:
        if not callable(activator): raise TypeError("activator must be callable.")
        if self._sld_service is None:
            self.project_lifecycle.configure_presentation_activator(activator)
            return
        sld_service = self._sld_service

        def composite(context: ProjectContext | None, value: Any | None):
            sld_rollback = self._bind_sld_transactionally(sld_service, value, context)
            try:
                workspace_rollback = activator(context, value)
            except BaseException:
                sld_rollback()
                raise

            def rollback() -> None:
                try:
                    if workspace_rollback is not None:
                        workspace_rollback()
                finally:
                    sld_rollback()

            return rollback

        self.project_lifecycle.configure_presentation_activator(composite)

    @property
    def sld_activation_diagnostics(self) -> tuple[dict[str, Any], ...]:
        """Return the latest read-only SLD/Core activation audit diagnostics."""
        return self._sld_activation_diagnostics

    def _reconcile_sld_after_network_activation(self, network: Any) -> Any:
        """Audit SLD/Core projection consistency after activation without mutation.

        The historical method name is retained for compatibility with the
        lifecycle callback, but activation is strictly read-only. Persistent
        reconciliation remains an explicit Application-controlled operation.
        """
        if self._sld_service is None or not self._sld_service.is_bound:
            self._sld_activation_diagnostics = ()
            return None
        diagnostics = self._sld_service.audit_simple_wire_projection(network)
        self._sld_activation_diagnostics = tuple(dict(item) for item in diagnostics)
        return None

    def _bind_sld_transactionally(
        self,
        service: SLDService,
        value: Any,
        context: ProjectContext | None = None,
    ) -> Any:
        previous = service.document if service.is_bound else None
        if value is None:
            service.detach_document()
            return lambda: service.detach_document() if service.is_bound else None

        service.bind_document(value)

        def rollback() -> None:
            if previous is None:
                service.detach_document()
            else:
                service.bind_document(previous)
        return rollback

    def attach_sld_service(self, service: SLDService) -> None:
        if not isinstance(service, SLDService): raise TypeError("service must be an SLDService.")
        if self._sld_service is not None and self._sld_service is not service: raise RuntimeError("Application SLD service is already configured.")
        self._sld_service = service
        service.attach_application(self)
        self._command_manager.set_pre_commit_hook(self._coordinate_pre_commit)
        if self._project_lifecycle is not None:
            self._project_lifecycle.configure_presentation_activator(
                lambda context, value: self._bind_sld_transactionally(service, value, context)
            )
            self._project_lifecycle.configure_post_network_activator(
                lambda context, loaded, network, generation: self._reconcile_sld_after_network_activation(network),
                mode=self.POST_NETWORK_ACTIVATION_READ_ONLY_AUDIT,
            )
        self._register_sld_handlers(service)
        presentation = self.presentation if self._project_lifecycle is not None else None
        if presentation is not None: service.bind_document(presentation)

    @staticmethod
    def _coerce_transition_decision(decision: ProjectTransitionDecision | str | None) -> ProjectTransitionDecision | None:
        if decision is None: return None
        if isinstance(decision, ProjectTransitionDecision): return decision
        if isinstance(decision, str):
            try: return ProjectTransitionDecision(decision.strip().lower())
            except ValueError as exc: raise ValueError(f"Unsupported project transition decision: {decision!r}") from exc
        raise TypeError("decision must be ProjectTransitionDecision, string, or None.")

    def _prepare_project_transition(self, decision: ProjectTransitionDecision | str | None) -> bool:
        if not self.is_dirty: return True
        normalized = self._coerce_transition_decision(decision)
        if normalized is None:
            raise ProjectTransitionRequired("The active project is dirty; the transition requires SAVE, DISCARD, or CANCEL.")
        if normalized is ProjectTransitionDecision.CANCEL: return False
        if normalized is ProjectTransitionDecision.SAVE:
            self.save_project()
            return True
        if normalized is ProjectTransitionDecision.DISCARD:
            self._run_project_transition(self.project_lifecycle.discard_project_changes)
            return True
        raise RuntimeError(f"Unhandled transition decision: {normalized!r}")

    def _run_project_transition(self, transition: Any) -> Any:
        """Coordinate revision/validation state around one lifecycle activation transaction."""
        if not callable(transition):
            raise TypeError("transition must be callable.")
        revision_state = self._revision_service.snapshot_state()
        try:
            result = transition()
        except BaseException:
            self._revision_service.restore_state(revision_state)
            raise

        # Lifecycle activation has committed successfully; only now establish
        # the clean baseline for the newly authoritative project state.
        self._revision_service.reset_for_project()
        if self._validation_service is not None:
            self._validation_service.invalidate()
            self._event_bus.publish(
                ValidationChanged(
                    metadata={
                        "valid": False,
                        "invalidated": True,
                        "reason": "project_transition",
                        **self._project_scope_metadata(),
                    }
                )
            )
        return result

    def discard_project_changes(self) -> ProjectContext:
        """Discard active-project changes through the existing lifecycle transaction."""
        self._study_service.ensure_no_active_studies()
        return self._run_project_transition(self.project_lifecycle.discard_project_changes)

    def new_project(self, name: str = "Untitled Project", *, project_id: str | None = None,
                    decision: ProjectTransitionDecision | str | None = None) -> ProjectContext:
        self._study_service.ensure_no_active_studies()
        if not self._prepare_project_transition(decision):
            current = self.project_lifecycle.context
            if current is None: raise RuntimeError("Cancel cannot leave the Application without an active project.")
            return current
        context = self._run_project_transition(
            lambda: self.project_lifecycle.new_project(name, project_id=project_id)
        )
        self._event_bus.publish(ProjectLoaded(metadata={
            "project_id": context.project_id, "name": context.name, "operation": "new",
            "activation_generation": self.project_lifecycle.activation_generation,
            "semantic_scope": "APPLICATION_PROJECT_ACTIVATED", "ui_workspace_ready": False,
            "sld_projection_diagnostics": self.sld_activation_diagnostics,
        }))
        return context

    def open_project(self, path: str, *, decision: ProjectTransitionDecision | str | None = None) -> ProjectContext:
        self._study_service.ensure_no_active_studies()
        if not self._prepare_project_transition(decision):
            current = self.project_lifecycle.context
            if current is None: raise RuntimeError("Cancel cannot leave the Application without an active project.")
            return current
        context = self._run_project_transition(lambda: self.project_lifecycle.open_project(path))
        self._event_bus.publish(ProjectLoaded(metadata={
            "project_id": context.project_id, "name": context.name,
            "path": str(context.path) if context.path else None, "operation": "open",
            "activation_generation": self.project_lifecycle.activation_generation,
            "semantic_scope": "APPLICATION_PROJECT_ACTIVATED", "ui_workspace_ready": False,
            "sld_projection_diagnostics": self.sld_activation_diagnostics,
        }))
        return context

    def save_project(self, path: str | None = None) -> ProjectContext:
        context = self.project_lifecycle.save_project(path)
        self._revision_service.mark_persisted()
        self._event_bus.publish(ProjectSaved(metadata={
            "project_id": context.project_id, "path": str(context.path) if context.path else None,
            "activation_generation": self.project_lifecycle.activation_generation,
        }))
        return context

    def save_project_as(self, path: str) -> ProjectContext:
        context = self.project_lifecycle.save_project_as(path)
        self._revision_service.mark_persisted()
        self._event_bus.publish(ProjectSaved(metadata={
            "project_id": context.project_id, "path": str(context.path) if context.path else None,
            "activation_generation": self.project_lifecycle.activation_generation,
        }))
        return context

    def close_project(self, *, decision: ProjectTransitionDecision | str | None = None) -> ProjectContext | None:
        self._study_service.ensure_no_active_studies()
        if not self._prepare_project_transition(decision): return self.project_lifecycle.context
        previous_generation = self.project_lifecycle.activation_generation
        context = self._run_project_transition(self.project_lifecycle.close_project)
        if context is not None:
            self._event_bus.publish(ProjectClosed(metadata={
                "project_id": context.project_id, "name": context.name,
                "activation_generation": previous_generation, "operation": "close",
                "semantic_scope": "APPLICATION_PROJECT_ACTIVATED", "ui_workspace_ready": False,
            }))
        return context

    def capture_project_snapshot(self) -> ProjectSnapshot:
        lifecycle = self.project_lifecycle
        context = lifecycle.context
        if context is None: raise RuntimeError("No active project.")
        network_snapshot = deserialize_network(serialize_network(lifecycle.network))
        dynamic_models = tuple(getattr(getattr(self, "dynamic_models", None), "snapshot", lambda: ())())
        return ProjectSnapshot(project_id=context.project_id, activation_generation=lifecycle.activation_generation,
                               revision=self.revision, network=network_snapshot, dynamic_models=dynamic_models)

    def execute_study(self, request: StudyRequest) -> StudyResult:
        if not isinstance(request, StudyRequest):
            raise TypeError("request must be a StudyRequest.")
        self._remember_study_case(request)
        lifecycle = self.project_lifecycle
        context = lifecycle.context
        if context is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            raise RuntimeError("Cannot start a study without a valid active project activation.")
        if request.project_id != context.project_id or request.activation_generation != lifecycle.activation_generation:
            raise ValueError("StudyRequest project scope does not match the active project generation.")
        # Project validation is an Application study gate. A study cannot
        # publish StudyStarted until authoritative project validation succeeds.
        validation = self.validate_project()
        if not validation.valid:
            raise ValueError("Project validation failed; study execution is blocked before StudyStarted.")
        if request.source_revision != self.revision:
            raise ValueError("StudyRequest source_revision does not match the active project revision.")
        network = lifecycle.network
        # Runtime-only provenance metadata; never authoritative persisted state.
        network.project_id = context.project_id
        network.activation_generation = lifecycle.activation_generation
        if network.state.topology_revision != request.source_revision.topology_revision:
            raise ValueError("Network topology revision does not match StudyRequest source revision.")
        if network.state.topology_dirty or not network.state.topology_valid:
            network.rebuild_topology()
        if network.state.topology_dirty or not network.state.topology_valid:
            raise ValueError("Topology normalization did not produce a valid study-ready state.")
        snapshot = network.topology_snapshot
        if snapshot is None:
            raise ValueError("Canonical TopologySnapshot is unavailable.")
        if snapshot.project_id != request.project_id or snapshot.activation_generation != request.activation_generation or snapshot.topology_revision != request.source_revision.topology_revision:
            raise ValueError("TopologySnapshot provenance does not match StudyRequest.")
        project_snapshot = self.capture_project_snapshot()
        execution_context = StudyExecutionContext(
            project_id=request.project_id,
            activation_generation=request.activation_generation,
            source_revision=request.source_revision,
            topology_snapshot=snapshot,
            project_snapshot=project_snapshot,
        )
        return self._study_service.execute(request, execution_context)

    def read_study_result(self, study_id) -> Any:
        """Return the current project-scoped study result read model."""
        context = self.project_lifecycle.context
        if context is None or not self.project_lifecycle.has_project or self.project_lifecycle.state != "ACTIVE":
            return None
        return self._study_read_service.result(
            study_id,
            project_id=context.project_id,
            activation_generation=self.project_lifecycle.activation_generation,
            current_revision=self.revision,
        )

    def read_study_results(self) -> tuple[Any, ...]:
        """Return all study result read models for the active project generation."""
        context = self.project_lifecycle.context
        if context is None or not self.project_lifecycle.has_project or self.project_lifecycle.state != "ACTIVE":
            return ()
        return self._study_read_service.results(
            project_id=context.project_id,
            activation_generation=self.project_lifecycle.activation_generation,
            current_revision=self.revision,
        )

    def study_result(self, study_id, *, project_id: str, activation_generation: int) -> StudyResult | None:
        """Compatibility-only Application lookup retained for non-presentation callers."""
        return self._study_service.get_result(study_id, project_id=project_id, activation_generation=activation_generation)

    def cancel_study(self, study_id, *, project_id: str, activation_generation: int) -> bool:
        return self._study_service.cancel(study_id, project_id=project_id, activation_generation=activation_generation)

    def _replace_runtime(self, command_manager: CommandManager, read_service: ReadService, validation_service: ValidationService | None = None) -> None:
        if not isinstance(command_manager, CommandManager): raise TypeError("Application command_manager must be a CommandManager.")
        if not isinstance(read_service, ReadService): raise TypeError("read_service must implement ReadService.")
        if validation_service is not None and not isinstance(validation_service, ValidationService): raise TypeError("validation_service must be a ValidationService.")
        if self._sld_service is not None:
            for command_type, handler in SLDCommandHandlers(self._sld_service).handlers().items():
                command_manager.register_handler(command_type, handler)
        next_control_execution = ControlExecutionService(ControlCommandDispatcher(command_manager, command_executor=self.execute))
        self._command_manager, self._read_service, self._validation_service, self._control_execution = command_manager, read_service, validation_service, next_control_execution
        if self._control_engine is not None:
            self._control_cycle = ControlCycleService(
                self._control_engine,
                next_control_execution,
                signal_mapping=self._control_signal_mapping,
                read_service=read_service,
            )
        self._command_manager.set_pre_commit_hook(self._coordinate_pre_commit)

    def _coordinate_pre_commit(self, command: Command, result: ApplicationResult, transaction: Any) -> None:
        """Coordinate Core and persistent SLD semantic reconciliation in one transaction.

        The originating Core command owns the history record. SLDService only
        performs the persistent presentation mutation requested by this hook;
        it never creates a second command/history boundary.
        """
        revision_state = self._revision_service.snapshot_state()
        context = self._command_manager.context
        network = getattr(context, "network", None)
        if command.command_type.startswith("sld."):
            self._revision_service.record_presentation_change(command)
        else:
            topology_revision = None
            if self._revision_service.is_topology_command(command) and network is not None:
                topology_revision = int(network.state.topology_revision)
            self._revision_service.record_command_success(command, topology_revision=topology_revision)
        # Committed history inverses execute after Transaction.commit().
        # Transaction.COMMITTED is not an undo-validity guard.
        transaction.record_undo(
            lambda state=revision_state: self._revision_service.restore_state(state)
        )

        command_type = command.command_type
        if command_type in {
            "protection.create_configuration",
            "protection.update_configuration",
            "protection.delete_configuration",
            "protection.bind_measurement",
            "protection.unbind_measurement",
        }:
            configuration_service = getattr(self, "protection_configuration_service", None)
            channel_service = self._measurement_channel_service
            if configuration_service is None or configuration_service.configuration is None:
                raise RuntimeError("Protection configuration service is unavailable during transactional recomposition.")
            if channel_service is None:
                raise RuntimeError("Measurement channel service is unavailable during transactional recomposition.")
            if network is None:
                raise RuntimeError("Active Network is unavailable during transactional protection recomposition.")
            previous_runtime = getattr(self, "protection_runtime", None)
            candidate_runtime = ProtectionRuntime(network, configuration_service.configuration)
            candidate_runtime.compose(channel_service.channels)
            self.protection_runtime = candidate_runtime
            transaction.record_undo(
                lambda previous=previous_runtime: setattr(self, "protection_runtime", previous)
            )
        if command_type == INSERT_EQUIPMENT_INTO_CONNECTION:
            if self._sld_service is not None:
                metadata = result.metadata
                self._sld_service.reconcile_insertion(
                    connection_id=str(metadata["connection_id"]),
                    equipment_id=str(metadata["equipment_id"]),
                    equipment_type=str(metadata["equipment_type"]),
                    insertion_position=tuple(metadata["insertion_position"]),
                    orientation=float(metadata["orientation"]),
                    segment_index=int(metadata["segment_index"]),
                    input_endpoint=dict(metadata["input_endpoint"]),
                    output_endpoint=dict(metadata["output_endpoint"]),
                    replacement_connection_ids=tuple(metadata["replacement_connection_ids"]),
                    terminal_anchors=metadata.get("terminal_anchors"),
                    transaction=transaction,
                )
            return
        if command_type == "network.commit_draft":
            if self._sld_service is not None:
                # Draft presentation is authoring state. A successful commit
                # receives one deterministic projection identity per Core
                # equipment; no sld-draft-* node survives as a second object.
                for item in tuple(result.metadata.get("created_elements", ())):
                    core_id = str(item["core_id"])
                    node_id = str(item.get("sld_node_id") or f"sld-core-{core_id}")
                    existing_by_equipment = self._sld_service.document.model.get_node_by_equipment_id_optional(core_id)
                    if existing_by_equipment is not None:
                        self._sld_service.execute(
                            RemoveSLDNodeCommand(node_id=existing_by_equipment.node_id),
                            transaction,
                        )
                    existing = self._sld_service.document.model.get_node_optional(node_id)
                    if existing is not None:
                        self._sld_service.execute(
                            RemoveSLDNodeCommand(node_id=node_id),
                            transaction,
                        )
                    x, y = item.get("x"), item.get("y")
                    if x is not None and y is not None:
                        self._sld_service.execute(
                            AddSLDNodeCommand(
                                node_id=node_id,
                                equipment_id=core_id,
                                x=float(x),
                                y=float(y),
                                presentation_owner="projection",
                                projection_source="network.commit_draft",
                                element_type=str(item["element_type"]),
                                presentation=item.get("presentation") or None,
                                presentation_properties={"position_owner": "engineer"},
                                correlation_id=command.correlation_id,
                                causation_id=command.command_id,
                            ),
                            transaction,
                            authoritative_value=context.network.get_by_identity(core_id),
                        )

                for item in tuple(result.metadata.get("committed_connections", ())):
                    connection_id = str(
                        item.get("sld_connection_id")
                        or self._sld_service.presentation_connection_id(str(item["connection_id"]))
                    )
                    existing = self._sld_service.document.model.get_connection_optional(connection_id)
                    if existing is not None:
                        raise ValueError(
                            f"SLD connection identity collision for committed connection {connection_id!r}."
                        )
                    self._sld_service.execute(
                        AddSLDConnectionCommand(
                            connection_id=connection_id,
                            source_node_id=self._sld_node_id_for_endpoint_mapping(item["endpoint_a"]),
                            target_node_id=self._sld_node_id_for_endpoint_mapping(item["endpoint_b"]),
                            source_endpoint=item["endpoint_a"],
                            target_endpoint=item["endpoint_b"],
                            connection_kind="SIMPLE_WIRE",
                            route=item.get("route"),
                            presentation_owner="projection",
                            projection_source="network.commit_draft",
                            core_connection_id=str(item["connection_id"]),
                            correlation_id=command.correlation_id,
                            causation_id=command.command_id,
                        ),
                        transaction,
                    )
            return

        if self._sld_service is None:
            return

        command_type = command.command_type

        if command_type in {
            "connectivity.create_simple_wire",
            "model.create_line",
            "model.create_cable",
        }:
            self._coordinate_connection_pre_commit(command, transaction)
            return

        if command_type in {
            "connectivity.remove_simple_wire",
            "model.delete_line",
            "model.delete_cable",
        }:
            connection_id = str(
                command.payload.get("connection_id")
                or command.payload.get("line_id")
                or command.payload.get("cable_id")
                or ""
            )
            if connection_id:
                self._sld_service.reconcile_connection_delete(
                    connection_id=connection_id,
                    transaction=transaction,
                )

        if not command_type.startswith("model."):
            return

        action = self._action_from_command_type(command_type)
        element_id = self._element_id(command)
        element_type = self._element_type(command)
        if element_id is None or element_type is None:
            return

        if action == "delete":
            self._sld_service.reconcile_element_delete(
                equipment_id=element_id,
                transaction=transaction,
            )
            return

        if action != "create":
            if action == "update":
                # The originating mutation handler has already changed Core
                # inside this transaction and returns the authoritative
                # transaction-visible object through ApplicationResult.value.
                # Pre-commit reconciliation must consume that result directly;
                # Application ReadModels are post-mutation read-side views and
                # are never authoritative evidence for an open transaction.
                updated_object = result.value
                if updated_object is None:
                    raise ValueError(
                        f"Update command {command_type!r} returned no "
                        "transaction-visible authoritative value."
                    )
                updated_object_id = getattr(updated_object, "id", None)
                if updated_object_id is None or str(updated_object_id) != element_id:
                    raise ValueError(
                        f"Updated equipment identity mismatch: expected {element_id!r}, "
                        f"got {updated_object_id!r}."
                    )
                self._sld_service.reconcile_element_update(
                    equipment_id=element_id,
                    element_type=element_type,
                    core_object=updated_object,
                    transaction=transaction,
                )
            return

        # Placement commands carry presentation coordinates while Core remains
        # authoritative for electrical identity. A generated SLD node is
        # projection-owned; only the explicit position field is engineer-owned.
        x = command.payload.get("presentation_x")
        y = command.payload.get("presentation_y")
        if x is None and command_type == "model.create_bus":
            x = command.payload.get("x")
        if y is None and command_type == "model.create_bus":
            y = command.payload.get("y")
        if x is None or y is None:
            return

        # Creation reconciliation runs before Transaction.commit().  The
        # handler result is the authoritative transaction-visible handoff:
        # ApplicationResult.value is the Core object returned by the mutation
        # service, while read models remain post-mutation read-side adapters.
        # Never require a read model to prove a creation here.
        created_object = result.value
        created_object_id = getattr(created_object, "id", None)
        if created_object_id is None or str(created_object_id) != element_id:
            raise ValueError(
                f"Created equipment identity mismatch: expected {element_id!r}, "
                f"got {created_object_id!r}."
            )
        source = "core_transaction"

        existing = self._sld_service.document.model.get_node_by_equipment_id_optional(element_id)
        if existing is not None:
            if existing.properties.get("presentation_owner") == "engineer" and existing.properties.get("projection_source") is None:
                # An explicitly authored presentation already exists. Preserve
                # it; semantic reconciliation remains active on later updates.
                return
            if existing.properties.get("projection_source") != source:
                raise ValueError(
                    f"Placement projection ownership collision for equipment ID: {element_id!r}"
                )
            return

        presentation_properties = dict(command.payload.get("presentation_properties", {}))
        presentation_properties["position_owner"] = "engineer"
        projection_result = self._sld_service.execute(
            AddSLDNodeCommand(
                node_id=self._new_sld_node_id(),
                equipment_id=element_id,
                x=float(x),
                y=float(y),
                presentation_owner="projection",
                projection_source=source,
                element_type=element_type,
                presentation_properties=presentation_properties,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
            ),
            transaction,
            authoritative_value=created_object,
        )
        if not projection_result.success:
            raise RuntimeError(projection_result.message)

    def _sld_node_id_for_endpoint_mapping(self, mapping: Any) -> str:
        if self._sld_service is None:
            raise RuntimeError("SLD service is required for endpoint projection.")
        object_id = str(mapping.get("object_id") or "")
        if not object_id:
            raise ValueError("Committed endpoint mapping requires a non-empty object_id.")
        nodes = tuple(
            node for node in self._sld_service.document.model.nodes
            if str(getattr(node, "equipment_id", "") or "") == object_id
        )
        if not nodes:
            raise ValueError(f"No SLD presentation node exists for committed endpoint object {object_id!r}.")
        if len(nodes) != 1:
            raise ValueError(
                f"Ambiguous SLD presentation node identity for committed endpoint object {object_id!r}: "
                f"{len(nodes)} nodes match."
            )
        return str(nodes[0].node_id)

    def _new_sld_node_id(self) -> str:
        """Generate an independent persistent SLD identity.

        SLD node identity is presentation/document identity and must never be
        derived from authoritative Core equipment identity. The generated ID
        is persisted by SLDService and therefore remains stable across reloads.
        """
        if self._sld_service is None:
            raise RuntimeError("SLD service is required for SLD node identity generation.")
        model = self._sld_service.document.model
        while True:
            node_id = f"sld-node-{uuid4()}"
            if not model.has_node(node_id):
                return node_id

    def _coordinate_connection_pre_commit(self, command: Command, transaction: Any) -> None:
        """Create the semantic SLD connection companion before Core commit."""
        if self._sld_service is None:
            return

        if command.command_type == "connectivity.create_simple_wire":
            source_ref = command.payload["endpoint_a"]
            target_ref = command.payload["endpoint_b"]
            connection_id = str(command.payload["connection_id"])
            connection_kind = "SIMPLE_WIRE"
        else:
            source_ref = command.payload["endpoint_from"]
            target_ref = command.payload["endpoint_to"]
            connection_id = str(command.payload.get("line_id") or command.payload.get("cable_id"))
            connection_kind = "CABLE" if command.command_type == "model.create_cable" else "LINE"

        # Line/Cable are equipment objects whose terminals can remain
        # disconnected at creation time. Their later wiring is a separate
        # terminal/connection operation.
        if command.command_type in {"model.create_line", "model.create_cable"} and (source_ref is None or target_ref is None):
            return
        if not isinstance(source_ref, EndpointReference) or not isinstance(target_ref, EndpointReference):
            raise ValueError("Connection commands require canonical EndpointReference endpoints.")
        if source_ref == target_ref:
            raise ValueError("Connection endpoints must be distinct.")

        source = self._sld_endpoint_for_reference(source_ref)
        target = self._sld_endpoint_for_reference(target_ref)

        projection_result = self._sld_service.execute(
            AddSLDConnectionCommand(
                connection_id=connection_id,
                source_node_id=source["node_id"],
                target_node_id=target["node_id"],
                source_endpoint=source,
                target_endpoint=target,
                route={"routing_mode": "orthogonal", "ownership": "auto", "points": []},
                connection_kind=connection_kind,
                presentation_owner="projection",
                projection_source="core_transaction",
                core_connection_id=connection_id,
                correlation_id=command.correlation_id,
                causation_id=command.command_id,
            ),
            transaction,
        )
        if not projection_result.success:
            raise RuntimeError(projection_result.message)

    def _sld_endpoint_for_reference(self, reference: EndpointReference) -> dict[str, str]:
        """Resolve an immutable Core endpoint reference to semantic SLD identity only."""
        if self._sld_service is None:
            raise RuntimeError("SLD service is required for endpoint presentation coordination.")

        nodes = tuple(
            node for node in self._sld_service.document.model.nodes
            if str(getattr(node, "equipment_id", "") or "") == str(reference.object_id)
        )
        if not nodes:
            raise ValueError(
                f"No SLD presentation node exists for endpoint object {reference.object_id!r}."
            )
        if len(nodes) != 1:
            raise ValueError(
                f"Ambiguous SLD presentation node identity for endpoint object {reference.object_id!r}: "
                f"{len(nodes)} nodes match."
            )
        node = nodes[0]

        if reference.is_bus:
            attachment_id = getattr(reference, "attachment_id", None)
            if not isinstance(attachment_id, str) or not attachment_id:
                raise ValueError("Bus connection endpoints require attachment_id.")
            return {
                "kind": "bus",
                "node_id": str(node.node_id),
                "bus_id": str(reference.object_id),
                "attachment_id": attachment_id,
            }

        return {
            "kind": "equipment",
            "node_id": str(node.node_id),
            "equipment_id": str(reference.object_id),
            "terminal_role": str(reference.terminal_role),
        }

    def mark_project_persisted(self) -> ProjectRevision: return self._revision_service.mark_persisted()
    def record_presentation_change(self) -> ProjectRevision: return self._revision_service.record_presentation_change()

    def validate_project(self) -> ValidationResult:
        context = self.project_lifecycle.context
        if context is None: raise RuntimeError("Validation requires an active project.")
        result = self.validation_service.validate_project(context=context, presentation=self.presentation)
        scoped = replace(result, project_id=context.project_id, activation_generation=self.project_lifecycle.activation_generation)
        self._event_bus.publish(ValidationChanged(metadata={
            "valid": scoped.valid,
            "errors": scoped.summary.errors,
            "warnings": scoped.summary.warnings,
            "model_revision": scoped.model_revision,
            "topology_revision": scoped.topology_revision,
            "project_id": scoped.project_id,
            "activation_generation": scoped.activation_generation,
        }))
        return scoped

    def read_validation(self) -> ValidationResult | None:
        result = self.validation_service.read_validation()
        if result is None: return None
        context = self.project_lifecycle.context
        if context is None: return None
        return replace(result, project_id=context.project_id, activation_generation=self.project_lifecycle.activation_generation)

    def execute_control_cycle(
        self,
        *,
        simulation_time: float | None = None,
        external_inputs: Mapping[str, Mapping[str, Any]] | None = None,
        context: ControlExecutionContext | None = None,
        interlock_inputs: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> ControlCycleResult:
        """Execute one Control cycle through the canonical Application-owned runtime."""
        lifecycle = self.project_lifecycle
        active_context = lifecycle.context
        if active_context is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            raise RuntimeError("Control cycle execution requires an active project.")
        configuration = self.control_service.configuration
        if configuration.project_id != active_context.project_id:
            raise RuntimeError("Control configuration project_id does not match the active project.")
        engine = self.control_engine
        if engine.configuration is not configuration:
            raise RuntimeError("ControlEngine configuration is not the active Application Control configuration.")
        if engine.logic_engine is not self.control_service.program.engine:
            raise RuntimeError("ControlEngine LogicEngine is not the active LadderProgram.engine.")
        if engine.activation_generation != lifecycle.activation_generation:
            raise RuntimeError("ControlEngine activation generation is stale for the active project.")
        t = context.simulation_time if context is not None else simulation_time
        scope = self._project_scope_metadata()
        self._event_bus.publish(ControlExecutionStarted(metadata={**scope, "simulation_time": t}))
        try:
            result = self.control_cycle.execute(
                simulation_time=simulation_time,
                external_inputs=external_inputs,
                context=context,
                interlock_inputs=interlock_inputs,
            )
        except Exception as exc:
            failure_metadata = {
                **scope,
                "simulation_time": t,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
            if isinstance(exc, ControlSignalResolutionError):
                # Keep per-source resolution failures machine-readable at the
                # established Application event boundary; re-raise unchanged
                # so callers retain the original exception and traceback.
                failure_metadata["diagnostics"] = exc.diagnostics
            self._event_bus.publish(ControlExecutionFailed(metadata=failure_metadata))
            raise
        self._event_bus.publish(ControlExecutionCompleted(metadata={
            **scope,
            "simulation_time": result.simulation_time,
            "executed_count": len(result.execution.executed_decisions),
            "blocked_count": len(result.execution.blocked_decisions),
            "failed_count": len(result.execution.failed_decisions),
        }))
        self._event_bus.publish(ControlStateChanged(metadata={
            **scope,
            "simulation_time": result.simulation_time,
            "state": "completed" if not result.execution.failed_decisions else "completed_with_failures",
        }))
        return result

    def prepare_creation_command(self, intent: CreationCommitIntent) -> Command:
        """Translate immutable UI creation intent into the canonical Application command."""
        return CreationCommandPreparer.prepare(intent)

    def prepare_engineering_update(self, intent: Any) -> Command:
        """Translate typed engineering intent into the authoritative update command."""
        return EngineeringUpdatePreparer.prepare(intent)

    def prepare_draft_engineering_update(self, intent: Any) -> Command:
        """Prepare an immutable DraftNetwork engineering-data update command."""
        if str(getattr(intent, "identity_kind", "")).strip().lower() != "draft":
            raise ValueError("Draft engineering update requires a draft projection identity.")
        draft_id = str(getattr(intent, "element_id", "")).strip()
        if not draft_id:
            raise ValueError("Draft engineering update requires draft_id.")
        values = dict(getattr(intent, "values", {}) or {})
        if not values:
            raise ValueError("Draft engineering update requires at least one engineering value.")
        draft = self._draft_network
        if draft is None:
            raise RuntimeError("Application DraftNetwork is not configured.")
        current = draft.require_equipment(draft_id)
        requested_type = str(getattr(intent, "element_type", "")).strip().lower()
        if requested_type and requested_type != str(current.equipment_type).strip().lower():
            raise ValueError(
                f"Draft engineering update type mismatch for {draft_id!r}: "
                f"expected {current.equipment_type!r}, got {requested_type!r}."
            )
        engineering_data = dict(current.engineering_data)
        engineering_data.update(values)
        return UpdateDraftEquipmentCommand(
            draft_id=draft_id,
            changes={"engineering_data": engineering_data},
        )

    def commit_draft_network(self) -> ApplicationResult:
        """Commit the complete Application-owned DraftNetwork through one canonical command.

        UI/controllers call this Application operation for Commit Drawing/Commit
        Network. The method never mutates Core directly and never creates a
        second commit/history boundary.
        """
        lifecycle = self.project_lifecycle
        context = lifecycle.context
        draft = self._draft_network
        if context is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            raise RuntimeError("Cannot commit DraftNetwork without an active project.")
        if draft is None:
            raise RuntimeError("Application DraftNetwork is not configured.")
        if draft.project_id != context.project_id:
            raise ValueError("DraftNetwork project_id does not match the active project.")
        if draft.activation_generation != lifecycle.activation_generation:
            raise ValueError("DraftNetwork activation_generation does not match the active project.")
        command = CommitNetworkCommand(
            project_id=context.project_id,
            activation_generation=lifecycle.activation_generation,
            draft_network=draft.to_dict(),
        )
        return self.execute(command)

    def read_draft_network(self) -> Mapping[str, Any] | None:
        """Return an isolated serialized snapshot for Application read/projection consumers."""
        draft = self._draft_network
        if draft is None:
            return None
        from copy import deepcopy
        return deepcopy(draft.to_dict())

    def execute(self, command: Command) -> ApplicationResult:
        if not isinstance(command, Command): raise TypeError("Application.execute requires a Command.")
        result = self._command_manager.execute(command)
        if result.success:
            if self._validation_service is not None:
                    self._validation_service.invalidate()
                    self._event_bus.publish(ValidationChanged(metadata={"valid": False, "invalidated": True, **self._project_scope_metadata()}))
            self._publish_semantic_events(command, result, operation="execute")
        return result

    def supports(self, command_type: str) -> bool: return isinstance(command_type, str) and self._command_manager.is_registered(command_type)

    def supports_control_component(self, component_type: str) -> bool:
        """Return whether the active Application Control service can create a component type."""
        if not isinstance(component_type, str) or not component_type.strip():
            return False
        service = self._control_service
        return (
            service is not None
            and self.supports(ADD_CONTROL_COMPONENT)
            and component_type.strip() in service.supported_component_types
        )

    def command_types(self) -> tuple[str, ...]: return self._command_manager.registered_commands

    def undo(self) -> ApplicationResult | None:
        records = self._command_manager.undo_commands()
        command = records[-1].command if records else None
        result = self._command_manager.undo()
        if result is not None and result.success:
            network = getattr(self._command_manager.context, "network", None)
            if command is not None and self._revision_service.has_undo_transition_for(command):
                self._revision_service.record_undo(
                    topology_revision=int(network.state.topology_revision) if network is not None else None
                )
            if self._validation_service is not None and command is not None and not command.command_type.startswith("sld."):
                self._validation_service.invalidate()
                self._event_bus.publish(ValidationChanged(metadata={"valid": False, "invalidated": True, **self._project_scope_metadata()}))
            self._publish_history_events(command, result, operation="undo")
        return result

    def redo(self) -> ApplicationResult | None:
        records = self._command_manager.redo_commands()
        command = records[-1].command if records else None
        result = self._command_manager.redo()
        if result is not None and result.success and command is not None:
            network = getattr(self._command_manager.context, "network", None)
            if command is not None and self._revision_service.has_redo_transition_for(command):
                self._revision_service.record_redo(
                    topology_revision=int(network.state.topology_revision)
                    if network is not None and self._revision_service.is_topology_command(command)
                    else None
                )
            if self._validation_service is not None and not command.command_type.startswith("sld."):
                self._validation_service.invalidate()
                self._event_bus.publish(ValidationChanged(metadata={"valid": False, "invalidated": True, **self._project_scope_metadata()}))
            self._publish_semantic_events(command, result, operation="redo")
        return result

    def can_undo(self) -> bool: return self._command_manager.can_undo()
    def can_redo(self) -> bool: return self._command_manager.can_redo()
    def undo_count(self) -> int: return self._command_manager.undo_count()
    def redo_count(self) -> int: return self._command_manager.redo_count()
    def undo_commands(self) -> tuple: return self._command_manager.undo_commands()
    def redo_commands(self) -> tuple: return self._command_manager.redo_commands()
    def clear_history(self) -> None: self._command_manager.clear_history()

    def read_control(self):
        service = getattr(self, "control_service", None)
        if service is None:
            raise RuntimeError("Application Control service is not configured.")
        return service.read()

    def read_network(self) -> NetworkReadModel:
        self._require_read_service(); return self._read_service.network()  # type: ignore[union-attr]
    def read_element(self, element_type: str, object_id: str) -> ElementReadModel:
        self._require_read_service(); return self._read_service.element(element_type, object_id)  # type: ignore[union-attr]
    def read_simple_wire(self, connection_id: str) -> SimpleWireReadModel:
        network = self.read_network()
        for connection in network.simple_wires:
            if connection.connection_id == connection_id:
                return connection
        raise KeyError(f"Simple Wire connection '{connection_id}' is not represented by the Application read model.")

    def read_protection(self) -> ProtectionReadModel:
        self._require_protection_read_service(); return self._protection_read_service.protection()  # type: ignore[union-attr]
    def read_protection_configuration(self) -> tuple[Any, ...]:
        """Return immutable project-scoped protection configuration read state."""
        service = getattr(self, "protection_configuration_service", None)
        configuration = getattr(service, "configuration", None) if service is not None else None
        if configuration is None:
            return ()
        return tuple(configuration.elements)
    def read_relay(self, relay_id: str) -> RelayReadModel:
        self._require_protection_read_service(); return self._protection_read_service.relay(relay_id)  # type: ignore[union-attr]

    def _project_scope_metadata(self) -> dict[str, object]:
        context = self.project_lifecycle.context
        return {"project_id": context.project_id if context is not None else None,
                "activation_generation": self.project_lifecycle.activation_generation}

    def _publish_semantic_events(self, command: Command, result: ApplicationResult, *, operation: str) -> None:
        metadata = {**dict(result.metadata), "command_id": str(command.command_id), "message": result.message, "operation": operation, **self._project_scope_metadata()}
        if command.command_type in {
            "protection.create_configuration",
            "protection.update_configuration",
            "protection.delete_configuration",
            "protection.bind_measurement",
            "protection.unbind_measurement",
        }:
            self._event_bus.publish(ProtectionChanged(
                operation=operation,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                metadata=metadata,
            ))
            return
        if command.command_type in {
            "draft.add_equipment", "draft.update_equipment", "draft.remove_equipment",
            "draft.add_connection", "draft.create_connection", "draft.remove_connection",
        }:
            self._event_bus.publish(DraftChanged(
                operation=operation,
                metadata=metadata,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            ))
            return
        if command.command_type == INSERT_EQUIPMENT_INTO_CONNECTION:
            payload = dict(metadata)
            command_payload = command.payload
            payload.setdefault("connection_id", str(command_payload["connection_id"]))
            payload.setdefault("equipment_type", str(command_payload["equipment_type"]))
            payload.setdefault(
                "equipment_id",
                str(
                    command_payload.get("equipment_id")
                    or f"insert-{command.command_id.hex}"
                ),
            )
            payload.setdefault("insertion_position", tuple(command_payload["insertion_position"]))
            payload.setdefault("orientation", float(command_payload["orientation"]))
            payload.setdefault("segment_index", int(command_payload["segment_index"]))
            payload.setdefault(
                "replacement_connection_ids",
                (
                    f"{command_payload['connection_id']}-A",
                    f"{command_payload['connection_id']}-B",
                ),
            )
            effective_action = "remove" if operation == "undo" else "create"
            if effective_action == "remove":
                self._event_bus.publish(ElementRemoved(
                    element_id=str(payload["equipment_id"]),
                    element_type=str(payload["equipment_type"]),
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                    metadata=payload,
                ))
            else:
                self._event_bus.publish(ElementCreated(
                    element_id=str(payload["equipment_id"]),
                    element_type=str(payload["equipment_type"]),
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                    metadata=payload,
                ))
            self._event_bus.publish(TopologyChanged(operation=operation, metadata=payload, correlation_id=command.correlation_id, causation_id=command.causation_id))
            self._event_bus.publish(NetworkChanged(operation=operation, metadata=payload, correlation_id=command.correlation_id, causation_id=command.causation_id))
            self._event_bus.publish(SLDPresentationChanged(operation=operation, metadata={**payload, "presentation_operation": "electrical_insertion"}, correlation_id=command.correlation_id, causation_id=command.causation_id))
            return
        if command.command_type == "network.commit_draft":
            if operation == "execute":
                self._event_bus.publish(NetworkCommitted(metadata=metadata, correlation_id=command.correlation_id, causation_id=command.causation_id))
            else:
                # Undo/redo of a Draft→Core commit changes the authoritative
                # network and its persistent SLD companion state together.
                self._event_bus.publish(NetworkChanged(
                    operation=operation,
                    metadata=metadata,
                    correlation_id=command.correlation_id,
                    causation_id=command.causation_id,
                ))
            # The persistent SLD mutation is part of the same originating
            # Application transaction. This event is emitted only after the
            # transaction has committed (or an undo/redo journal has completed),
            # and therefore never advertises an uncommitted presentation state.
            self._event_bus.publish(SLDPresentationChanged(
                operation=operation,
                metadata={
                    **metadata,
                    "presentation_operation": "network_commit_draft",
                },
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            ))
            return
        if command.command_type in {"connectivity.create_junction", "connectivity.remove_junction"}:
            action = "create" if command.command_type == "connectivity.create_junction" else "remove"
            if operation == "undo":
                action = "remove" if action == "create" else "create"
            junction_id = str(metadata.get("junction_id") or command.payload.get("junction_id"))
            event_type = JunctionCreated if action == "create" else JunctionRemoved
            self._event_bus.publish(event_type(
                junction_id=junction_id, correlation_id=command.correlation_id,
                causation_id=command.causation_id, metadata=metadata,
            ))
            self._event_bus.publish(TopologyChanged(operation=operation, metadata=metadata,
                correlation_id=command.correlation_id, causation_id=command.causation_id))
            self._event_bus.publish(NetworkChanged(operation=operation, metadata=metadata,
                correlation_id=command.correlation_id, causation_id=command.causation_id))
        elif command.command_type in {"connectivity.create_simple_wire", "connectivity.remove_simple_wire"}:
            action = "create" if command.command_type.endswith("create_simple_wire") else "remove"
            if operation == "undo":
                action = "remove" if action == "create" else "create"
            event_type = SimpleWireConnectionCreated if action == "create" else SimpleWireConnectionRemoved
            endpoint_a = metadata.get("endpoint_a") or getattr(command, "payload", {}).get("endpoint_a")
            endpoint_b = metadata.get("endpoint_b") or getattr(command, "payload", {}).get("endpoint_b")
            if endpoint_a is None or endpoint_b is None:
                raise RuntimeError(
                    f"Simple Wire semantic event lacks endpoint snapshots for {metadata.get('connection_id')!r}."
                )
            if hasattr(endpoint_a, "to_mapping"):
                endpoint_a = endpoint_a.to_mapping()
            if hasattr(endpoint_b, "to_mapping"):
                endpoint_b = endpoint_b.to_mapping()
            self._event_bus.publish(event_type(
                connection_id=str(metadata.get("connection_id") or getattr(command, "payload", {}).get("connection_id")),
                endpoint_a=endpoint_a,
                endpoint_b=endpoint_b,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                metadata=metadata,
            ))
            self._event_bus.publish(TopologyChanged(operation=operation, metadata=metadata, correlation_id=command.correlation_id, causation_id=command.causation_id))
            self._event_bus.publish(NetworkChanged(operation=operation, metadata=metadata, correlation_id=command.correlation_id, causation_id=command.causation_id))
        elif command.command_type in {"model.connect_terminal", "model.disconnect_terminal", "model.reconnect_terminal"}:
            self._event_bus.publish(TopologyChanged(operation=operation, metadata=metadata, correlation_id=command.correlation_id, causation_id=command.causation_id))
            self._event_bus.publish(NetworkChanged(operation=operation, metadata=metadata, correlation_id=command.correlation_id, causation_id=command.causation_id))
        elif command.command_type.startswith("model."):
            self._publish_model_event(command, metadata, operation=operation); self._publish_network_changed(command, metadata)
        elif command.command_type.startswith("control."): self._publish_control_event(command, result, metadata, operation=operation)
        elif command.command_type.startswith("sld."):
            payload = dict(result.metadata)
            payload.update(metadata)
            self._event_bus.publish(SLDPresentationChanged(operation=operation, metadata=payload,
                                                          correlation_id=command.correlation_id,
                                                          causation_id=command.causation_id))

    def _publish_control_event(self, command: Command, result: ApplicationResult, metadata: dict[str, object], *, operation: str) -> None:
        command_type=command.command_type; payload=dict(result.metadata); payload.update(metadata); cid,caid=command.correlation_id,command.causation_id
        effective=command_type if operation!="undo" else {ADD_CONTROL_COMPONENT:REMOVE_CONTROL_COMPONENT,REMOVE_CONTROL_COMPONENT:ADD_CONTROL_COMPONENT,CONNECT_CONTROL_SIGNALS:DISCONNECT_CONTROL_SIGNALS,DISCONNECT_CONTROL_SIGNALS:CONNECT_CONTROL_SIGNALS,ADD_LADDER_RUNG:REMOVE_LADDER_RUNG,REMOVE_LADDER_RUNG:ADD_LADDER_RUNG,ADD_LOGIC_DEPENDENCY:REMOVE_LOGIC_DEPENDENCY,REMOVE_LOGIC_DEPENDENCY:ADD_LOGIC_DEPENDENCY,ADD_CONTROL_ACTION_BINDING:REMOVE_CONTROL_ACTION_BINDING,REMOVE_CONTROL_ACTION_BINDING:ADD_CONTROL_ACTION_BINDING,ADD_CONTROL_INTERLOCK:REMOVE_CONTROL_INTERLOCK,REMOVE_CONTROL_INTERLOCK:ADD_CONTROL_INTERLOCK,ADD_DYNAMIC_CONTROL_ASSOCIATION:REMOVE_DYNAMIC_CONTROL_ASSOCIATION,REMOVE_DYNAMIC_CONTROL_ASSOCIATION:ADD_DYNAMIC_CONTROL_ASSOCIATION}.get(command_type,command_type)
        if effective==ADD_CONTROL_COMPONENT: self._event_bus.publish(ControlComponentCreated(component_id=str(payload["component_id"]),component_type=str(payload.get("component_type","")),metadata=payload,correlation_id=cid,causation_id=caid))
        elif effective==REMOVE_CONTROL_COMPONENT: self._event_bus.publish(ControlComponentRemoved(component_id=str(payload["component_id"]),metadata=payload,correlation_id=cid,causation_id=caid))
        elif effective==CONNECT_CONTROL_SIGNALS: self._event_bus.publish(ControlConnectionCreated(source_id=str(command.payload["source_component"]),target_id=str(command.payload["target_component"]),metadata=payload,correlation_id=cid,causation_id=caid))
        elif effective==DISCONNECT_CONTROL_SIGNALS: self._event_bus.publish(ControlConnectionRemoved(source_id=str(command.payload["source_component"]),target_id=str(command.payload["target_component"]),metadata=payload,correlation_id=cid,causation_id=caid))
        elif command_type.startswith("control."): self._event_bus.publish(ControlProgramChanged(metadata={**payload,"command_type":command_type,"effective_event":effective},correlation_id=cid,causation_id=caid))

    def _publish_history_events(self, command: Command | None, result: ApplicationResult, *, operation: str) -> None:
        if command is not None: self._publish_semantic_events(command, result, operation=operation)

    def _publish_model_event(self, command: Command, metadata: dict[str, object], *, operation: str) -> None:
        action = self._action_from_command_type(command.command_type); element_type = self._element_type(command); element_id = self._element_id(command)
        if element_type is None or element_id is None: return
        effective_action = {"create": "delete", "delete": "create"}.get(action, action) if operation == "undo" else action
        if effective_action == "create":
            self._event_bus.publish(ElementCreated(
                element_id=element_id,
                element_type=element_type,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                metadata=metadata,
            ))
        elif effective_action == "delete":
            self._event_bus.publish(ElementRemoved(
                element_id=element_id,
                element_type=element_type,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                metadata=metadata,
            ))
        elif effective_action in {"update", "open", "close", "reset", "blow", "trip", "put_in_service", "take_out_of_service"}:
            self._event_bus.publish(ElementUpdated(
                element_id=element_id,
                element_type=element_type,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
                changes=metadata,
            ))

    def _publish_network_changed(self, command: Command, metadata: dict[str, object]) -> None:
        if not self._is_network_change_command(command): return
        operation = str(metadata.get("operation", "execute"))
        if self._is_topology_command(command):
            self._event_bus.publish(TopologyChanged(
                operation=operation,
                metadata=metadata,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            ))
        self._event_bus.publish(NetworkChanged(
            operation=operation,
            metadata=metadata,
            correlation_id=command.correlation_id,
            causation_id=command.causation_id,
        ))

    @classmethod
    def _is_network_change_command(cls, command: Command) -> bool:
        if not command.command_type.startswith("model."): return False
        if cls._is_topology_command(command): return True
        action = cls._action_from_command_type(command.command_type)
        if action not in {"create", "delete"}: return False
        return cls._element_type(command) in cls._NETWORK_ELEMENT_CREATE_DELETE_TYPES

    @classmethod
    def _is_topology_command(cls, command: Command) -> bool: return RevisionService.is_topology_command(command)

    @staticmethod
    def _action_from_command_type(command_type: str) -> str:
        action = command_type.rsplit(".", 1)[-1]
        for prefix, value in (("create_", "create"), ("delete_", "delete"), ("update_", "update")):
            if action.startswith(prefix): return value
        return action

    @staticmethod
    def _element_type(command: Command) -> str | None:
        payload = command.payload; value = payload.get("element_type") or payload.get("equipment_type")
        if value is None and command.command_type.startswith("model."):
            action = command.command_type.split(".", 1)[1]
            for prefix in ("create_", "update_", "delete_", "open_", "close_", "trip_", "put_", "take_", "blow_", "reset_"):
                if action.startswith(prefix): return action[len(prefix):].removesuffix("_in_service").removesuffix("_out_of_service")
        return str(value) if value is not None else None

    @staticmethod
    def _element_id(command: Command) -> str | None:
        payload = command.payload; value = payload.get("element_id") or payload.get("equipment_id") or payload.get("id")
        if value is None:
            for key in (
                "bus_id", "grid_id", "generator_id", "synchronous_machine_id",
                "load_id", "motor_id", "shunt_id", "reactor_id", "solar_id",
                "battery_id", "capacitor_id", "breaker_id", "switch_id",
                "disconnector_id", "fuse_id", "line_id", "transformer_id",
                "cable_id", "ct_id", "pt_id", "cvt_id", "relay_id",
            ):
                if key in payload: value = payload[key]; break
        return str(value) if value is not None else None

    def _require_read_service(self) -> None:
        if self._read_service is None: raise RuntimeError("Application read service is not configured.")
    def _require_protection_read_service(self) -> None:
        if self._protection_read_service is None: raise RuntimeError("Application protection read service is not configured.")
