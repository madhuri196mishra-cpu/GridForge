# ============================================================
# GridForge V2 — Application Composition Root
# ============================================================
# Author: Subhendu Mishra

"""Composition root for the headless GridForge Application layer."""

from __future__ import annotations

from typing import Any, Callable
from pathlib import Path
import math
from dataclasses import replace
from uuid import uuid4

from core.analysis.contingency import ContingencyAnalysis
from core.analysis.power_flow import PowerFlowAnalysis
from core.analysis.power_flow_configuration import PowerFlowStudyConfiguration
from core.analysis.power_flow_preparation import PreparedPowerFlow
from core.analysis.short_circuit import ShortCircuitAnalysis
from core.analysis.short_circuit_configuration import ShortCircuitStudyConfiguration
from core.analysis.dynamic_model_association import DynamicMachineModelRegistry
from core.analysis.transient_event_state import TransientEventState
from core.analysis.transient_events import (
    schedule_breaker_close,
    schedule_breaker_open,
    schedule_equipment_state,
    schedule_fault_apply,
    schedule_fault_clear,
)
from core.analysis.transient_fault import TransientFault
from core.analysis.transient_network import TransientNetworkSolver
from core.analysis.transient_runtime import TransientNetworkRuntime
from core.analysis.transient_stability import TransientStabilityAnalysis, TransientStabilityStudyConfiguration
from core.network import Network
from core.model.breaker import Breaker
from core.persistence import ProjectPersistenceService, StudyResultArchive
from core.protection.project_configuration import ProtectionProjectConfiguration
from core.control.configuration import ControlConfiguration
from core.application.control_signal_mapping import ControlSignalMapping
from core.protection.runtime import ProtectionRuntime
from core.solver.dynamics import DAESolver, EventManager, Integrator, MultiMachineSystem, TransientStabilitySolver
from core.solver.power_flow.result import PowerFlowResult
from core.solver.short_circuit.fault_types import FaultType

from .application import Application
from .control_dispatch import ControlTripBatchCommand
from .control_command_handlers import ControlCommandHandlers
from .command_handlers import build_model_command_handlers
from .services.electrical_connection_service import ElectricalConnectionCommandHandlers
from .services.simple_wire_service import SimpleWireConnectionCommandHandlers
from .services.phase_domain_conductor_service import PhaseDomainConductorNetworkCommandHandlers
from .services.junction_service import JunctionCommandHandlers
from .services.electrical_insertion_service import ElectricalInsertionService, ElectricalInsertionCommandHandlers
from .command_manager import CommandManager
from .results import ApplicationResult
from .context import ApplicationContext
from .project import ProjectContext
from .project_lifecycle import ProjectLifecycleService
from .protection_configuration_handlers import ProtectionConfigurationHandlers
from .read_service import NetworkReadService, ProtectionReadService
from .relay_command_handlers import RelayCommandHandlers
from .services.control_service import ControlApplicationService
from .services.model_service import ModelService
from .services.protection_configuration_service import ProtectionConfigurationService
from .services.measurement_channel_service import MeasurementChannelService, MeasurementSampleAttestation
from .synchronism_check import (
    BypassAuthorization,
    ProjectScopedSynchronismAuditSink,
    SynchronismCheckProjectConfiguration,
    create_project_synchronism_guard,
    validate_synchronism_instrument_topology,
)
from .services.relay_model_service import RelayModelService
from .services.validation_service import ValidationService
from .study import StudyRequest, StudyCancellationToken
from .study_preparation import StudyPreparationService
from .draft import DraftNetwork, DraftCommandHandlers, CommitNetworkHandler
from .creation_schema import creation_contracts_from_definitions
from .services.motor_dynamics_service import MotorDynamicsComposition


