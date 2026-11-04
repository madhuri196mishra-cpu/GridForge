# ============================================================
# File: core/application/application.py
# GridForge V2 — Headless Application Facade
# Author: Subhendu Mishra
# ============================================================

"""Stable public Application facade for commands, reads, events, history, project lifecycle, and studies."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from threading import RLock
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4

from core.control.context import ControlExecutionContext
from core.control.decision import ControlActionType, ControlDecision
from core.control.engine import ControlEngine
from core.model import EndpointReference, Terminal
from core.network import TopologyEndpointReference
from core.network.topology_snapshot import TopologySnapshot

from .command import Command
from .creation import CreationCommitIntent, CreationCommandPreparer, CreationContractRegistry
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
    SLDPresentationChanged, TopologyChanged, ProtectionChanged, ValidationChanged, DraftChanged, StudyCaseCreated,
    SimpleWireConnectionCreated, SimpleWireConnectionRemoved, JunctionCreated, JunctionRemoved,
)
from .project import ProjectContext, ProjectSnapshot
from .project_lifecycle import ProjectLifecycleService
from .project_transition import ProjectTransitionDecision, ProjectTransitionRequired
from .read_models import (
    ConfigurationFieldAssessmentReadModel, ElementReadModel,
    EquipmentConfigurationAssessmentReadModel, NetworkReadModel,
    ProtectionReadModel, RelayReadModel, SimpleWireReadModel,
)
from .read_service import ProtectionReadService, ReadService, StudyReadService
from .results import ApplicationResult
from core.persistence.network_serializer import deserialize_network, serialize_network
from .revision import ProjectRevision
from .revision_service import RevisionService
from .sld_command_handlers import SLDCommandHandlers
from .services.sld_service import SLDService
from .services.measurement_channel_service import MeasurementChannelService
from .protection_execution import (
    ProtectionExecutionResult,
    ProtectionExecutionService,
    ProtectionTargetUnavailableError,
    PostconditionStatus,
    ProtectionPostconditionVerification,
    StaleProtectionExecutionError,
)
from core.protection.context import ProtectionContext
from core.protection.runtime import ProtectionRuntime
from core.protection.directional.directional_relay import _create_authoritative_directional_snapshot
from core.protection.input_contracts import APPLICATION_MEASUREMENT_SNAPSHOT_AUTHORITY, validate_protection_input_contracts, validate_directional_context, validate_sample_representation, input_contract_for
from core.measurement.measurement_channel import MeasurementQuality, MeasurementValidity
from math import isfinite, atan2, degrees
from pathlib import Path
from .services.control_service import ControlApplicationService
from .services.validation_service import ValidationService
from .study import StudyCaseDefinition, StudyExecutionContext, StudyRequest, StudyResult, StudyService
from .protection_study import ProtectionStudyHandler
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
        self._measurement_lifecycle_service: ProjectLifecycleService | None = None
        self._configuration_lifecycle_service: ProjectLifecycleService | None = None
        self._configuration_assessments_project_id: str | None = None
        self._equipment_configuration_assessments: dict[str, EquipmentConfigurationAssessmentReadModel] = {}
        self._workspace_dirty_provider: Callable[[], bool] | None = None
        self._draft_network: Any | None = None
        self._execution_lock = RLock()
        self._commit_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gridforge-network-commit")
        self._commit_state_lock = RLock()
        self._commit_in_flight = False
        self._save_in_progress = False
        self._pending_async_commit_command: CommitNetworkCommand | None = None
        # Recovery artifacts live outside the project file and preserve the
        # Application-owned draft separately from authoritative Core state.
        self._command_manager.configure_recovery(
            snapshot_provider=self._recovery_snapshot,
            recovery_directory=Path.home() / ".gridforge" / "recovery",
        )
        self._creation_contract_registry = CreationContractRegistry()
        # Extension factories are durable across project-runtime replacement.
        # Each factory must bind handlers to the candidate CommandManager rather
        # than retaining handlers that capture the previous project's runtime.
        self._extension_handler_factories: dict[str, Callable[[CommandManager], Mapping[str, Any]]] = {}
        self._extension_handler_command_types: dict[str, tuple[str, ...]] = {}
        # Keep exact handler identities so teardown cannot remove a replacement
        # installed by another owner under a command type we originally owned.
        self._extension_handler_handlers: dict[str, dict[str, Any]] = {}
        self._revision_service = RevisionService()
        self._study_service = StudyService(self._event_bus)
        self._study_service.register("protection", ProtectionStudyHandler(self))
        self._study_cases: dict[UUID, StudyCaseDefinition] = {}
        self._study_result_archive: Any | None = None
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
    def integrity_state(self) -> str:
        """Expose the command/Core consistency state for a read-only degraded workspace."""
        return self._command_manager.integrity_state

    @property
    def is_read_only_degraded(self) -> bool:
        """Return whether mutations are blocked pending explicit project recovery."""
        return self._command_manager.integrity_state != "CLEAN"

    @property
    def recovery_record(self) -> Mapping[str, Any] | None:
        """Return recovery diagnostics without exposing mutable internal state."""
        return self._command_manager.recovery_record

    @property
    def draft_network(self) -> Any | None:
        return self._draft_network

    def set_draft_network(self, draft_network: Any | None) -> None:
        self._draft_network = draft_network

    def _recovery_snapshot(self) -> Mapping[str, Any]:
        """Return a JSON-serializable snapshot of volatile authoring state.

        This is diagnostic recovery data only: it never changes or replaces
        authoritative Core project/network state.
        """
        draft_network = self._draft_network
        return {
            "kind": "application-draft-network",
            "draft_network": None if draft_network is None else draft_network.to_dict(),
        }

    @property
    def creation_contract_registry(self) -> CreationContractRegistry:
        """Return the Application-owned registry of trusted creation contracts."""
        return self._creation_contract_registry

    def configure_creation_contracts(self, contracts: Mapping[str, Mapping[str, Any]]) -> None:
        """Inject validated catalogue contracts at the composition boundary."""
        self._creation_contract_registry.configure(contracts)

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
    def study_result_archive(self) -> Any | None:
        """Return the configured durable Study Result archive, when enabled."""
        return self._study_result_archive

    def configure_study_result_archive(self, archive: Any | None) -> None:
        """Attach the Persistence-owned archive to the Application study boundary."""
        if archive is None:
            self._study_service.set_result_persister(None)
            self._study_result_archive = None
            self._study_read_service.configure_archive(None)
            return
        persister = getattr(archive, "save", None)
        if not callable(persister) or not callable(getattr(archive, "load_all", None)):
            raise TypeError("archive must expose callable save() and load_all() operations.")
        self._study_service.set_result_persister(persister)
        self._study_read_service.configure_archive(archive)
        self._study_result_archive = archive

    @property
    def study_cases(self) -> tuple[StudyCaseDefinition, ...]:
        """Return runnable Study Cases scoped to the active project generation."""
        lifecycle = self._project_lifecycle
        if lifecycle is None:
            return ()
        context = lifecycle.context
        if context is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            return ()
        generation = lifecycle.activation_generation
        return tuple(
            case for case in self._study_cases.values()
            if case.project_id == context.project_id
            and case.activation_generation == generation
        )

    def create_study_case(
        self,
        *,
        study_type: str,
        configuration: Mapping[str, Any],
        display_name: str = "",
    ) -> StudyCaseDefinition:
        """Register a validated Study Case without executing its solver."""
        lifecycle = self.project_lifecycle
        context = lifecycle.context
        if context is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            raise RuntimeError("Cannot create a Study Case without a valid active project.")
        request = StudyRequest(
            study_id=uuid4(),
            project_id=context.project_id,
            activation_generation=lifecycle.activation_generation,
            source_revision=self.revision,
            study_type=study_type,
            configuration=configuration,
        )
        registered_types = self._study_service.registered_study_types
        if request.study_type not in registered_types:
            raise ValueError(
                f"No study handler is registered for {request.study_type!r}; "
                f"available study types: {', '.join(registered_types)}."
            )
        case = StudyCaseDefinition(
            study_id=request.study_id,
            project_id=request.project_id,
            activation_generation=request.activation_generation,
            source_revision=request.source_revision,
            study_type=request.study_type,
            configuration=request.configuration,
            display_name=display_name,
        )
        self._study_cases[case.study_id] = case
        self._event_bus.publish(StudyCaseCreated(metadata={
            "study_id": str(case.study_id),
            "study_type": case.study_type,
            "display_name": case.display_name,
            "project_id": case.project_id,
            "activation_generation": case.activation_generation,
        }))
        return case

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
            existing = self._study_cases.get(case.study_id)
            if (
                existing is not None
                and existing.project_id == case.project_id
                and existing.activation_generation == case.activation_generation
            ):
                # Preserve the revision at which an unchanged Study Case was
                # configured. Presentation/persistence edits do not change its
                # numerical input identity and must not rewrite provenance.
                if (
                    existing.study_type == case.study_type
                    and existing.configuration == case.configuration
                    and existing.source_revision.matches_study_state(request.source_revision)
                ):
                    case = replace(
                        case,
                        source_revision=existing.source_revision,
                        display_name=existing.display_name,
                    )
                else:
                    case = replace(case, display_name=existing.display_name)
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
        # Do not silently bless a configuration after electrical inputs change.
        if not case.source_revision.matches_study_state(self.revision):
            raise ValueError(
                "Study Case configuration is stale for the active electrical model/topology. "
                "Reconfigure or recreate the Study Case before execution."
            )
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
    def configure_workspace_dirty_provider(self, provider: Callable[[], bool]) -> None:
        """Register the workspace's existing dirty projection for transition protection."""
        if not callable(provider):
            raise TypeError("provider must be callable.")
        self._workspace_dirty_provider = provider

    def _has_persistent_workspace_changes(self) -> bool:
        provider = self._workspace_dirty_provider
        return bool(provider is not None and provider())

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
        evaluation_timestep: float | None = None,
        event_id: str | None = None,
        event_type: str | None = None,
        supervision: Mapping[str, Any] | None = None,
        network_state: TopologySnapshot | None = None,
    ) -> ProtectionExecutionResult:
        """Serialize one protection cycle and publish its read-side change event."""
        with self._execution_lock:
            result = self._evaluate_protection_cycle_unlocked(
                evaluation_time,
                action_resolver=action_resolver,
                evaluation_metadata=evaluation_metadata,
                evaluation_timestep=evaluation_timestep,
                event_id=event_id,
                event_type=event_type,
                supervision=supervision,
                network_state=network_state,
            )
            lifecycle = self.project_lifecycle
            project = lifecycle.context
            self._event_bus.publish(ProtectionChanged(
                operation="cycle_evaluated",
                metadata={
                    "project_id": None if project is None else str(project.project_id),
                    "activation_generation": lifecycle.activation_generation,
                    "evaluation_time": float(evaluation_time),
                    "decision_count": len(result.decisions),
                    "command_count": len(result.commands),
                    "outcome_statuses": tuple(item.status for item in result.outcomes),
                    "physical_acknowledgement": "unknown/unavailable",
                },
            ))
            return result

    def _on_measurement_sample_published(
        self,
        channel_id: str,
        sample_timestamp: float,
        trusted_evaluation_time: float,
    ) -> ProtectionExecutionResult | None:
        """Drive protection using trusted time, not the source sample's age-zero timestamp."""
        lifecycle = self._project_lifecycle
        if lifecycle is None or lifecycle.state != "ACTIVE" or not lifecycle.has_project:
            return None
        if (
            isinstance(trusted_evaluation_time, bool)
            or not isinstance(trusted_evaluation_time, (int, float))
            or not isfinite(trusted_evaluation_time)
        ):
            raise ValueError("Trusted protection evaluation time must be finite.")
        configuration_service = getattr(self, "protection_configuration_service", None)
        configuration = getattr(configuration_service, "configuration", None)
        if configuration is None:
            return None
        if not any(
            element.enabled and channel_id in element.input_channel_ids.values()
            for element in configuration.elements
        ):
            return None
        try:
            return self.evaluate_protection_cycle(float(trusted_evaluation_time))
        except Exception as exc:
            project = lifecycle.context
            self._event_bus.publish(ProtectionChanged(
                operation="cycle_failed",
                metadata={
                    "project_id": None if project is None else str(project.project_id),
                    "activation_generation": lifecycle.activation_generation,
                    "channel_id": channel_id,
                    "sample_timestamp": float(sample_timestamp),
                    "evaluation_time": float(trusted_evaluation_time),
                    "diagnostic": f"{type(exc).__name__}: {exc}",
                    "physical_acknowledgement": "unknown/unavailable",
                },
            ))
            return ProtectionExecutionResult(
                diagnostics=(f"Measurement-triggered protection cycle failed: {type(exc).__name__}: {exc}",)
            )

    def _evaluate_protection_cycle_unlocked(
        self,
        evaluation_time: float,
        *,
        action_resolver: Any | None = None,
        evaluation_metadata: Mapping[str, Any] | None = None,
        evaluation_timestep: float | None = None,
        event_id: str | None = None,
        event_type: str | None = None,
        supervision: Mapping[str, Any] | None = None,
        network_state: TopologySnapshot | None = None,
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
        if runtime.project_id != project.project_id:
            raise RuntimeError("Protection runtime project identity is stale for the active project.")
        if runtime.activation_generation != lifecycle.activation_generation:
            raise RuntimeError("Protection runtime activation generation is stale for the active project.")
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
        if service.network is not lifecycle.network:
            raise RuntimeError("Measurement channel registry is provisioned against a stale network.")

        channels = service.channels

        def runtime_uses_active_channel_objects(runtime_candidate: Any, active_channels: Mapping[str, Any]) -> bool:
            runtime_channels = runtime_candidate.channels
            return (
                runtime_channels.keys() == active_channels.keys()
                and all(runtime_channels[channel_id] is channel for channel_id, channel in active_channels.items())
            )

        if not runtime_uses_active_channel_objects(runtime, channels):
            raise RuntimeError(
                "Protection runtime channel objects are stale for the active measurement-channel registry."
            )

        # Capture authoritative identities for the complete evaluation-to-dispatch
        # interval. The action resolver is application-supplied and may re-enter
        # project activation; caller metadata is deliberately not provenance.
        captured_project = project
        captured_network = lifecycle.network
        captured_generation = lifecycle.activation_generation
        captured_runtime = runtime
        captured_configuration_service = configuration
        captured_configuration = active_configuration
        captured_channel_service = service

        def ensure_current_protection_provenance() -> None:
            current_lifecycle = self.project_lifecycle
            current_project = current_lifecycle.context
            current_runtime = getattr(self, "protection_runtime", None)
            current_configuration_service = getattr(self, "protection_configuration_service", None)
            current_channels = self.measurement_channel_service
            if (
                current_lifecycle.state != "ACTIVE"
                or not current_lifecycle.has_project
                or current_project is not captured_project
                or current_project is None
                or current_project.project_id != captured_project.project_id
                or current_lifecycle.network is not captured_network
                or current_lifecycle.activation_generation != captured_generation
                or current_runtime is not captured_runtime
                or current_configuration_service is not captured_configuration_service
                or current_configuration_service.configuration is not captured_configuration
                or current_runtime is None
                or current_runtime.network is not captured_network
                or current_runtime.project_id != captured_project.project_id
                or current_runtime.activation_generation != captured_generation
                or current_runtime.configuration is not captured_configuration
                or not current_runtime.configuration_matches_composition
                or current_channels is not captured_channel_service
                or current_channels.project_id != captured_project.project_id
                or current_channels.activation_generation != captured_generation
                or current_channels.network is not captured_network
                or not runtime_uses_active_channel_objects(current_runtime, current_channels.channels)
            ):
                raise StaleProtectionExecutionError(
                    "active project, network, activation generation, protection runtime/configuration, "
                    "or measurement-channel authority changed after evaluation."
                )

        if evaluation_metadata is not None and not isinstance(evaluation_metadata, Mapping):
            raise TypeError("evaluation_metadata must be a mapping of explicit protection evaluation inputs.")
        diagnostics: list[str] = []
        validated_sample_values: dict[tuple[str, str], tuple[Any, Any, float]] = {}
        validated_measurement_snapshots: dict[tuple[str, str], Mapping[str, Any]] = {}
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
                # Read the sample sequence around the complete validation
                # window. When a producer supplies a sequence, any change means
                # the fields cannot be treated as one coherent measurement.
                sample_sequence_before = getattr(channel, "sample_sequence", None)
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
                available = bool(getattr(channel, "available", False))
                usable = bool(getattr(channel, "is_usable", False))
                if not available:
                    diagnostics.append(f"{prefix}: measurement channel is unavailable.")
                    continue
                if not usable:
                    diagnostics.append(f"{prefix}: measurement channel is not usable.")
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
                sample_sequence_after = getattr(channel, "sample_sequence", None)
                sample_fields_stable = (
                    getattr(channel, "engineering_value", None) == value
                    and getattr(channel, "timestamp", None) == timestamp
                    and getattr(channel, "quality", None) is quality
                    and bool(getattr(channel, "available", False)) == available
                    and bool(getattr(channel, "is_usable", False)) == usable
                    and sample_sequence_before == sample_sequence_after
                )
                if not sample_fields_stable:
                    diagnostics.append(
                        f"{prefix}: measurement changed during validation; a coherent sample snapshot could not be established."
                    )
                    continue
                # Runtime composition records the exact objects it bound into
                # RelayInput instances. Compare by identity, not merely channel ID.
                if runtime.channels.get(channel_id) is not channel:
                    diagnostics.append(f"{prefix}: protection runtime retains a stale channel object.")
                    continue
                validated_sample_values[(element.element_id, input_name)] = (channel, value, timestamp)
                validated_measurement_snapshots[(element.element_id, input_name)] = MappingProxyType({
                    "channel_id": channel_id,
                    "channel_identity": id(channel),
                    "value": value,
                    "timestamp": timestamp,
                    "quality": quality,
                    "available": available,
                    "usable": usable,
                    "validity": validity,
                    "sample_sequence": sample_sequence_after,
                })

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
        # RelayInput bindings consume these immutable Application-validated
        # samples for this cycle instead of rereading mutable channels while
        # individual protection elements execute.
        snapshots_by_element: dict[str, Mapping[str, Any]] = {
            element.element_id: MappingProxyType({})
            for element in runtime.configuration.elements
            if element.enabled
        }
        for (element_id, input_name), snapshot in validated_measurement_snapshots.items():
            per_element = dict(snapshots_by_element.get(element_id, {}))
            per_element[input_name] = snapshot
            snapshots_by_element[element_id] = MappingProxyType(per_element)
        context_metadata["validated_measurement_snapshots_by_element"] = MappingProxyType(snapshots_by_element)
        context_metadata["validated_measurement_snapshot_authority"] = APPLICATION_MEASUREMENT_SNAPSHOT_AUTHORITY
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
        if network_state is not None and not isinstance(network_state, TopologySnapshot):
            raise TypeError("network_state must be a canonical TopologySnapshot or None.")
        context = ProtectionContext(
            time=evaluation_time,
            timestep=evaluation_timestep,
            event_id=event_id,
            event_type=event_type,
            network_state=network_state,
            supervision=supervision if supervision is not None else {},
            metadata=context_metadata,
        )
        decisions = runtime.system.evaluate(context)
        if action_resolver is None:
            def resolve_persisted_protection_action(decision: Any) -> ControlDecision | None:
                """Resolve only an explicit project-persisted function-to-target mapping."""
                try:
                    mapping = active_configuration.get(str(decision.element_id))
                except KeyError:
                    return None
                # The mapping is keyed by element identity, but function identity
                # is also part of the persisted protection-action contract. Fail
                # closed if configuration drift or malformed persisted data causes
                # a different function to inherit this target/action.
                decision_function_code = str(decision.function_code).strip().upper()
                configured_function_code = str(mapping.function_code).strip().upper()
                if not decision_function_code or configured_function_code != decision_function_code:
                    raise ValueError(
                        f"Protection action mapping function mismatch for element "
                        f"{decision.element_id!r}: decision={decision_function_code!r}, "
                        f"configured={configured_function_code!r}."
                    )
                if (
                    mapping.action_target_type is None
                    or mapping.action_target_id is None
                    or mapping.action_type is None
                ):
                    return None
                return ControlDecision(
                    control_id=f"protection:{mapping.element_id}",
                    action_type=ControlActionType(mapping.action_type),
                    target_equipment_id=mapping.action_target_id,
                    target_equipment_type=mapping.action_target_type,
                    reason=decision.reason or f"Protection function {mapping.function_code} requested action.",
                    simulation_time=evaluation_time,
                    triggered_by=mapping.element_id,
                )
            action_resolver = resolve_persisted_protection_action

        def observe_protection_command_state(control_decision: Any, command: Command, result: ApplicationResult) -> ProtectionPostconditionVerification:
            """Verify the action-specific postcondition in the captured Core network.

            The result is a Core-state observation, never field-device acknowledgement.
            """
            ensure_current_protection_provenance()
            target_type = str(getattr(control_decision, "target_equipment_type", "")).strip().lower()
            target_id = getattr(control_decision, "target_equipment_id", None)
            action = str(getattr(getattr(control_decision, "action_type", None), "value", getattr(control_decision, "action_type", ""))).strip().lower()
            command_id = str(command.command_id)

            def verification(status: PostconditionStatus, expected: str | None, observed: str | None, diagnostic: str) -> ProtectionPostconditionVerification:
                return ProtectionPostconditionVerification(
                    command_id=command_id,
                    target_equipment_type=target_type or "unknown",
                    target_equipment_id=target_id if isinstance(target_id, str) and target_id else "unknown",
                    action_type=action or "unknown",
                    status=status,
                    expected_state=expected,
                    observed_state=observed,
                    diagnostic=diagnostic,
                )

            target_types = {
                "breaker": "breaker",
                "switch": "switch",
                "disconnector": "disconnector",
                "fuse": "fuse",
                "motor": "motor",
            }
            core_type = target_types.get(target_type)
            if core_type is None or not isinstance(target_id, str) or not target_id.strip():
                return verification(PostconditionStatus.UNAVAILABLE_UNSUPPORTED, None, None, "unsupported or missing target identity")
            contracts = {
                ("breaker", "open"): ("closed", False),
                ("breaker", "close"): ("closed", True),
                # The Core Breaker model represents the physical switching
                # result of trip() as open (closed=False). This verifies the
                # commanded state change, but deliberately does not claim a
                # separate trip-latched/cause state that the model does not store.
                ("breaker", "trip"): ("closed", False),
                ("breaker", "put_in_service"): ("in_service", True),
                ("breaker", "take_out_of_service"): ("in_service", False),
                ("switch", "open"): ("closed", False),
                ("switch", "close"): ("closed", True),
                ("switch", "put_in_service"): ("in_service", True),
                ("switch", "take_out_of_service"): ("in_service", False),
                ("disconnector", "open"): ("closed", False),
                ("disconnector", "close"): ("closed", True),
                ("disconnector", "put_in_service"): ("in_service", True),
                ("disconnector", "take_out_of_service"): ("in_service", False),
                ("fuse", "blow"): ("blown", True),
                ("fuse", "trip"): ("blown", True),
                ("fuse", "reset"): ("blown", False),
                ("fuse", "put_in_service"): ("in_service", True),
                ("fuse", "take_out_of_service"): ("in_service", False),
                ("motor", "start"): ("running", True),
                ("motor", "stop"): ("running", False),
                ("motor", "put_in_service"): ("in_service", True),
                ("motor", "take_out_of_service"): ("in_service", False),
            }
            contract = contracts.get((target_type, action))
            if contract is None:
                return verification(PostconditionStatus.UNAVAILABLE_UNSUPPORTED, None, None, "no action-specific Core postcondition contract is defined")
            field_name, expected_value = contract
            try:
                equipment = captured_network.get_by_id(core_type, target_id)
            except (KeyError, ValueError):
                equipment = None
            if equipment is None:
                return verification(PostconditionStatus.UNAVAILABLE_UNSUPPORTED, f"{field_name}={expected_value!r}", None, "target not found in the captured active Core network")
            if not hasattr(equipment, field_name):
                return verification(PostconditionStatus.UNAVAILABLE_UNSUPPORTED, f"{field_name}={expected_value!r}", None, f"target model does not expose authoritative field {field_name!r}")
            observed_value = getattr(equipment, field_name)
            if not isinstance(observed_value, bool):
                return verification(PostconditionStatus.UNAVAILABLE_UNSUPPORTED, f"{field_name}={expected_value!r}", f"{field_name}={observed_value!r}", "authoritative postcondition field is not boolean")
            observed_state = f"{field_name}={observed_value!r}"
            expected_state = f"{field_name}={expected_value!r}"
            ensure_current_protection_provenance()
            if observed_value is expected_value:
                return verification(PostconditionStatus.MATCHED, expected_state, observed_state, "authoritative Core state matches the action-specific postcondition")
            return verification(PostconditionStatus.MISMATCHED, expected_state, observed_state, "authoritative Core state does not match the action-specific postcondition")

        def resolve_explicit_protection_action(decision: Any) -> Any | None:
            """Validate explicit or persisted action resolution against Core authority.

            Persisted mappings are resolved by protection-function identity;
            targets are never inferred from relay identity or presentation.
            """
            ensure_current_protection_provenance()
            resolved = action_resolver(decision)
            ensure_current_protection_provenance()
            if resolved is None:
                return None
            if not hasattr(resolved, "target_equipment_type") or not hasattr(resolved, "target_equipment_id") or not hasattr(resolved, "action_type"):
                raise TypeError("Explicit protection action resolver must return a typed ControlDecision or None.")
            target_type = str(resolved.target_equipment_type).strip().lower()
            target_id = resolved.target_equipment_id
            supported_target_types = {"breaker", "switch", "disconnector", "fuse", "motor"}
            if target_type not in supported_target_types or not isinstance(target_id, str) or not target_id.strip():
                raise ValueError("Explicit protection action has an unsupported target type or empty target identity.")
            try:
                target = captured_network.get_by_id(target_type, target_id)
            except (KeyError, ValueError):
                target = None
            if target is None:
                raise ProtectionTargetUnavailableError(
                    f"target {target_type}:{target_id} does not exist in the captured active Core network"
                )
            return resolved

        execution = ProtectionExecutionService(
            self.control_execution.dispatcher,
            action_resolver=resolve_explicit_protection_action,
            pre_dispatch_guard=ensure_current_protection_provenance,
            state_observer=observe_protection_command_state,
        )
        return execution.execute(decisions)


    def _evaluate_protection_scenario(
        self,
        *,
        scenario_id: str,
        samples: tuple[Any, ...],
        evaluation_time: float,
        evaluation_timestep: float | None = None,
        event_id: str | None = None,
        event_type: str | None = None,
        supervision: Mapping[str, Any] | None = None,
        evaluation_metadata: Mapping[str, Any] | None = None,
        network_state: TopologySnapshot,
        protection_runtime: Any | None = None,
    ) -> ProtectionExecutionResult:
        """Evaluate one detached study scenario through Application-authorized RelayInput snapshots.

        Scenario values are calculated study quantities, not live sensor
        telemetry. Their snapshot validity is established from complete
        solver/result provenance and binding correlation; sensor quality and
        acquisition timestamps are deliberately not fabricated.
        """
        from core.measurement.protection_scenario_measurement import ProtectionScenarioMeasurementSample
        from core.protection.residual_current import (
            normalize_residual_current_source,
            resolve_residual_current,
        )

        if not isinstance(scenario_id, str) or not scenario_id.strip():
            raise ValueError("scenario_id must be a non-empty string.")
        if not isinstance(samples, (tuple, list)):
            raise TypeError("samples must be a tuple or list of detached scenario measurement samples.")
        if any(not isinstance(item, ProtectionScenarioMeasurementSample) for item in samples):
            raise TypeError("samples must contain only ProtectionScenarioMeasurementSample values.")
        if len({item.binding_id for item in samples}) != len(samples):
            raise ValueError("A scenario may contain only one sample per measurement binding.")
        if isinstance(evaluation_time, bool):
            raise TypeError("evaluation_time must be a finite non-negative number.")
        evaluation_time = float(evaluation_time)
        if not isfinite(evaluation_time) or evaluation_time < 0.0:
            raise ValueError("evaluation_time must be finite and non-negative.")
        if not isinstance(network_state, TopologySnapshot):
            raise TypeError("network_state must be the captured TopologySnapshot for this scenario.")
        if evaluation_metadata is not None and not isinstance(evaluation_metadata, Mapping):
            raise TypeError("evaluation_metadata must be a mapping or None.")

        lifecycle = self.project_lifecycle
        project = lifecycle.context
        service = self.measurement_channel_service
        active_runtime = getattr(self, "protection_runtime", None)
        runtime = active_runtime if protection_runtime is None else protection_runtime
        configuration_service = getattr(self, "protection_configuration_service", None)
        if (
            project is None or not lifecycle.has_project or lifecycle.state != "ACTIVE"
            or runtime is None or configuration_service is None
        ):
            raise RuntimeError("Scenario protection evaluation requires an active project and composed protection runtime.")
        configuration = configuration_service.configuration
        if (
            configuration is None or runtime.configuration is not configuration
            or runtime.network is not lifecycle.network
            or runtime.project_id != project.project_id
            or runtime.activation_generation != lifecycle.activation_generation
            or not runtime.configuration_matches_composition
        ):
            raise RuntimeError("Scenario protection runtime/configuration provenance is stale.")
        if (
            service.project_id != project.project_id
            or service.activation_generation != lifecycle.activation_generation
            or service.network is not lifecycle.network
        ):
            raise RuntimeError("Scenario measurement-channel authority is stale.")
        if network_state.topology_revision != lifecycle.network.state.topology_revision:
            raise RuntimeError("Scenario topology snapshot does not match the active network revision.")

        channels = service.channels
        runtime_channels = runtime.channels
        if runtime_channels.keys() != channels.keys() or any(
            runtime_channels[channel_id] is not channel for channel_id, channel in channels.items()
        ):
            raise RuntimeError("Protection runtime retains stale MeasurementChannel objects.")

        diagnostics = list(validate_protection_input_contracts(configuration, channels))
        samples_by_binding = {item.binding_id: item for item in samples}
        bindings_by_input: dict[tuple[str, str], list[Any]] = {}
        bindings_by_id = {
            binding.binding_id: binding
            for binding in configuration.measurement_bindings
        }
        for binding in configuration.measurement_bindings:
            bindings_by_input.setdefault(
                (binding.protection_element_id, binding.relay_input_id), []
            ).append(binding)

        expected_samples: set[str] = set()
        snapshots_by_element: dict[str, Mapping[str, Any]] = {
            item.element_id: MappingProxyType({})
            for item in configuration.elements if item.enabled
        }
        for element_configuration in configuration.elements:
            if not element_configuration.enabled:
                continue
            element_snapshots: dict[str, Any] = {}
            for input_name, channel_id in element_configuration.input_channel_ids.items():
                key = (element_configuration.element_id, input_name)
                matching_bindings = bindings_by_input.get(key, ())
                prefix = (
                    f"scenario={scenario_id!r}, element={element_configuration.element_id!r}, "
                    f"input={input_name!r}"
                )
                if len(matching_bindings) != 1:
                    diagnostics.append(
                        f"{prefix}: exactly one canonical ProtectionMeasurementBinding is required; "
                        f"found {len(matching_bindings)}."
                    )
                    continue
                binding = matching_bindings[0]
                expected_samples.add(binding.binding_id)
                sample = samples_by_binding.get(binding.binding_id)
                if sample is None:
                    diagnostics.append(f"{prefix}, binding={binding.binding_id!r}: scenario sample is missing.")
                    continue
                channel = channels.get(channel_id)
                if channel is None or runtime_channels.get(channel_id) is not channel:
                    diagnostics.append(f"{prefix}, channel={channel_id!r}: authoritative runtime channel is unavailable.")
                    continue
                if (
                    sample.scenario_id != scenario_id
                    or sample.protection_element_id != element_configuration.element_id
                    or sample.relay_input_id != input_name
                    or sample.binding_id != binding.binding_id
                    or sample.channel_id != channel_id
                    or binding.channel_id != channel_id
                    or sample.instrument_id != binding.instrument_id
                    or binding.measurement_type.strip().lower() != "current"
                    or getattr(getattr(channel, "signal_type", None), "value", None) != "CURRENT"
                    or getattr(channel, "source_id", None) != binding.instrument_id
                ):
                    diagnostics.append(f"{prefix}: sample, binding, channel or physical CT identity does not agree.")
                    continue
                source_terminal = getattr(channel, "source_terminal", None)
                if (
                    source_terminal is None
                    or getattr(source_terminal, "equipment_id", None) != binding.instrument_id
                    or getattr(source_terminal, "terminal_role", None) not in {"S1", "S2"}
                ):
                    diagnostics.append(f"{prefix}: channel does not originate from the configured CT secondary terminal.")
                    continue
                try:
                    active_instrument = lifecycle.network.get_by_identity(binding.instrument_id)
                except (KeyError, TypeError, ValueError):
                    diagnostics.append(f"{prefix}: configured CT is absent from the active canonical Network.")
                    continue
                if getattr(channel, "source", None) is not active_instrument:
                    diagnostics.append(f"{prefix}: MeasurementChannel does not reference the active canonical CT object.")
                    continue
                try:
                    active_ratio = float(getattr(active_instrument, "ratio"))
                except (AttributeError, TypeError, ValueError, OverflowError):
                    active_ratio = float("nan")
                if (
                    not isfinite(active_ratio)
                    or active_ratio <= 0.0
                    or abs(active_ratio - binding.instrument_ratio) > max(1e-9, active_ratio * 1e-9)
                ):
                    diagnostics.append(f"{prefix}: active canonical CT ratio does not match the persisted measurement binding.")
                    continue
                active_polarity = getattr(
                    getattr(active_instrument, "polarity", None),
                    "value",
                    getattr(active_instrument, "polarity", None),
                )
                expected_ct_direction = {
                    "P1_P2": "P1_TO_P2",
                    "P2_P1": "P2_TO_P1",
                }.get(active_polarity)
                if expected_ct_direction is None:
                    diagnostics.append(f"{prefix}: active canonical CT has an unsupported primary polarity.")
                    continue
                channel_phase = getattr(getattr(channel, "phase", None), "value", None)
                if (
                    sample.phase_or_sequence != binding.phase_or_sequence
                    or sample.phase_or_sequence != channel_phase
                    or sample.quantity_key != binding.result_quantity
                    or sample.unit != getattr(channel, "unit", None)
                ):
                    diagnostics.append(
                        f"{prefix}: phase/sequence, result member or engineering unit does not match the binding/channel."
                    )
                    continue
                provenance = sample.provenance
                expected_provenance = {
                    "project_id": project.project_id,
                    "activation_generation": lifecycle.activation_generation,
                    "topology_revision": network_state.topology_revision,
                    "ct_topology_revision": network_state.topology_revision,
                    "ct_id": binding.instrument_id,
                    "branch_id": binding.source_equipment_id,
                    "branch_side": binding.electrical_side.strip().lower(),
                }
                if any(provenance.get(name) != value for name, value in expected_provenance.items()):
                    diagnostics.append(f"{prefix}: scenario sample provenance does not match the captured project/topology/binding.")
                    continue
                if provenance.get("ct_positive_current_direction") != expected_ct_direction:
                    diagnostics.append(f"{prefix}: detached CT polarity provenance does not match the active canonical CT.")
                    continue
                try:
                    ratio = float(provenance.get("instrument_ratio"))
                except (TypeError, ValueError, OverflowError):
                    ratio = float("nan")
                if (
                    not isfinite(ratio) or ratio <= 0.0
                    or abs(ratio - binding.instrument_ratio) > max(1e-9, ratio * 1e-9)
                ):
                    diagnostics.append(f"{prefix}: sample CT ratio does not match the persisted measurement binding.")
                    continue
                requirement = input_contract_for(configuration, element_configuration, input_name)
                if requirement is None:
                    diagnostics.append(f"{prefix}: no supported canonical protection input contract is available.")
                    continue
                try:
                    channel_scale = float(getattr(channel, "scale"))
                    channel_polarity = float(getattr(channel, "polarity"))
                    if not isfinite(channel_scale) or not isfinite(channel_polarity):
                        raise ValueError("channel scale/polarity must be finite.")
                    engineering_value = sample.value * channel_scale * channel_polarity
                except (AttributeError, TypeError, ValueError, OverflowError) as exc:
                    diagnostics.append(f"{prefix}: channel engineering scale/polarity is invalid: {exc}")
                    continue
                rejected = validate_sample_representation(engineering_value, requirement)
                if rejected is not None:
                    diagnostics.append(f"{prefix}: scenario engineering value violates the protection input contract: {rejected}")
                    continue
                element_snapshots[input_name] = MappingProxyType({
                    "channel_id": channel_id,
                    "channel_identity": id(channel),
                    "value": engineering_value,
                    "source_value": sample.value,
                    "channel_scale": channel_scale,
                    "channel_polarity": channel_polarity,
                    "timestamp": None,
                    # UNKNOWN is intentional: this is a calculated study
                    # quantity, not a GOOD-quality field measurement.
                    "quality": MeasurementQuality.UNKNOWN,
                    "available": True,
                    "usable": True,
                    "validity": MeasurementValidity.VALID,
                    "sample_sequence": None,
                    "sample_origin": "PROTECTION_STUDY_SCENARIO",
                    "scenario_id": scenario_id,
                    "binding_id": binding.binding_id,
                    "reference_basis_id": sample.reference_basis_id,
                    "provenance": provenance,
                })
            if element_configuration.function_code in {"50N", "51N"}:
                residual_configuration = element_configuration.residual_current_input
                if residual_configuration is None:
                    diagnostics.append(
                        f"scenario={scenario_id!r}, element={element_configuration.element_id!r}: explicit residual-current configuration is missing."
                    )
                else:
                    expected_sources = (
                        dict(residual_configuration.phase_binding_ids or {})
                        if residual_configuration.mode == "THREE_PHASE_SUM"
                        else {"N": residual_configuration.direct_binding_id}
                    )
                    normalized_sources = {}
                    source_instrument_ids: set[str] = set()
                    three_phase_terminal_identity: tuple[str, str] | None = None
                    for phase, binding_id in expected_sources.items():
                        prefix = (
                            f"scenario={scenario_id!r}, element={element_configuration.element_id!r}, "
                            f"residual_source={phase!r}, binding={binding_id!r}"
                        )
                        binding = bindings_by_id.get(binding_id)
                        expected_samples.add(binding_id)
                        if binding is None:
                            diagnostics.append(f"{prefix}: configured residual source binding is absent from the project configuration aggregate.")
                            continue
                        sample = samples_by_binding.get(binding_id)
                        if sample is None:
                            diagnostics.append(f"{prefix}: detached scenario sample is missing.")
                            continue
                        channel = channels.get(binding.channel_id)
                        if channel is None or runtime_channels.get(binding.channel_id) is not channel:
                            diagnostics.append(f"{prefix}: authoritative physical MeasurementChannel is unavailable.")
                            continue
                        if (
                            not binding.source_endpoint_reference.is_terminal
                            or binding.source_endpoint_reference.object_id != binding.source_equipment_id
                            or binding.protection_element_id != element_configuration.element_id
                            or binding.relay_input_id != "residual_current"
                            or sample.protection_element_id != element_configuration.element_id
                            or sample.relay_input_id != "residual_current"
                            or sample.binding_id != binding_id
                            or sample.channel_id != binding.channel_id
                            or sample.instrument_id != binding.instrument_id
                            or binding.measurement_type.strip().lower() != "current"
                            or getattr(getattr(channel, "signal_type", None), "value", None) != "CURRENT"
                            or getattr(channel, "source_id", None) != binding.instrument_id
                        ):
                            diagnostics.append(f"{prefix}: residual source, binding, sample, channel or CT identity does not agree.")
                            continue
                        sample_phase = sample.phase_or_sequence.strip().upper()
                        sample_phase_valid = (
                            sample_phase == phase
                            or (
                                residual_configuration.mode == "CBCT"
                                and sample_phase == "RESIDUAL"
                            )
                        )
                        if (
                            binding.phase_or_sequence.strip().upper() != phase
                            or not sample_phase_valid
                            or getattr(getattr(channel, "phase", None), "value", None) != phase
                            or sample.quantity_key != binding.result_quantity
                            or sample.unit != "A"
                            or getattr(channel, "unit", None) != "A"
                        ):
                            diagnostics.append(f"{prefix}: source phase/sequence, result member or ampere unit is inconsistent.")
                            continue
                        source_terminal = getattr(channel, "source_terminal", None)
                        if (
                            source_terminal is None
                            or getattr(source_terminal, "equipment_id", None) != binding.instrument_id
                            or getattr(source_terminal, "terminal_role", None) not in {"S1", "S2"}
                        ):
                            diagnostics.append(f"{prefix}: channel is not sourced from the configured CT secondary terminal.")
                            continue
                        try:
                            active_instrument = lifecycle.network.get_by_identity(binding.instrument_id)
                        except (KeyError, TypeError, ValueError):
                            diagnostics.append(f"{prefix}: configured CT is absent from the active canonical Network.")
                            continue
                        if (
                            getattr(active_instrument, "TYPE", None) != "CT"
                            or getattr(channel, "source", None) is not active_instrument
                            or getattr(active_instrument, "in_service", True) is not True
                        ):
                            diagnostics.append(f"{prefix}: active source is not an in-service canonical CT referenced by the channel.")
                            continue
                        source_instrument_ids.add(binding.instrument_id)
                        provenance = sample.provenance
                        if (
                            provenance.get("terminal_current_reference_direction") != "INTO_EQUIPMENT"
                            or provenance.get("current_units") != "per_unit"
                        ):
                            diagnostics.append(f"{prefix}: source sample lacks the required terminal-current reference direction or per-unit provenance.")
                            continue
                        try:
                            active_ratio = float(active_instrument.ratio)
                            channel_scale = float(channel.scale)
                            channel_polarity = float(channel.polarity)
                        except (AttributeError, TypeError, ValueError, OverflowError):
                            active_ratio = channel_scale = channel_polarity = float("nan")
                        if (
                            not isfinite(active_ratio) or active_ratio <= 0.0
                            or abs(active_ratio - binding.instrument_ratio) > max(1e-9, active_ratio * 1e-9)
                            or not isfinite(channel_scale) or not isfinite(channel_polarity)
                        ):
                            diagnostics.append(f"{prefix}: active CT ratio or channel scale/polarity is invalid or stale.")
                            continue
                        expected_source_provenance = {
                            "project_id": project.project_id,
                            "activation_generation": lifecycle.activation_generation,
                            "topology_revision": network_state.topology_revision,
                            "ct_topology_revision": network_state.topology_revision,
                            "ct_id": binding.instrument_id,
                            "equipment_id": binding.source_equipment_id,
                            "equipment_terminal_role": binding.source_endpoint_reference.terminal_role,
                            "instrument_ratio": binding.instrument_ratio,
                            "measurement_point_id": residual_configuration.measurement_point_id,
                        }
                        if provenance.get("ct_primary_side") not in {"P1_SIDE", "P2_SIDE"}:
                            diagnostics.append(f"{prefix}: CT association provenance does not identify exactly one primary side.")
                            continue
                        if any(provenance.get(key) != value for key, value in expected_source_provenance.items()):
                            diagnostics.append(f"{prefix}: CT terminal sample provenance does not match the active project, topology, binding or measurement point.")
                            continue
                        if residual_configuration.mode == "THREE_PHASE_SUM":
                            terminal_identity = (
                                str(provenance.get("equipment_id")),
                                str(provenance.get("equipment_terminal_role")),
                            )
                            if three_phase_terminal_identity is None:
                                three_phase_terminal_identity = terminal_identity
                            elif terminal_identity != three_phase_terminal_identity:
                                diagnostics.append(
                                    f"{prefix}: A/B/C residual sources do not monitor the same canonical equipment terminal."
                                )
                                continue
                        if residual_configuration.mode in {"NEUTRAL_CT", "CBCT"}:
                            expected_direct_phase = "N" if residual_configuration.mode == "NEUTRAL_CT" else "RESIDUAL"
                            if (
                                binding.metadata.get("residual_source_mode") != residual_configuration.mode
                                or provenance.get("residual_source_mode") != residual_configuration.mode
                                or phase != "N"
                                or sample_phase != expected_direct_phase
                            ):
                                diagnostics.append(f"{prefix}: direct Neutral CT/CBCT sample must declare its configured mode and correct derived phase.")
                                continue
                        elif binding.metadata.get("residual_source_mode") is not None:
                            diagnostics.append(f"{prefix}: three-phase sum source cannot declare a direct neutral/CBCT mode.")
                            continue
                        expected_direction = {
                            "P1_P2": "P1_TO_P2",
                            "P2_P1": "P2_TO_P1",
                        }.get(getattr(getattr(active_instrument, "polarity", None), "value", getattr(active_instrument, "polarity", None)))
                        if expected_direction is None or provenance.get("ct_positive_current_direction") != expected_direction:
                            diagnostics.append(f"{prefix}: CT physical polarity provenance does not match the active CT.")
                            continue
                        normalized_sources[binding_id] = normalize_residual_current_source(
                            sample,
                            measurement_point_id=residual_configuration.measurement_point_id,
                            instrument_ratio=active_ratio,
                            channel_scale=channel_scale,
                            channel_polarity=channel_polarity,
                        )
                    if residual_configuration.mode == "THREE_PHASE_SUM" and len(source_instrument_ids) != 3:
                        diagnostics.append(
                            f"scenario={scenario_id!r}, element={element_configuration.element_id!r}: A/B/C residual summation requires three distinct in-service CT instruments."
                        )
                    if len(normalized_sources) == len(expected_sources) and (
                        residual_configuration.mode != "THREE_PHASE_SUM" or len(source_instrument_ids) == 3
                    ):
                        try:
                            residual_sample = resolve_residual_current(
                                residual_configuration,
                                normalized_sources,
                            )
                            relay_input = runtime.system.get_element(
                                element_configuration.element_id
                            ).function.get_input("residual_current")
                            derived_source = relay_input.channel
                            element_snapshots["residual_current"] = MappingProxyType({
                                "channel_id": derived_source.id,
                                "channel_identity": id(derived_source),
                                "input_kind": "DERIVED_RESIDUAL_CURRENT",
                                "value": residual_sample.value,
                                "source_value": residual_sample.value,
                                "timestamp": None,
                                "quality": MeasurementQuality.UNKNOWN,
                                "available": True,
                                "usable": True,
                                "validity": MeasurementValidity.VALID,
                                "sample_sequence": None,
                                "sample_origin": "PROTECTION_STUDY_SCENARIO",
                                "scenario_id": scenario_id,
                                "binding_id": None,
                                "source_binding_ids": residual_sample.source_binding_ids,
                                "source_mode": residual_sample.source_mode,
                                "measurement_point_id": residual_sample.measurement_point_id,
                                "reference_basis_id": residual_sample.reference_basis_id,
                                "provenance": MappingProxyType({
                                    "project_id": project.project_id,
                                    "activation_generation": lifecycle.activation_generation,
                                    "topology_revision": residual_sample.topology_revision,
                                    "scenario_id": scenario_id,
                                    "measurement_point_id": residual_sample.measurement_point_id,
                                    "source_mode": residual_sample.source_mode,
                                    "source_binding_ids": residual_sample.source_binding_ids,
                                    "reference_basis_id": residual_sample.reference_basis_id,
                                    "primary_current_basis": "PRIMARY_AMPS",
                                }),
                            })
                        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                            diagnostics.append(
                                f"scenario={scenario_id!r}, element={element_configuration.element_id!r}: residual-current composition failed closed: {exc}"
                            )
            snapshots_by_element[element_configuration.element_id] = MappingProxyType(element_snapshots)

        unexpected = set(samples_by_binding) - expected_samples
        if unexpected:
            diagnostics.append(
                "Scenario supplied samples for bindings that are not required by enabled protection inputs: "
                + ", ".join(sorted(unexpected))
            )
        if diagnostics:
            return ProtectionExecutionResult(diagnostics=tuple(diagnostics))

        context_metadata = dict(evaluation_metadata or {})
        context_metadata.update({
            "project_id": project.project_id,
            "activation_generation": lifecycle.activation_generation,
            "study_type": "protection",
            "scenario_id": scenario_id,
            "scenario_measurement_snapshot_kind": "CALCULATED_FAULT_SCENARIO",
            "validated_measurement_snapshots_by_element": MappingProxyType(snapshots_by_element),
            "validated_measurement_snapshot_authority": APPLICATION_MEASUREMENT_SNAPSHOT_AUTHORITY,
        })
        context = ProtectionContext(
            time=evaluation_time,
            timestep=evaluation_timestep,
            event_id=event_id,
            event_type=event_type,
            network_state=network_state,
            supervision=supervision if supervision is not None else {},
            metadata=context_metadata,
        )
        decisions = runtime.system.evaluate(context)
        current_lifecycle = self.project_lifecycle
        if (
            current_lifecycle.state != "ACTIVE"
            or current_lifecycle.context is not project
            or current_lifecycle.network is not lifecycle.network
            or current_lifecycle.activation_generation != lifecycle.activation_generation
            or getattr(self, "protection_runtime", None) is not active_runtime
            or (protection_runtime is None and active_runtime is not runtime)
            or configuration_service.configuration is not configuration
            or not runtime.configuration_matches_composition
            or self.measurement_channel_service is not service
        ):
            raise StaleProtectionExecutionError(
                "Project, topology, protection runtime/configuration or measurement authority changed during scenario evaluation."
            )
        return ProtectionExecutionResult(decisions=decisions)

    def _register_sld_handlers(self, service: SLDService) -> None:
        for command_type, handler in SLDCommandHandlers(service).handlers().items():
            self._command_manager.register_handler(command_type, handler)

    def attach_project_lifecycle(self, service: ProjectLifecycleService) -> None:
        if not isinstance(service, ProjectLifecycleService): raise TypeError("service must be a ProjectLifecycleService.")
        if self._project_lifecycle is not None and self._project_lifecycle is not service: raise RuntimeError("Application project lifecycle is already configured.")
        self._project_lifecycle = service
        if self._configuration_lifecycle_service is not service:
            def activate_configuration_assessments(context: ProjectContext | None, loaded: Any, network: Any, generation: int):
                del network, generation
                previous_project_id = self._configuration_assessments_project_id
                previous_assessments = self._equipment_configuration_assessments
                persisted = (
                    getattr(loaded, "configuration_assessments", None)
                    if context is not None and loaded is not None else None
                )
                if persisted is None:
                    candidate = {}
                elif isinstance(persisted, Mapping) and all(
                    isinstance(value, EquipmentConfigurationAssessmentReadModel)
                    for value in persisted.values()
                ):
                    candidate = dict(persisted)
                else:
                    raise TypeError("Loaded configuration assessments must be a mapping of typed Application read models.")
                self._configuration_assessments_project_id = context.project_id if context is not None else None
                self._equipment_configuration_assessments = candidate

                def rollback_configuration_assessments() -> None:
                    self._configuration_assessments_project_id = previous_project_id
                    self._equipment_configuration_assessments = previous_assessments

                return rollback_configuration_assessments

            service.compose_project_state_activator(activate_configuration_assessments)
            self._configuration_lifecycle_service = service
        if self._measurement_lifecycle_service is not service and self._measurement_channel_service is not None:
            measurement_service = self._measurement_channel_service

            def activate_measurement_channels(context: ProjectContext | None, loaded: Any, network: Any, generation: int):
                definitions = loaded.measurement_definitions if context is not None and loaded is not None else ()
                return measurement_service.activate(context, network, definitions, generation)

            service.compose_project_state_activator(activate_measurement_channels)
            measurement_service.bind_sample_published_handler(
                self._on_measurement_sample_published
            )
            self._measurement_lifecycle_service = service
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

    def _prepare_project_transition(
        self,
        decision: ProjectTransitionDecision | str | None,
        *,
        discard_before_transition: bool = True,
    ) -> bool:
        if not self.is_dirty and not self._has_persistent_workspace_changes():
            return True
        normalized = self._coerce_transition_decision(decision)
        if normalized is None:
            raise ProjectTransitionRequired(
                "The active project is dirty; the transition requires SAVE, DISCARD, or CANCEL."
            )
        if normalized is ProjectTransitionDecision.CANCEL:
            return False
        if normalized is ProjectTransitionDecision.SAVE:
            self.save_project()
            if self.is_dirty or self._has_persistent_workspace_changes():
                raise RuntimeError(
                    "Project save completed but persistent project or workspace content remains dirty; transition aborted."
                )
            return True
        if normalized is ProjectTransitionDecision.DISCARD:
            # Replacement/close transitions discard by replacing the outgoing
            # state transactionally. Reloading the persisted package first
            # would mutate the outgoing project before the replacement commits.
            if discard_before_transition:
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

        # Lifecycle activation has committed. The outgoing project's history
        # must never be replayed against the newly authoritative network.
        # A history-clear failure must not skip revision/validation cleanup:
        # the lifecycle transition has already committed and cannot be rolled
        # back by this coordinator.
        transition_error: Exception | None = None
        try:
            self._command_manager.clear_history()
        except Exception as exc:
            transition_error = exc

        self._revision_service.reset_for_project()
        if self._validation_service is not None:
            self._validation_service.invalidate()
            try:
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
            except Exception as exc:
                # Preserve the first failure while still completing all
                # mandatory post-activation state reconciliation.
                if transition_error is None:
                    transition_error = exc

        if transition_error is not None:
            raise transition_error
        return result

    def discard_project_changes(self) -> ProjectContext:
        """Discard active-project changes through the existing lifecycle transaction."""
        self._study_service.ensure_no_active_studies()
        return self._run_project_transition(self.project_lifecycle.discard_project_changes)

    def new_project(self, name: str = "Untitled Project", *, project_id: str | None = None,
                    decision: ProjectTransitionDecision | str | None = None) -> ProjectContext:
        self._study_service.ensure_no_active_studies()
        if not self._prepare_project_transition(decision, discard_before_transition=False):
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
        if not self._prepare_project_transition(decision, discard_before_transition=False):
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

    def _save_project_scoped(self, path: str | None = None, *, save_as: bool = False) -> ProjectContext:
        """Serialize persistence against all Application-owned network mutations."""
        with self._execution_lock:
            with self._commit_state_lock:
                if self._commit_in_flight:
                    raise RuntimeError("A DraftNetwork commit is in progress; retry project save after it completes.")
                if self._save_in_progress:
                    raise RuntimeError("A project save is already in progress.")
                self._save_in_progress = True
            try:
                return self._save_project_scoped_locked(path, save_as=save_as)
            finally:
                with self._commit_state_lock:
                    self._save_in_progress = False

    def _save_project_scoped_locked(self, path: str | None = None, *, save_as: bool = False) -> ProjectContext:
        """Persist one captured project activation without contaminating a later activation.

        The lifecycle saver is synchronous today, but serializers/savers may call
        application code. Bind the revision baseline and success event to the
        project activation that entered the save; an activation change makes the
        comparison ambiguous and must never clean the newly active project.
        """
        lifecycle = self.project_lifecycle
        starting_context = lifecycle.context
        if starting_context is None:
            raise RuntimeError("Cannot save without an active project.")
        starting_project_id = starting_context.project_id
        starting_generation = lifecycle.activation_generation
        revision_snapshot = self._revision_service.revision

        context = (
            lifecycle.save_project_as(path) if save_as
            else lifecycle.save_project(path)
        )

        # Do not advance the persisted baseline if a re-entrant callback changed
        # the project while the synchronous saver was running.
        if self._revision_service.revision != revision_snapshot:
            raise RuntimeError(
                "Project revision changed during save; persisted revision baseline "
                "and ProjectSaved event were not updated."
            )

        if (
            lifecycle.context is None
            or lifecycle.context.project_id != starting_project_id
            or lifecycle.activation_generation != starting_generation
            or context.project_id != starting_project_id
        ):
            raise RuntimeError(
                "Project activation changed during save; persisted revision baseline "
                "and ProjectSaved event were not updated."
            )

        self._revision_service.mark_persisted(revision_snapshot)
        self._event_bus.publish(ProjectSaved(metadata={
            "project_id": starting_project_id,
            "path": str(context.path) if context.path else None,
            "activation_generation": starting_generation,
        }))
        return context

    def save_project(self, path: str | None = None) -> ProjectContext:
        return self._save_project_scoped(path)

    def save_project_as(self, path: str) -> ProjectContext:
        return self._save_project_scoped(path, save_as=True)

    def close_project(self, *, decision: ProjectTransitionDecision | str | None = None) -> ProjectContext | None:
        self._study_service.ensure_no_active_studies()
        if not self._prepare_project_transition(decision, discard_before_transition=False):
            return self.project_lifecycle.context
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
        """Capture a detached snapshot only if project scope and revisions stay stable."""
        lifecycle = self.project_lifecycle
        context = lifecycle.context
        if context is None or not lifecycle.has_project or lifecycle.state != "ACTIVE":
            raise RuntimeError("Cannot capture a study snapshot without an active project.")
        project_id = context.project_id
        activation_generation = lifecycle.activation_generation
        revision_before = self.revision
        active_network = lifecycle.network
        network_snapshot = deserialize_network(serialize_network(active_network))
        # Project scope and activation generation are runtime provenance, not
        # persisted Network engineering state. Restore them on the detached
        # study copy before rebuilding its TopologySnapshot so phase-domain
        # preparation can verify its provenance against the captured project.
        network_snapshot.project_id = project_id
        network_snapshot.activation_generation = activation_generation
        # Canonical persistence omits runtime topology revision counters. A
        # detached study snapshot must retain the exact active Network revision;
        # reconstruction can replay attachment mutations and change its counter.
        network_snapshot.state.topology_revision = active_network.state.topology_revision
        network_snapshot.rebuild_topology()
        dynamic_models = tuple(getattr(getattr(self, "dynamic_models", None), "snapshot", lambda: ())())
        revision_after = self.revision
        current_context = lifecycle.context
        if (
            current_context is None
            or current_context.project_id != project_id
            or lifecycle.activation_generation != activation_generation
            or not lifecycle.has_project
            or lifecycle.state != "ACTIVE"
        ):
            raise RuntimeError("Project activation changed while capturing the study snapshot.")
        if revision_before != revision_after:
            raise RuntimeError("Project revision changed while capturing the study snapshot; retry study execution.")
        return ProjectSnapshot(
            project_id=project_id,
            activation_generation=activation_generation,
            revision=revision_after,
            network=network_snapshot,
            dynamic_models=dynamic_models,
        )

    def execute_study(self, request: StudyRequest) -> StudyResult:
        if not isinstance(request, StudyRequest):
            raise TypeError("request must be a StudyRequest.")
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
        # The canonical topology and detached project snapshot are captured
        # through separate boundaries. Refuse to pair them if a mutation landed
        # between those captures or if the detached network carries a different
        # topology revision than the StudyRequest.
        if self.revision != request.source_revision:
            raise ValueError("Project revision changed while preparing the study snapshot; retry study execution.")
        if (
            project_snapshot.project_id != request.project_id
            or project_snapshot.activation_generation != request.activation_generation
            or project_snapshot.revision != request.source_revision
        ):
            raise ValueError("Detached ProjectSnapshot provenance does not match StudyRequest.")
        detached_network = project_snapshot.network
        detached_state = getattr(detached_network, "state", None)
        detached_topology_revision = getattr(detached_state, "topology_revision", None)
        if detached_topology_revision != request.source_revision.topology_revision:
            raise ValueError("Detached network topology revision does not match StudyRequest.")
        execution_context = StudyExecutionContext(
            project_id=request.project_id,
            activation_generation=request.activation_generation,
            source_revision=request.source_revision,
            topology_snapshot=snapshot,
            project_snapshot=project_snapshot,
        )
        # Only register a runnable Study Case after the request has passed
        # project-generation, revision, validation, and topology provenance gates.
        self._remember_study_case(request)
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

    def read_archived_study_results(
        self, *, study_type: str | None = None, status: str | None = None,
        saved_after: str | None = None, saved_before: str | None = None,
        search: str | None = None, activation_generation: int | None = None,
        study_id: UUID | None = None,
    ) -> tuple[Any, ...]:
        """Search durable executions for the active project, including older generations."""
        context = self.project_lifecycle.context
        if context is None or not self.project_lifecycle.has_project or self.project_lifecycle.state != "ACTIVE":
            return ()
        return self._study_read_service.archived_results(
            project_id=context.project_id,
            activation_generation=activation_generation,
            study_id=study_id,
            study_type=study_type,
            status=status,
            saved_after=saved_after,
            saved_before=saved_before,
            search=search,
        )

    def read_archived_study_result(self, record_id: UUID, *, activation_generation: int | None = None) -> Any:
        """Load one durable execution record within the active project identity."""
        context = self.project_lifecycle.context
        if context is None or not self.project_lifecycle.has_project or self.project_lifecycle.state != "ACTIVE":
            raise RuntimeError("An active project is required to read archived Study Results.")
        return self._study_read_service.archived_result(
            record_id, project_id=context.project_id,
            activation_generation=activation_generation,
        )

    def compare_archived_study_results(self, left_record_id: UUID, right_record_id: UUID) -> Any:
        """Compare two archived executions without silently mixing project/study types."""
        context = self.project_lifecycle.context
        if context is None or not self.project_lifecycle.has_project or self.project_lifecycle.state != "ACTIVE":
            raise RuntimeError("An active project is required to compare archived Study Results.")
        return self._study_read_service.compare_archived_results(
            left_record_id, right_record_id, project_id=context.project_id,
        )

    def verify_archived_study_results(self, *, activation_generation: int | None = None) -> Any:
        """Verify active-project archive records without modifying or deleting them."""
        context = self.project_lifecycle.context
        if context is None or not self.project_lifecycle.has_project or self.project_lifecycle.state != "ACTIVE":
            raise RuntimeError("An active project is required to verify archived Study Results.")
        return self._study_read_service.verify_archived_results(
            project_id=context.project_id, activation_generation=activation_generation,
        )

    def study_result(self, study_id, *, project_id: str, activation_generation: int) -> StudyResult | None:
        """Compatibility-only Application lookup retained for non-presentation callers."""
        return self._study_service.get_result(study_id, project_id=project_id, activation_generation=activation_generation)

    def cancel_study(self, study_id, *, project_id: str, activation_generation: int) -> bool:
        return self._study_service.cancel(study_id, project_id=project_id, activation_generation=activation_generation)

    @staticmethod
    def _invoke_extension_handler_factory(
        extension_id: str,
        factory: Callable[[CommandManager], Mapping[str, Any]],
        command_manager: CommandManager,
    ) -> Mapping[str, Any]:
        """Invoke a pure handler factory and reject command-registry side effects."""
        before = command_manager.handler_registry_snapshot()
        before_pre_commit_hook = command_manager.pre_commit_hook
        factory_error: BaseException | None = None
        handlers: Mapping[str, Any] | None = None
        try:
            factory_result = factory(command_manager)
            # Materialize custom/lazy mappings inside the guarded window so
            # iteration-time side effects are detected and the validated
            # handlers cannot change between validation and installation.
            handlers = (
                dict(factory_result)
                if isinstance(factory_result, Mapping)
                else factory_result
            )
        except BaseException as exc:
            factory_error = exc

        # The verification read is part of the guarded operation too: an
        # overridden snapshot/property accessor may fail after the factory has
        # already mutated the manager. In that case, restore both captured
        # composition components because the extent of mutation is unknown.
        verification_error: BaseException | None = None
        try:
            after = command_manager.handler_registry_snapshot()
            after_pre_commit_hook = command_manager.pre_commit_hook
        except BaseException as exc:
            verification_error = exc
            after = None
            after_pre_commit_hook = None

        registry_changed = (
            True
            if verification_error is not None
            else (
                before.keys() != after.keys()
                or any(before[key] is not after[key] for key in before.keys() & after.keys())
            )
        )
        hook_changed = (
            True
            if verification_error is not None
            else after_pre_commit_hook is not before_pre_commit_hook
        )

        restoration_errors: list[tuple[str, BaseException]] = []
        if registry_changed:
            # Restore additions, removals, replacements, and the original order
            # before surfacing the factory contract violation.
            try:
                command_manager.restore_handler_registry(before)
            except BaseException as exc:
                restoration_errors.append(("handler registry", exc))
        if hook_changed:
            try:
                command_manager.set_pre_commit_hook(before_pre_commit_hook)
            except BaseException as exc:
                restoration_errors.append(("pre-commit hook", exc))

        if restoration_errors:
            failed_parts = ", ".join(part for part, _ in restoration_errors)
            failure_details = "; ".join(
                f"{part}: {error!r}" for part, error in restoration_errors
            )
            factory_details = (
                f" Original factory exception: {factory_error!r}."
                if factory_error is not None
                else ""
            )
            verification_details = (
                f" Verification exception: {verification_error!r}."
                if verification_error is not None
                else ""
            )
            raise RuntimeError(
                f"Extension handler factory {extension_id!r} changed or may have "
                f"changed CommandManager composition state, and restoration failed "
                f"for {failed_parts}. The manager may remain corrupted. "
                f"Restoration errors: {failure_details}.{verification_details}"
                f"{factory_details}"
            ) from restoration_errors[0][1]

        if verification_error is not None:
            factory_details = (
                f" Original factory exception: {factory_error!r}."
                if factory_error is not None
                else ""
            )
            raise RuntimeError(
                f"Could not verify CommandManager composition after extension "
                f"factory {extension_id!r}; the captured registry and pre-commit "
                f"hook were restored, but the factory side effects could not be "
                f"reliably classified. Verification exception: {verification_error!r}."
                f"{factory_details}"
            ) from verification_error

        if factory_error is not None:
            raise factory_error
        if registry_changed or hook_changed:
            changed_state = []
            if registry_changed:
                changed_state.append("handler registry")
            if hook_changed:
                changed_state.append("pre-commit hook")
            raise RuntimeError(
                f"Extension handler factory {extension_id!r} mutated the "
                f"CommandManager {' and '.join(changed_state)}; the original "
                "composition state was restored."
            )
        # Let the standard handler validator provide the specific type error
        # if a factory returns None or another non-mapping value.
        return handlers  # type: ignore[return-value]

    def register_extension_handler_factory(
        self,
        extension_id: str,
        factory: Callable[[CommandManager], Mapping[str, Any]],
    ) -> None:
        """Register extension handlers that are rebuilt for every project runtime.

        The factory receives the candidate CommandManager and must return a
        mapping of command type to handler. It should construct project-bound
        adapters for that manager; it must not mutate Core or retain a handler
        bound to a previous project's runtime. Registration is atomic: any
        collision or factory error leaves the active command registry unchanged.
        """
        if not isinstance(extension_id, str) or not extension_id.strip():
            raise ValueError("extension_id must be a non-empty string.")
        extension_id = extension_id.strip()
        if not callable(factory):
            raise TypeError("extension handler factory must be callable.")
        if extension_id in self._extension_handler_factories:
            raise ValueError(f"Extension handler factory already registered: {extension_id!r}.")

        handlers = self._invoke_extension_handler_factory(extension_id, factory, self._command_manager)
        self._validate_extension_handlers(extension_id, handlers, self._command_manager)
        # Snapshot before installation so a manager override that mutates then
        # raises cannot leave an untracked handler behind.
        original_registry = self._command_manager.handler_registry_snapshot()
        original_pre_commit_hook = self._command_manager.pre_commit_hook
        installed: list[str] = []
        try:
            for command_type, handler in handlers.items():
                self._command_manager.register_handler(command_type, handler)
                installed.append(command_type)
        except BaseException as registration_error:
            rollback_errors: list[tuple[str, BaseException]] = []
            try:
                self._command_manager.restore_handler_registry(original_registry)
            except BaseException as rollback_error:
                rollback_errors.append(("handler registry", rollback_error))
            try:
                self._command_manager.set_pre_commit_hook(original_pre_commit_hook)
            except BaseException as rollback_error:
                rollback_errors.append(("pre-commit hook", rollback_error))
            if rollback_errors:
                failed_parts = ", ".join(part for part, _ in rollback_errors)
                details = "; ".join(
                    f"{part}: {error!r}" for part, error in rollback_errors
                )
                raise RuntimeError(
                    f"Extension handler registration for {extension_id!r} failed "
                    f"({registration_error!r}) and rollback failed for "
                    f"{failed_parts} ({details}); the active CommandManager may "
                    "remain corrupted."
                ) from rollback_errors[0][1]
            raise
        self._extension_handler_factories[extension_id] = factory
        self._extension_handler_command_types[extension_id] = tuple(installed)
        self._extension_handler_handlers[extension_id] = dict(handlers)

    def unregister_extension_handler_factory(
        self,
        extension_id: str,
        *,
        expected_factory: Callable[[CommandManager], Mapping[str, Any]] | None = None,
    ) -> bool:
        """Unregister an extension, optionally requiring ownership of its registration.

        The expected_factory guard prevents stale teardown callbacks from
        removing a later registration that reused the same extension ID.
        """
        if not isinstance(extension_id, str) or not extension_id.strip():
            raise ValueError("extension_id must be a non-empty string.")
        extension_id = extension_id.strip()
        current_factory = self._extension_handler_factories.get(extension_id)
        if current_factory is None:
            return False
        if expected_factory is not None and current_factory is not expected_factory:
            return False
        command_types = self._extension_handler_command_types.get(extension_id, ())
        expected_handlers = self._extension_handler_handlers.get(extension_id)
        if expected_handlers is None:
            raise RuntimeError(
                f"Extension {extension_id!r} has no handler-identity ownership record; "
                "refusing unsafe teardown."
            )
        current_registry = self._command_manager.handler_registry_snapshot()
        conflicts = [
            command_type for command_type in command_types
            if command_type not in expected_handlers
            or current_registry.get(command_type) is not expected_handlers.get(command_type)
        ]
        metadata_drift = set(command_types) != set(expected_handlers)
        if conflicts or metadata_drift:
            details = ", ".join(repr(command_type) for command_type in conflicts) or "ownership metadata"
            raise RuntimeError(
                f"Cannot unregister extension {extension_id!r}: handler ownership "
                f"drift detected for {details}. The active handlers were left untouched; "
                "repair or reconcile the registration before retrying teardown."
            )
        original_registry = current_registry
        original_pre_commit_hook = self._command_manager.pre_commit_hook
        try:
            for command_type in command_types:
                self._command_manager.unregister_handler(command_type)
        except BaseException as teardown_error:
            rollback_errors: list[tuple[str, BaseException]] = []
            try:
                self._command_manager.restore_handler_registry(original_registry)
            except BaseException as rollback_error:
                rollback_errors.append(("handler registry", rollback_error))
            try:
                self._command_manager.set_pre_commit_hook(original_pre_commit_hook)
            except BaseException as rollback_error:
                rollback_errors.append(("pre-commit hook", rollback_error))
            if rollback_errors:
                failed_parts = ", ".join(part for part, _ in rollback_errors)
                details = "; ".join(
                    f"{part}: {error!r}" for part, error in rollback_errors
                )
                raise RuntimeError(
                    f"Extension handler teardown for {extension_id!r} failed "
                    f"({teardown_error!r}) and rollback failed for {failed_parts} "
                    f"({details}); the active CommandManager may remain corrupted. "
                    "Extension ownership metadata was retained."
                ) from rollback_errors[0][1]
            raise
        # Publish ownership removal only after all registry mutations succeed.
        self._extension_handler_command_types.pop(extension_id, None)
        self._extension_handler_handlers.pop(extension_id, None)
        del self._extension_handler_factories[extension_id]
        return True

    @staticmethod
    def _validate_extension_handlers(
        extension_id: str,
        handlers: Mapping[str, Any],
        command_manager: CommandManager,
    ) -> None:
        """Validate a factory result without changing the target registry."""
        if not isinstance(handlers, Mapping):
            raise TypeError(
                f"Extension {extension_id!r} factory must return a command-type mapping."
            )
        seen: set[str] = set()
        for command_type, handler in handlers.items():
            if not isinstance(command_type, str) or not command_type.strip():
                raise ValueError(
                    f"Extension {extension_id!r} returned an invalid command type."
                )
            if command_type in seen:
                raise ValueError(
                    f"Extension {extension_id!r} returned duplicate command type {command_type!r}."
                )
            if not callable(handler):
                raise TypeError(
                    f"Extension {extension_id!r} handler for {command_type!r} is not callable."
                )
            if command_manager.has_handler(command_type):
                raise ValueError(
                    f"Extension {extension_id!r} conflicts with registered command {command_type!r}."
                )
            seen.add(command_type)

    def _replace_runtime(self, command_manager: CommandManager, read_service: ReadService, validation_service: ValidationService | None = None) -> None:
        if not isinstance(command_manager, CommandManager): raise TypeError("Application command_manager must be a CommandManager.")
        if not isinstance(read_service, ReadService): raise TypeError("read_service must implement ReadService.")
        if validation_service is not None and not isinstance(validation_service, ValidationService): raise TypeError("validation_service must be a ValidationService.")
        if command_manager is self._command_manager:
            raise ValueError(
                "Runtime replacement requires a distinct candidate CommandManager; "
                "the active manager cannot be mutated as a candidate."
            )

        # Snapshot the complete candidate composition before any mutation.
        # Reverse-unregister bookkeeping alone cannot restore overwritten or
        # removed entries, the original order, or the previous pre-commit hook.
        original_registry = command_manager.handler_registry_snapshot()
        original_pre_commit_hook = command_manager.pre_commit_hook
        try:
            if self._sld_service is not None:
                for command_type, handler in SLDCommandHandlers(self._sld_service).handlers().items():
                    command_manager.register_handler(command_type, handler)

            next_extension_types: dict[str, tuple[str, ...]] = {}
            next_extension_handlers: dict[str, dict[str, Any]] = {}
            for extension_id, factory in self._extension_handler_factories.items():
                extension_handlers = self._invoke_extension_handler_factory(extension_id, factory, command_manager)
                self._validate_extension_handlers(extension_id, extension_handlers, command_manager)
                installed_extension_types: list[str] = []
                for command_type, handler in extension_handlers.items():
                    command_manager.register_handler(command_type, handler)
                    installed_extension_types.append(command_type)
                next_extension_types[extension_id] = tuple(installed_extension_types)
                next_extension_handlers[extension_id] = dict(extension_handlers)

            next_control_execution = ControlExecutionService(ControlCommandDispatcher(command_manager, command_executor=self.execute))
            next_control_cycle = self._control_cycle
            if self._control_engine is not None:
                next_control_cycle = ControlCycleService(
                    self._control_engine,
                    next_control_execution,
                    signal_mapping=self._control_signal_mapping,
                    read_service=read_service,
                )
            command_manager.set_pre_commit_hook(self._coordinate_pre_commit)
        except BaseException as setup_error:
            # Restore the whole captured composition, including pre-existing
            # entries and the hook. A rollback failure must not be silently
            # swallowed because the candidate may then be unsafe to reuse.
            rollback_errors: list[tuple[str, BaseException]] = []
            try:
                command_manager.restore_handler_registry(original_registry)
            except BaseException as exc:
                rollback_errors.append(("handler registry", exc))
            try:
                command_manager.set_pre_commit_hook(original_pre_commit_hook)
            except BaseException as exc:
                rollback_errors.append(("pre-commit hook", exc))
            if rollback_errors:
                failed_parts = ", ".join(part for part, _ in rollback_errors)
                details = "; ".join(
                    f"{part}: {error!r}" for part, error in rollback_errors
                )
                raise RuntimeError(
                    f"Candidate runtime composition failed ({setup_error!r}) and "
                    f"rollback failed for {failed_parts}. The candidate "
                    f"CommandManager may remain corrupted. Rollback errors: {details}."
                ) from rollback_errors[0][1]
            raise

        self._command_manager, self._read_service, self._validation_service, self._control_execution = command_manager, read_service, validation_service, next_control_execution
        self._control_cycle = next_control_cycle
        self._extension_handler_command_types = next_extension_types
        self._extension_handler_handlers = next_extension_handlers

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
            candidate_runtime = ProtectionRuntime(network, configuration_service.configuration, activation_generation=self.project_lifecycle.activation_generation)
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
            self._apply_configuration_dispositions(
                tuple(result.metadata.get("created_elements", ())),
                transaction,
            )
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
                    draft_presentation = item.get("presentation")
                    symbol_presentation = (
                        dict(draft_presentation)
                        if isinstance(draft_presentation, Mapping)
                        and isinstance(draft_presentation.get("symbol_id"), str)
                        and draft_presentation["symbol_id"].strip()
                        else None
                    )
                    draft_rotation = (
                        draft_presentation.get("orientation")
                        if symbol_presentation is None and isinstance(draft_presentation, Mapping)
                        else None
                    )
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
                                presentation=symbol_presentation,
                                presentation_rotation=draft_rotation,
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
                    source_endpoint = self._sld_endpoint_for_committed_mapping(item["endpoint_a"])
                    target_endpoint = self._sld_endpoint_for_committed_mapping(item["endpoint_b"])
                    if self._sld_service.rebind_orphaned_simple_wire(
                        core_connection_id=str(item["connection_id"]),
                        source_endpoint=source_endpoint,
                        target_endpoint=target_endpoint,
                        transaction=transaction,
                    ):
                        continue
                    existing = self._sld_service.document.model.get_connection_optional(connection_id)
                    if existing is not None:
                        raise ValueError(
                            f"SLD connection identity collision for committed connection {connection_id!r}."
                        )
                    self._sld_service.execute(
                        AddSLDConnectionCommand(
                            connection_id=connection_id,
                            source_node_id=source_endpoint["node_id"],
                            target_node_id=target_endpoint["node_id"],
                            source_endpoint=source_endpoint,
                            target_endpoint=target_endpoint,
                            connection_kind="SIMPLE_WIRE",
                            route=item.get("route") or None,
                            presentation_owner="projection",
                            projection_source="network.commit_draft",
                            core_connection_id=str(item["connection_id"]),
                            correlation_id=command.correlation_id,
                            causation_id=command.command_id,
                        ),
                        transaction,
                    )
            return

        if command_type.startswith("model.") and self._action_from_command_type(command_type) == "delete":
            element_id = self._element_id(command)
            if element_id is not None:
                self._remove_configuration_assessment(element_id, transaction)

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

    def _sld_endpoint_for_committed_mapping(self, mapping: Any) -> dict[str, Any]:
        """Translate DraftNetwork endpoint identity into the canonical SLD projection schema."""
        if not isinstance(mapping, Mapping):
            raise TypeError("Committed draft endpoint mapping must be a mapping.")
        kind = str(mapping.get("kind") or "")
        object_id = str(mapping.get("object_id") or "")
        if not object_id:
            raise ValueError("Committed endpoint mapping requires a non-empty object_id.")
        node_id = self._sld_node_id_for_endpoint_mapping(mapping)
        if kind == "bus":
            attachment_id = mapping.get("attachment_id")
            if not isinstance(attachment_id, str) or not attachment_id:
                raise ValueError("Committed Bus endpoint requires attachment_id.")
            return {
                "kind": "bus",
                "node_id": node_id,
                "equipment_id": None,
                "terminal_role": None,
                "bus_id": object_id,
                "attachment_id": attachment_id,
            }
        if kind == "terminal":
            terminal_role = mapping.get("terminal_role")
            if not isinstance(terminal_role, str) or not terminal_role:
                raise ValueError("Committed terminal endpoint requires terminal_role.")
            return {
                "kind": "equipment",
                "node_id": node_id,
                "equipment_id": object_id,
                "terminal_role": terminal_role,
                "bus_id": None,
                "attachment_id": None,
            }
        raise ValueError(f"Unsupported committed draft endpoint kind: {kind!r}.")

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

        # Simple Wire commands normalize their endpoints to topology-level
        # references before reaching Application. Unwrap only the explicitly
        # declared terminal/Bus payload; never infer an endpoint from graphics
        # coordinates or silently map a Junction to a terminal.
        def sld_endpoint_reference(value: Any) -> EndpointReference:
            if isinstance(value, EndpointReference):
                return value
            if isinstance(value, TopologyEndpointReference):
                if value.is_terminal:
                    endpoint = value.terminal_reference
                    if isinstance(endpoint, EndpointReference) and endpoint.is_terminal:
                        return endpoint
                    raise ValueError(
                        "Terminal topology endpoint does not contain a valid canonical terminal reference."
                    )
                if value.is_bus:
                    endpoint = value.bus_reference
                    if isinstance(endpoint, EndpointReference) and endpoint.is_bus:
                        return endpoint
                    raise ValueError(
                        "Bus topology endpoint does not contain a valid canonical Bus attachment reference."
                    )
                if value.is_junction:
                    raise ValueError(
                        "SLD connection projection does not support Junction endpoints; "
                        "a dedicated Junction presentation contract is required."
                    )
                raise ValueError(
                    f"Unsupported topology endpoint kind for SLD projection: {value.kind!r}."
                )
            raise ValueError(
                "Connection commands require canonical EndpointReference or "
                "TopologyEndpointReference endpoints."
            )

        source_ref = sld_endpoint_reference(source_ref)
        target_ref = sld_endpoint_reference(target_ref)
        if source_ref == target_ref:
            raise ValueError("Connection endpoints must be distinct.")

        source = self._sld_endpoint_for_reference(source_ref)
        target = self._sld_endpoint_for_reference(target_ref)

        # Core connection identity and persistent SLD presentation identity
        # are separate contracts. Draft commit already uses this mapping; direct
        # Simple Wire commands must use it too so activation audits, deletion,
        # undo/redo, and draft-commit projection resolve the same companion.
        sld_connection_id = (
            self._sld_service.presentation_connection_id(connection_id)
            if command.command_type == "connectivity.create_simple_wire"
            else connection_id
        )
        if command.command_type == "connectivity.create_simple_wire" and self._sld_service.rebind_orphaned_simple_wire(
            core_connection_id=connection_id,
            source_endpoint=source,
            target_endpoint=target,
            transaction=transaction,
        ):
            return

        projection_result = self._sld_service.execute(
            AddSLDConnectionCommand(
                connection_id=sld_connection_id,
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

    def _sld_endpoint_for_reference(self, reference: EndpointReference) -> dict[str, Any]:
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
                "equipment_id": None,
                "terminal_role": None,
                "bus_id": str(reference.object_id),
                "attachment_id": attachment_id,
            }

        return {
            "kind": "equipment",
            "node_id": str(node.node_id),
            "equipment_id": str(reference.object_id),
            "terminal_role": str(reference.terminal_role),
            "bus_id": None,
            "attachment_id": None,
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
        """Translate intent using the trusted Application creation contract."""
        if not isinstance(intent, CreationCommitIntent):
            raise TypeError("intent must be a CreationCommitIntent.")
        canonical_contract = self._creation_contract_registry.resolve(intent.equipment_type)
        return CreationCommandPreparer.prepare(intent, canonical_contract=canonical_contract)

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

    def commit_draft_network_async(self) -> Future[ApplicationResult]:
        """Execute the canonical DraftNetwork command off-thread and finalize events on the caller thread."""
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

        project_id = context.project_id
        activation_generation = lifecycle.activation_generation
        draft_payload = draft.to_dict()
        source_revision = self.revision
        command = CommitNetworkCommand(project_id=project_id, activation_generation=activation_generation, draft_network=draft_payload)
        # Serialize the transition into the in-flight state with project saves.
        # A save holds this same execution lock for its complete persistence window,
        # so a commit cannot become pending after the save has passed its guard.
        with self._execution_lock:
            with self._commit_state_lock:
                if self._commit_in_flight:
                    raise RuntimeError("A DraftNetwork commit is already in progress.")
                if self._save_in_progress:
                    raise RuntimeError("Project persistence is in progress; retry the DraftNetwork commit afterward.")
                self._commit_in_flight = True
                self._pending_async_commit_command = command

        def execute_captured_commit() -> ApplicationResult:
            try:
                with self._execution_lock:
                    current_lifecycle = self.project_lifecycle
                    current_context = current_lifecycle.context
                    current_draft = self._draft_network
                    if (
                        current_lifecycle.state != "ACTIVE"
                        or not current_lifecycle.has_project
                        or current_context is None
                        or current_context.project_id != project_id
                        or current_lifecycle.activation_generation != activation_generation
                        or self.revision != source_revision
                        or current_draft is not draft
                        or current_draft is None
                        or current_draft.to_dict() != draft_payload
                    ):
                        raise RuntimeError("DraftNetwork commit became stale before execution; the project, revision, or draft changed.")
                    # Core mutation/history runs on the worker; synchronous Application
                    # events are deliberately published by complete_async_commit().
                    return self._command_manager.execute(command)
            except BaseException:
                self.abort_async_commit()
                raise

        try:
            return self._commit_executor.submit(execute_captured_commit)
        except BaseException:
            self.abort_async_commit()
            raise

    def complete_async_commit(self, result: ApplicationResult) -> ApplicationResult:
        """Publish semantic events after worker completion on the caller/UI thread."""
        with self._commit_state_lock:
            command = self._pending_async_commit_command
            if not self._commit_in_flight or command is None:
                raise RuntimeError("No asynchronous DraftNetwork commit awaits completion.")
        try:
            # The worker has completed before this UI-thread callback; the lock
            # preserves the normal Application command/event serialization order.
            with self._execution_lock:
                if result.success:
                    if self._validation_service is not None:
                        self._validation_service.invalidate()
                        self._event_bus.publish(ValidationChanged(metadata={"valid": False, "invalidated": True, **self._project_scope_metadata()}))
                    self._publish_semantic_events(command, result, operation="execute")
            return result
        finally:
            self.abort_async_commit()

    def abort_async_commit(self) -> None:
        """Release the single-flight guard after failure or completion."""
        with self._commit_state_lock:
            self._pending_async_commit_command = None
            self._commit_in_flight = False

    def _reject_during_async_commit(self) -> None:
        with self._commit_state_lock:
            if self._commit_in_flight:
                raise RuntimeError("Another DraftNetwork commit is in progress; retry after it completes.")
            if self._save_in_progress:
                raise RuntimeError("Project persistence is in progress; retry the mutation after save completes.")

    def read_draft_network(self) -> Mapping[str, Any] | None:
        """Return an isolated serialized snapshot for Application read/projection consumers."""
        draft = self._draft_network
        if draft is None:
            return None
        from copy import deepcopy
        return deepcopy(draft.to_dict())

    def execute(self, command: Command) -> ApplicationResult:
        if not isinstance(command, Command):
            raise TypeError("Application.execute requires a Command.")
        self._reject_during_async_commit()
        with self._execution_lock:
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
        self._reject_during_async_commit()
        with self._execution_lock:
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
        self._reject_during_async_commit()
        with self._execution_lock:
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
    def clear_history(self) -> None:
        """Clear command and revision journals while preserving project revisions."""
        self._command_manager.clear_history()
        self._revision_service.clear_transitions()

    def read_control(self):
        service = getattr(self, "control_service", None)
        if service is None:
            raise RuntimeError("Application Control service is not configured.")
        return service.read()

    def read_network(self) -> NetworkReadModel:
        self._require_read_service(); return self._read_service.network()  # type: ignore[union-attr]
    def read_element(self, element_type: str, object_id: str) -> ElementReadModel:
        self._require_read_service()
        model = self._read_service.element(element_type, object_id)  # type: ignore[union-attr]
        assessment = (
            self._equipment_configuration_assessments.get(object_id)
            if self._configuration_assessments_project_id == self.project_lifecycle.context.project_id
            and self.project_lifecycle.context is not None
            else None
        )
        return replace(model, configuration_assessment=assessment)

    def read_equipment_configuration_assessment(
        self, equipment_id: str
    ) -> EquipmentConfigurationAssessmentReadModel:
        """Return the Application-owned configuration obligation assessment."""
        context = self.project_lifecycle.context
        if context is None or self._configuration_assessments_project_id != context.project_id:
            raise RuntimeError("Equipment configuration assessments are unavailable without the active project scope.")
        try:
            return self._equipment_configuration_assessments[equipment_id]
        except KeyError as exc:
            raise KeyError(f"No configuration assessment is recorded for equipment {equipment_id!r}.") from exc

    def read_equipment_configuration_assessments(
        self,
    ) -> tuple[EquipmentConfigurationAssessmentReadModel, ...]:
        """Return detached assessments for all equipment with a recorded creation contract."""
        context = self.project_lifecycle.context
        if context is None or self._configuration_assessments_project_id != context.project_id:
            return ()
        return tuple(self._equipment_configuration_assessments[key] for key in sorted(self._equipment_configuration_assessments))

    def _apply_configuration_dispositions(self, created_elements: tuple[Mapping[str, Any], ...], transaction: Any) -> None:
        context = self.project_lifecycle.context
        if context is None:
            raise RuntimeError("Cannot record equipment configuration obligations without an active project.")
        if self._configuration_assessments_project_id not in (None, context.project_id):
            raise RuntimeError("Equipment configuration assessments belong to a different active project.")
        next_assessments = dict(self._equipment_configuration_assessments)
        for item in created_elements:
            equipment_id = str(item.get("core_id") or "").strip()
            equipment_type = str(item.get("element_type") or "").strip().lower()
            dispositions = item.get("configuration_dispositions")
            if not equipment_id or not equipment_type or not isinstance(dispositions, Mapping):
                raise ValueError("Committed equipment is missing its trusted configuration disposition contract.")
            fields = []
            for field_name, disposition in sorted(dispositions.items(), key=lambda pair: str(pair[0])):
                if not isinstance(disposition, Mapping):
                    raise TypeError(f"Configuration disposition for {field_name!r} must be a mapping.")
                kind = str(disposition.get("kind") or "")
                reason = str(disposition.get("reason") or "")
                workflow = disposition.get("workflow")
                if kind == "metadata":
                    status = "RESOLVED"
                elif kind == "not_applicable":
                    status = "NOT_APPLICABLE"
                elif kind == "provisional_default":
                    status = "PROVISIONAL"
                else:
                    status = "PENDING"
                fields.append(ConfigurationFieldAssessmentReadModel(
                    field_name=str(field_name),
                    kind=kind,
                    reason=reason,
                    workflow=str(workflow) if workflow is not None else None,
                    status=status,
                ))
            if equipment_id in next_assessments:
                raise ValueError(f"Configuration assessment already exists for equipment {equipment_id!r}.")
            next_assessments[equipment_id] = EquipmentConfigurationAssessmentReadModel(
                equipment_id=equipment_id,
                equipment_type=equipment_type,
                fields=tuple(fields),
            )
        previous_project_id = self._configuration_assessments_project_id
        previous_assessments = self._equipment_configuration_assessments
        transaction.record_undo(
            lambda project_id=previous_project_id, assessments=previous_assessments: self._restore_configuration_assessments(project_id, assessments)
        )
        self._configuration_assessments_project_id = context.project_id
        self._equipment_configuration_assessments = next_assessments

    def _remove_configuration_assessment(self, equipment_id: str, transaction: Any) -> None:
        if equipment_id not in self._equipment_configuration_assessments:
            return
        previous_project_id = self._configuration_assessments_project_id
        previous_assessments = self._equipment_configuration_assessments
        next_assessments = dict(previous_assessments)
        next_assessments.pop(equipment_id, None)
        transaction.record_undo(
            lambda project_id=previous_project_id, assessments=previous_assessments: self._restore_configuration_assessments(project_id, assessments)
        )
        self._equipment_configuration_assessments = next_assessments

    def _restore_configuration_assessments(
        self,
        project_id: str | None,
        assessments: dict[str, EquipmentConfigurationAssessmentReadModel],
    ) -> None:
        self._configuration_assessments_project_id = project_id
        self._equipment_configuration_assessments = assessments
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
        elif command.command_type == "control.execute_trip_batch":
            breaker_ids = tuple(command.payload.get("breaker_ids", ()))
            batch_metadata = {
                **metadata,
                "breaker_ids": breaker_ids,
                "atomic": True,
                "command_type": command.command_type,
            }
            self._event_bus.publish(TopologyChanged(
                operation=operation,
                metadata=batch_metadata,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            ))
            self._event_bus.publish(NetworkChanged(
                operation=operation,
                metadata=batch_metadata,
                correlation_id=command.correlation_id,
                causation_id=command.causation_id,
            ))
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