def create_application(
    network: Any,
    *,
    measurement_source_verifier: Callable[[MeasurementSampleAttestation, Any, float | complex], bool] | None = None,
    measurement_clock: Callable[[], float] | None = None,
    measurement_clock_domain: str | None = None,
    max_measurement_source_clock_skew_seconds: float = 2.0,
    synchronism_bypass_provider: Callable[[Breaker], BypassAuthorization | None] | None = None,
) -> Application:
    """Construct the fully configured headless GridForge Application facade."""
    if network is None:
        raise ValueError("network is required.")
    if synchronism_bypass_provider is not None and not callable(synchronism_bypass_provider):
        raise TypeError("synchronism_bypass_provider must be callable when supplied.")

    initial_context = ProjectContext(project_id=str(uuid4()), name="Untitled Project", path=None)
    # The protection service may resolve the active Network for its own
    # Application-scoped configuration checks. Study execution never uses this
    # provider; studies capture an explicit detached ProjectSnapshot.
    lifecycle = None
    measurement_channel_service = MeasurementChannelService(
        source_verifier=measurement_source_verifier,
        clock=measurement_clock,
        clock_domain=measurement_clock_domain,
        max_source_clock_skew_seconds=max_measurement_source_clock_skew_seconds,
    )
    measurement_channel_service.activate(initial_context, network, (), generation=1)
    protection_configuration_service = ProtectionConfigurationService(
        ProtectionProjectConfiguration(initial_context.project_id),
        network_provider=lambda: lifecycle.network if lifecycle is not None else network,
        measurement_channel_provider=lambda: measurement_channel_service.channels,
    )

    def register_handlers(target: dict[str, Any], source: Any, family: str) -> None:
        """Merge one handler family into the single Application registry.

        Duplicate command ownership is a composition error, not a last-write-wins
        condition.  The composition root therefore rejects duplicate handlers
        before constructing the CommandManager.
        """
        for command_type, handler in dict(source).items():
            if command_type in target:
                raise RuntimeError(
                    f"Duplicate Application handler registration for {command_type!r} "
                    f"while composing {family}."
                )
            target[command_type] = handler

    control_service = ControlApplicationService(ControlConfiguration.empty(initial_context.project_id))
    application: Application | None = None
    synchronism_guard: Any = None

    def authorize_breaker_close(breaker: Breaker, operation: str) -> bool:
        nonlocal synchronism_guard
        active_lifecycle = getattr(application, "project_lifecycle", None)
        active_context = getattr(active_lifecycle, "context", None)
        active_network = getattr(active_lifecycle, "network", None)
        active_generation = getattr(active_lifecycle, "activation_generation", 0)
        active_path = getattr(active_context, "path", None)
        guard = None
        if (
            active_context is not None
            and active_network is not None
            and measurement_channel_service.network is active_network
            and measurement_channel_service.project_id == active_context.project_id
            and measurement_channel_service.activation_generation == active_generation
            and measurement_clock is not None
            and isinstance(measurement_clock_domain, str)
            and measurement_clock_domain.strip()
            and active_path is not None
        ):
            audit_sink = ProjectScopedSynchronismAuditSink(
                project_id=active_context.project_id,
                activation_generation=active_generation,
                project_path=active_path,
                clock=measurement_clock,
                clock_domain=measurement_clock_domain,
            )
            guard = create_project_synchronism_guard(
                project_id=active_context.project_id,
                activation_generation=active_generation,
                project_configuration_provider=lambda: (
                    protection_configuration_service.configuration.synchronism_check_configuration
                ),
                measurement_channel_service=measurement_channel_service,
                audit_sink=audit_sink,
                clock=measurement_clock,
                clock_domain=measurement_clock_domain,
                bypass_provider=synchronism_bypass_provider,
                topology_validator=lambda target_breaker, policy, channels: (
                    validate_synchronism_instrument_topology(
                        network=active_network,
                        breaker=target_breaker,
                        configuration=policy,
                        channels=channels,
                    )
                ),
            )
        synchronism_guard = guard
        if guard is None:
            raise PermissionError("Breaker energization denied: no active audited ANSI 25 guard.")
        decision = guard.authorize_close(breaker)
        if getattr(decision, "permitted", None) is not True:
            raise PermissionError("Breaker energization denied by ANSI 25.")
        return True

    def build_runtime(active_network: Any) -> tuple[CommandManager, NetworkReadService, ValidationService]:
        context = ApplicationContext(network=active_network)
        model_service = ModelService(
            network=active_network,
            breaker_close_authorizer=authorize_breaker_close,
        )
        handlers: dict[str, Any] = {}
        register_handlers(handlers, build_model_command_handlers(model_service), "model")
        register_handlers(handlers, ElectricalConnectionCommandHandlers().handlers(), "electrical connection")
        register_handlers(handlers, SimpleWireConnectionCommandHandlers().handlers(), "simple wire connectivity")
        register_handlers(handlers, PhaseDomainConductorNetworkCommandHandlers().handlers(), "phase-domain conductor network")
        register_handlers(handlers, JunctionCommandHandlers().handlers(), "junction topology")
        register_handlers(
            handlers,
            RelayCommandHandlers(
                RelayModelService(
                    active_network,
                    protection_configuration_provider=lambda: protection_configuration_service.configuration,
                )
            ).handlers(),
            "protection relay",
        )
        register_handlers(
            handlers,
            ProtectionConfigurationHandlers(protection_configuration_service).handlers(),
            "protection configuration",
        )
        # Control editing commands are composed into the same authoritative
        # Application command registry as model and protection commands.
        register_handlers(handlers, ControlCommandHandlers(
            control_service,
            signal_mapping_getter=lambda: application.control_signal_mapping,
            signal_mapping_setter=lambda mapping: application.configure_control_signal_mapping(mapping),
        ).handlers(), "control")
        command_manager = CommandManager(context=context, handlers=handlers)

        def execute_control_trip_batch(command: Any, _context: Any, transaction: Any) -> Any:
            """Execute all breaker trips in one shared transaction and history record."""
            children = tuple(command.payload.get("commands", ()))
            expected_ids = tuple(command.payload.get("breaker_ids", ()))
            if len(children) < 2 or len(children) != len(expected_ids):
                raise ValueError("Coordinated breaker-trip batch payload is incomplete.")
            actual_ids = tuple(
                str(child.payload.get("breaker_id", "")).strip()
                for child in children
            )
            if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
                raise ValueError("Coordinated breaker-trip batch identity validation failed.")
            for child in children:
                if child.command_type != "model.trip_breaker":
                    raise ValueError("Only canonical breaker trip commands are permitted in this batch.")
                command_manager.execute_in_transaction(child, transaction)
            return ApplicationResult.success_result(
                message=f"Atomically executed {len(children)} coordinated breaker trips.",
                metadata={
                    "breaker_ids": expected_ids,
                    "child_command_ids": tuple(str(child.command_id) for child in children),
                    "atomic": True,
                },
            )

        command_manager.register_handler(
            ControlTripBatchCommand.COMMAND_TYPE,
            execute_control_trip_batch,
        )
        insertion_service = ElectricalInsertionService(
            command_executor=command_manager.execute_in_transaction,
        )
        command_manager.register_handler(
            "connectivity.insert_equipment_into_connection",
            ElectricalInsertionCommandHandlers(insertion_service).execute,
        )
        draft_provider = lambda: application.draft_network if application is not None else None
        for command_type, handler in DraftCommandHandlers(draft_provider).handlers().items():
            command_manager.register_handler(command_type, handler)
        command_manager.register_handler(
            "network.commit_draft",
            CommitNetworkHandler(
                draft_provider,
                command_executor=command_manager.execute_in_transaction,
                creation_contract_provider=(
                    lambda: application.creation_contract_registry
                    if application is not None else None
                ),
            ),
        )
        return command_manager, NetworkReadService(active_network), ValidationService(active_network)

    command_manager, read_service, validation_service = build_runtime(network)
    application = Application(
        command_manager=command_manager,
        read_service=read_service,
        validation_service=validation_service,
        measurement_channel_service=measurement_channel_service,
        control_service=control_service,
    )
    measurement_channel_service.bind_execution_lock(application._execution_lock)
    # Study outputs are durable execution artifacts, stored outside the
    # canonical project package so ordinary project saves cannot discard them.
    study_result_archive = StudyResultArchive(Path.home() / ".gridforge" / "study-results")
    application.configure_study_result_archive(study_result_archive)

    # Configure trusted creation contracts at the canonical Application
    # composition boundary. The registry validates all definitions against
    # actual command constructor signatures before any draft can be committed.
    application.configure_creation_contracts(creation_contracts_from_definitions())
    # Optional UI plugin consumes this adapter through PluginContext.services.
    # No motor handlers are registered until that plugin is explicitly loaded.
    application.motor_dynamics_composition = MotorDynamicsComposition(application)

    application.set_draft_network(DraftNetwork.empty(initial_context.project_id, 1))
    application.protection_configuration_service = protection_configuration_service
    initial_protection_runtime = ProtectionRuntime(
        network, protection_configuration_service.configuration, activation_generation=1
    )
    initial_protection_runtime.compose(measurement_channel_service.channels)
    application.protection_runtime = initial_protection_runtime
    application.protection_presentation = None
    def protection_decisions_for_relay(relay_id: str):
        runtime = application.protection_runtime
        if runtime is None:
            return ()
        return runtime.system.decisions_for_relay(str(relay_id))

    application._protection_read_service = ProtectionReadService(
        network,
        decision_provider=protection_decisions_for_relay,
    )

    def activate_network(active_network: Any):
        """Replace project-bound Application runtime and return its rollback."""
        previous_command_manager = application._command_manager
        previous_read_service = application._read_service
        previous_validation_service = application._validation_service
        previous_protection_read_service = application._protection_read_service
        previous_control_execution = application._control_execution
        previous_control_cycle = application._control_cycle
        previous_extension_handler_command_types = dict(application._extension_handler_command_types)
        previous_extension_handler_handlers = {
            extension_id: dict(handlers)
            for extension_id, handlers in application._extension_handler_handlers.items()
        }

        try:
            next_command_manager, next_read_service, next_validation_service = build_runtime(active_network)
            application._replace_runtime(next_command_manager, next_read_service, next_validation_service)
            application._protection_read_service = ProtectionReadService(
                active_network,
                decision_provider=protection_decisions_for_relay,
            )
        except Exception:
            application._command_manager = previous_command_manager
            application._read_service = previous_read_service
            application._validation_service = previous_validation_service
            application._protection_read_service = previous_protection_read_service
            application._control_execution = previous_control_execution
            application._control_cycle = previous_control_cycle
            application._extension_handler_command_types = previous_extension_handler_command_types
            application._extension_handler_handlers = {
                extension_id: dict(handlers)
                for extension_id, handlers in previous_extension_handler_handlers.items()
            }
            raise

        def rollback() -> None:
            application._command_manager = previous_command_manager
            application._read_service = previous_read_service
            application._validation_service = previous_validation_service
            application._protection_read_service = previous_protection_read_service
            application._control_execution = previous_control_execution
            application._control_cycle = previous_control_cycle
            application._extension_handler_command_types = previous_extension_handler_command_types
            application._extension_handler_handlers = {
                extension_id: dict(handlers)
                for extension_id, handlers in previous_extension_handler_handlers.items()
            }

        return rollback

    persistence = ProjectPersistenceService()
    dynamic_models = DynamicMachineModelRegistry()

    def activate_project_state(context: ProjectContext | None, loaded, network: Network, generation: int):
        """Install project-scoped protection/dynamic runtime state transactionally."""
        nonlocal synchronism_guard
        previous_configuration = protection_configuration_service.configuration
        previous_synchronism_guard = synchronism_guard
        previous_measurement_state = measurement_channel_service.snapshot_state()
        previous_protection_runtime = application.protection_runtime
        previous_protection_presentation = getattr(application, "protection_presentation", None)
        # Capture the exact pre-transition authority. Reconstructing a synthetic
        # ProjectContext or consulting lifecycle.network during compensation can
        # bind old channel definitions to the candidate network.
        previous_lifecycle_context = application.project_lifecycle.context
        previous_lifecycle_network = application.project_lifecycle.network
        previous_dynamic_models = dynamic_models.snapshot()
        previous_control_configuration = control_service.configuration
        previous_control_signal_mapping = application.control_signal_mapping
        previous_control_generation = application.control_engine.activation_generation
        previous_control_active = application.control_engine.configuration is not None
        previous_draft = application.draft_network
        previous_study_cases = dict(application._study_cases)

        def restore_control_runtime() -> None:
            if not previous_control_active:
                application.control_engine.deactivate()
            else:
                application.control_engine.configure(
                    previous_control_configuration,
                    activation_generation=previous_control_generation,
                )
            control_service.activate(previous_control_configuration)
            application.configure_control_signal_mapping(previous_control_signal_mapping)

        def restore_project_state() -> None:
            nonlocal synchronism_guard
            synchronism_guard = previous_synchronism_guard
            measurement_channel_service.restore_state(previous_measurement_state)
            if previous_configuration is None:
                protection_configuration_service.deactivate()
            else:
                protection_configuration_service.activate(previous_configuration)
            application.protection_runtime = previous_protection_runtime
            application.protection_presentation = previous_protection_presentation
            application.set_draft_network(previous_draft)
            application._study_cases = dict(previous_study_cases)
            dynamic_models.replace(previous_dynamic_models)
            restore_control_runtime()

        try:
            # Rebuild the candidate project's measurement registry before
            # validating protection bindings or composing ProtectionRuntime.
            # The loaded definitions are configuration only; activation binds
            # them to this candidate Network and project generation.
            measurement_channel_service.activate(
                context,
                network,
                loaded.measurement_definitions if context is not None and loaded is not None else (),
                generation=generation,
            )
            if context is None:
                control_service.activate(ControlConfiguration.empty("closed-project"))
                application.control_engine.deactivate()
                application.configure_control_signal_mapping(None)
                protection_configuration_service.deactivate()
                application.protection_runtime = None
                application.protection_presentation = None
                dynamic_models.replace(())
                application.set_draft_network(DraftNetwork.empty(context.project_id if context is not None else "closed-project", generation))
                application._study_cases = {}
            else:
                configuration = (
                    loaded.protection_configuration
                    if loaded is not None and loaded.protection_configuration is not None
                    else ProtectionProjectConfiguration(context.project_id)
                )
                if configuration.project_id != context.project_id:
                    raise ValueError(
                        f"Protection configuration project_id {configuration.project_id!r} does not match "
                        f"active project {context.project_id!r}."
                    )
                protection_configuration_service.activate(configuration)
                candidate_channels = measurement_channel_service.channels
                for item in configuration.elements:
                    protection_configuration_service.validate_configuration(
                        item,
                        network=network,
                        channels=candidate_channels,
                    )
                for item in configuration.elements:
                    relay = network.get_by_id("relay", item.relay_id)
                    for input_name, channel_id in item.input_channel_ids.items():
                        relay.bind_input(input_name, measurement_channel_service.require(channel_id))
                control_configuration = (
                    loaded.control_configuration
                    if loaded is not None and loaded.control_configuration is not None
                    else ControlConfiguration.empty(context.project_id)
                )
                if control_configuration.project_id != context.project_id:
                    raise ValueError(
                        "Control configuration project_id does not match the active project."
                    )
                control_configuration.validate()
                application.control_engine.configure(
                    control_configuration,
                    activation_generation=generation,
                )
                control_service.activate(control_configuration)
                application.configure_control_signal_mapping(
                    loaded.control_signal_mapping if loaded is not None else None
                )
                if loaded is None:
                    dynamic_models.replace(())
                else:
                    dynamic_models.replace(
                        tuple(
                            replace(item, activation_generation=generation)
                            for item in loaded.dynamic_models
                        )
                    )
                loaded_draft = getattr(loaded, "draft_network", None) if loaded is not None else None
                if loaded_draft is None:
                    application.set_draft_network(DraftNetwork.empty(context.project_id, generation))
                else:
                    application.set_draft_network(DraftNetwork.from_dict(loaded_draft.to_dict(), project_id=context.project_id, activation_generation=generation))
                candidate_protection_runtime = ProtectionRuntime(
                    network=network,
                    configuration=configuration,
                    activation_generation=generation,
                )
                candidate_protection_runtime.compose(measurement_channel_service.channels)
                application.protection_runtime = candidate_protection_runtime
                application.protection_presentation = getattr(loaded, "protection_presentation", None) if loaded is not None else None
                application._study_cases = {
                    case.study_id: replace(
                        case,
                        project_id=context.project_id,
                        activation_generation=generation,
                    )
                    for case in (getattr(loaded, "study_cases", ()) if loaded is not None else ())
                }

        except Exception as transition_error:
            try:
                restore_project_state()
            except Exception as compensation_error:
                raise ExceptionGroup(
                    "Project activation failed and rollback compensation also failed; "
                    "both failures are preserved.",
                    [transition_error, compensation_error],
                ) from None
            raise

        def rollback() -> None:
            # The lifecycle invokes this callback after a later activation
            # stage fails. Restore the complete snapshot, not just protection
            # and Control services; draft state is part of project identity.
            restore_project_state()

        return rollback

    def validate_project_candidate(context: ProjectContext, loaded, candidate_network, presentation) -> None:
        """Validate candidate identity/provenance without mutating active state."""
        if candidate_network is None:
            raise ValueError("Candidate Network is required.")
        if presentation is None:
            raise ValueError("Candidate presentation is required.")
        if loaded is not None:
            if loaded.context.project_id != context.project_id:
                raise ValueError("Loaded ProjectContext does not match the candidate project.")
            for association in loaded.dynamic_models:
                if association.project_id != context.project_id:
                    raise ValueError(
                        f"Dynamic model association {association.machine_id!r} belongs to "
                        f"project {association.project_id!r}, not {context.project_id!r}."
                    )
                if association.activation_generation < 1:
                    raise ValueError(
                        f"Dynamic model association {association.machine_id!r} has invalid persisted "
                        "activation provenance."
                    )
            configuration = loaded.protection_configuration
            if configuration is not None and configuration.project_id != context.project_id:
                raise ValueError("Protection configuration project_id does not match the candidate project.")
            if configuration is not None and configuration.synchronism_check_configuration is not None:
                synchronism_registry = SynchronismCheckProjectConfiguration.from_dict(
                    configuration.synchronism_check_configuration
                )
                for breaker_id, policy in synchronism_registry.breakers.items():
                    try:
                        candidate_network.get_by_id("breaker", breaker_id)
                    except (KeyError, ValueError) as exc:
                        raise ValueError(
                            f"ANSI 25 policy references unknown breaker {breaker_id!r}."
                        ) from exc
                    configured_channel_ids = {
                        item.get("channel_id", item.get("id"))
                        for item in loaded.measurement_definitions
                        if isinstance(item, Mapping)
                    }
                    missing_channels = sorted(set(policy.measurement_channel_ids.values()) - configured_channel_ids)
                    if missing_channels:
                        raise ValueError(
                            f"ANSI 25 policy for breaker {breaker_id!r} references missing measurement channels: {missing_channels!r}."
                        )
            control_configuration = loaded.control_configuration
            if control_configuration is not None:
                if control_configuration.project_id != context.project_id: raise ValueError("Control configuration project_id does not match the candidate project.")
                control_configuration.validate()
            mapping = loaded.control_signal_mapping
            if mapping is not None:
                if control_configuration is None:
                    control_configuration = ControlConfiguration.empty(context.project_id)
                component_by_id = {
                    record.component_id: record.component
                    for record in control_configuration.program.engine.records()
                }
                action_ids = {item.control_id for item in control_configuration.action_bindings}
                for binding in mapping.bindings:
                    destination = binding.destination
                    component = component_by_id.get(destination.component_id)
                    if component is None:
                        raise ValueError(
                            f"Control signal mapping references missing component {destination.component_id!r}."
                        )
                    if destination.control_id not in action_ids:
                        raise ValueError(
                            f"Control signal mapping references missing action/control {destination.control_id!r}."
                        )
                    input_port = next(
                        (item for item in component.input_definition() if item.name == destination.input_name),
                        None,
                    )
                    if input_port is None:
                        raise ValueError(
                            f"Control signal mapping references missing input "
                            f"{destination.component_id}.{destination.input_name}."
                        )
                    expected = binding.source.expected_type
                    if expected is not None:
                        accepted = expected if isinstance(expected, tuple) else (expected,)
                        if input_port.value_type not in accepted:
                            raise TypeError(
                                f"Control signal mapping type constraint for {destination.component_id}."
                                f"{destination.input_name} is incompatible with {input_port.value_type.__name__}."
                            )

    def load_project(path):
        # Loading is side-effect free. Candidate dynamic/protection state is
        # installed only by the successful activation transaction.
        return persistence.load(path)

    def save_project(context, active_network, presentation, path, presentation_collection=None):
        persistence.save(
            context,
            active_network,
            presentation,
            path,
            dynamic_models=dynamic_models.all(),
            protection_configuration=protection_configuration_service.configuration,
            measurement_definitions=measurement_channel_service.serialize_definitions(),
            control_configuration=control_service.configuration,
            control_signal_mapping=application.control_signal_mapping,
            draft_network=application.draft_network,
            presentation_collection=presentation_collection,
            study_cases=application.study_cases,
            configuration_assessments=application.serialize_equipment_configuration_assessments(),
            protection_presentation=(                application.protection_presentation.to_dict()
                if callable(getattr(getattr(application, "protection_presentation", None), "to_dict", None))
                else getattr(application, "protection_presentation", None)
            ),        )

    def new_network() -> Network:
        return Network()

    application.dynamic_models = dynamic_models

    lifecycle = ProjectLifecycleService(
        network=network,
        network_factory=new_network,
        context=initial_context,
        loader=load_project,
        saver=save_project,
        activate_network=activate_network,
        project_state_activator=activate_project_state,
        project_state_validator=validate_project_candidate,
    )
    application.attach_project_lifecycle(lifecycle)
    application.revision_service.initialize_from_network(
        topology_revision=network.state.topology_revision,
    )
    # The initial project already owns generation 1; establish the same
    # generation-aware runtime invariant used by every later activation.
    application.control_engine.configure(
        control_service.configuration,
        activation_generation=lifecycle.activation_generation,
    )

    def study_configuration(request: StudyRequest, expected_type: type[Any]) -> Any:
        configuration = request.configuration.get("configuration", request.configuration)
        if not isinstance(configuration, expected_type):
            raise TypeError(
                f"{request.study_type!r} requires {expected_type.__name__}; "
                f"received {type(configuration).__name__}."
            )
        return configuration

    def run_power_flow(request: StudyRequest, execution_context, token: StudyCancellationToken) -> Any:
        if token.cancelled:
            return None
        configuration = study_configuration(request, PowerFlowStudyConfiguration)
        prepared = StudyPreparationService(execution_context).prepare_power_flow(configuration)
        if token.cancelled:
            return None
        analysis = PowerFlowAnalysis.from_prepared(prepared)
        numerical_result = analysis.solve()
        if token.cancelled:
            return None
        if not numerical_result.success:
            raise RuntimeError(
                "Power Flow did not converge: "
                f"{numerical_result.message or 'solver reported success=False'}"
            )
        return analysis.to_engineering_result()

    def run_contingency(request: StudyRequest, execution_context, token: StudyCancellationToken) -> Any:
        if token.cancelled:
            return None
        configuration = study_configuration(request, PowerFlowStudyConfiguration)
        preparation = StudyPreparationService(execution_context)
        request_configuration = request.configuration
        analysis = ContingencyAnalysis(preparation.snapshot.network, configuration)
        result = analysis.run(
            elements=request_configuration.get("elements"),
            contingency_type=request_configuration.get("contingency_type", "N-1"),
            element_types=request_configuration.get("element_types"),
            power_flow_options=request_configuration.get("power_flow_options"),
            voltage_min=request_configuration.get("voltage_min", 0.95),
            voltage_max=request_configuration.get("voltage_max", 1.05),
            thermal_limit=request_configuration.get("thermal_limit", 100.0),
        )
        if token.cancelled:
            return None
        return result

    def run_short_circuit(request: StudyRequest, execution_context, token: StudyCancellationToken) -> Any:
        if token.cancelled:
            return None
        configuration = study_configuration(request, ShortCircuitStudyConfiguration)
        prepared = StudyPreparationService(execution_context).prepare_short_circuit(configuration)
        if token.cancelled:
            return None
        analysis = ShortCircuitAnalysis.from_prepared(prepared)
        result = analysis.run()
        if token.cancelled:
            return None
        if not result.success:
            diagnostics = "; ".join(result.diagnostics) or "solver reported success=False"
            raise RuntimeError(f"Short-Circuit calculation failed: {diagnostics}")
        return result

    def configure_transient_events(request: StudyRequest, event_manager: EventManager, event_state: TransientEventState) -> None:
        typed_configuration = study_configuration(request, TransientStabilityStudyConfiguration)
        # Study Case UI persists the typed configuration under `configuration`;
        # event definitions belong to that configuration's metadata contract.
        # Retain top-level `events` as a compatibility path for existing direct callers.
        events = request.configuration.get("events")
        if events is None:
            events = typed_configuration.metadata.get("events", ())
        if not isinstance(events, (tuple, list)):
            raise TypeError("Transient Stability events must be a sequence of event mappings.")
        fault_operations: list[tuple[float, int, str, str]] = []
        for event in events:
            if not callable(getattr(event, "get", None)):
                raise TypeError("Each Transient Stability event must be a mapping.")
            kind = str(event["type"]).strip().lower()
            time = float(event["time"])
            event_id = str(event["event_id"])
            if not math.isfinite(time):
                raise ValueError(
                    f"Transient event {event_id!r} must have a finite event time."
                )
            if not typed_configuration.start_time <= time <= typed_configuration.end_time:
                raise ValueError(
                    f"Transient event {event_id!r} at t={time:.12g} s falls outside "
                    f"the configured study interval "
                    f"[{typed_configuration.start_time:.12g}, "
                    f"{typed_configuration.end_time:.12g}] s."
                )
            if kind in {"breaker_open", "breaker_close"}:
                breaker_id = str(event["breaker_id"])
                affected_equipment_ids = tuple(
                    str(value) for value in event.get("affected_equipment_ids", ())
                )
                if not affected_equipment_ids:
                    raise ValueError(
                        f"Transient event {event_id!r} ({kind}) must provide "
                        "affected_equipment_ids to change the detached network."
                    )
                known_ids = {
                    item.branch_id
                    for item in (*event_state.snapshot.branches, *event_state.snapshot.transformers)
                } | {item.shunt_id for item in event_state.snapshot.shunts}
                unknown_ids = sorted(set(affected_equipment_ids) - known_ids)
                if unknown_ids:
                    raise ValueError(
                        f"Transient breaker event {event_id!r} references unknown "
                        f"network equipment IDs: {unknown_ids!r}."
                    )
                scheduler = schedule_breaker_open if kind == "breaker_open" else schedule_breaker_close
                scheduler(
                    event_manager, event_state, time, breaker_id,
                    event_id=event_id,
                    affected_equipment_ids=affected_equipment_ids,
                    priority=int(event.get("priority", 0)),
                )
            elif kind == "equipment_state":
                schedule_equipment_state(event_manager, event_state, time, str(event["equipment_id"]), bool(event["conducting"]), event_id=event_id, priority=int(event.get("priority", 0)))
            elif kind == "fault_apply":
                fault = TransientFault(
                    fault_type=FaultType.from_value(event["fault_type"]),
                    bus_id=str(event["bus_id"]),
                    impedance=complex(event.get("impedance", 0.0j)),
                )
                if fault.bus_id not in event_state.snapshot.bus_ids:
                    raise ValueError(
                        f"Transient fault event {event_id!r} references unknown "
                        f"bus ID {fault.bus_id!r}."
                    )
                priority = int(event.get("priority", 0))
                schedule_fault_apply(event_manager, event_state, time, fault, event_id=event_id, priority=priority)
                fault_operations.append((time, priority, event_id, "apply"))
            elif kind == "fault_clear":
                priority = int(event.get("priority", 0))
                schedule_fault_clear(event_manager, event_state, time, event_id=event_id, priority=priority)
                fault_operations.append((time, priority, event_id, "clear"))
            else:
                raise ValueError(f"Unsupported transient event type: {kind!r}.")

        # Preflight the single-active-fault contract using the exact ordering
        # used by EventManager: time, ascending priority, then event ID.
        fault_active = event_state.active_fault is not None
        for _, _, event_id, operation in sorted(fault_operations):
            if operation == "apply":
                if fault_active:
                    raise ValueError(
                        f"Transient fault event {event_id!r} applies a fault while "
                        "another fault is still active; schedule a fault_clear first."
                    )
                fault_active = True
            else:
                if not fault_active:
                    raise ValueError(
                        f"Transient fault-clear event {event_id!r} has no active fault "
                        "in the configured event order."
                    )
                fault_active = False

    def run_transient_stability(request: StudyRequest, execution_context, token: StudyCancellationToken) -> Any:
        if token.cancelled:
            return None
        configuration = study_configuration(request, TransientStabilityStudyConfiguration)
        prepared_power_flow = request.configuration.get("prepared_power_flow")
        power_flow_result = request.configuration.get("power_flow_result")
        if not isinstance(prepared_power_flow, PreparedPowerFlow) or not isinstance(power_flow_result, PowerFlowResult):
            initial_power_flow_configuration = configuration.power_flow_configuration
            if not isinstance(initial_power_flow_configuration, PowerFlowStudyConfiguration):
                raise TypeError(
                    "transient_stability requires an explicit power_flow_configuration "
                    "when prepared runtime inputs are not supplied."
                )
            preparation = StudyPreparationService(execution_context)
            prepared_power_flow = preparation.prepare_power_flow(initial_power_flow_configuration)
            if token.cancelled:
                return None
            initial_power_flow_analysis = PowerFlowAnalysis.from_prepared(prepared_power_flow)
            power_flow_result = initial_power_flow_analysis.solve()
            if not bool(getattr(power_flow_result, "success", False)):
                raise ValueError(
                    "Transient Stability initialization Power Flow did not converge; "
                    "dynamic integration was not started."
                )
        prepared = StudyPreparationService(execution_context).prepare_transient_stability(
            configuration, prepared_power_flow, power_flow_result,
            DynamicMachineModelRegistry(execution_context.project_snapshot.dynamic_models),
        )
        if token.cancelled:
            return None
        machine_system = MultiMachineSystem(prepared.machines)
        event_state = TransientEventState.from_snapshot(prepared.network)
        network_runtime = TransientNetworkRuntime(event_state, machine_system)
        event_manager = EventManager()
        configure_transient_events(request, event_manager, event_state)
        dae_solver = DAESolver(machine_system, network_runtime.solve, prepared.mechanical_powers, integrator=Integrator("RK4"))
        solver = TransientStabilitySolver(dae_solver, start_time=configuration.start_time, end_time=configuration.end_time, dt=configuration.dt, event_manager=event_manager)
        analysis = TransientStabilityAnalysis(
            solver,
            configuration,
            prepared.initial_state,
            provenance={
                "project_id": prepared.project_id,
                "activation_generation": prepared.activation_generation,
                "source_revision": prepared.source_revision,
                "topology_revision": prepared.network.topology_revision,
            },
        )
        result = analysis.run(should_cancel=lambda: token.cancelled)
        if token.cancelled:
            return None
        return result

    application.study_service.register("power_flow", run_power_flow)
    application.study_service.register("contingency", run_contingency)
    application.study_service.register("short_circuit", run_short_circuit)
    application.study_service.register("transient_stability", run_transient_stability)
    return application


__all__ = ["create_application"]
