# ============================================================
# GridForge V2 — Application Composition Root
# ============================================================
# Author: Subhendu Mishra

"""Composition root for the headless GridForge Application layer."""

from __future__ import annotations

from collections.abc import Mapping
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
from core.application.instrumentation import ProjectInstrumentationCollection
from core.persistence.project_package import normalize_package_path
from core.protection.project_configuration import ProtectionProjectConfiguration
from core.control.configuration import ControlConfiguration
from core.application.control_signal_mapping import ControlSignalMapping
from core.protection.runtime import ProtectionRuntime
from core.solver.dynamics import DAESolver, EventManager, Integrator, MultiMachineSystem, TransientStabilitySolver
from core.solver.power_flow.result import PowerFlowResult
from core.solver.short_circuit.fault_types import FaultType

from .application import Application
from .command import Command
from .protection_execution_audit import ProtectionExecutionAuditStore
from .control_dispatch import ControlTripBatchCommand
from .commands.selection_commands import DELETE_SELECTION_BATCH, ENGINEERING_UPDATE_BATCH
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
from .protection_configuration_handlers import ProtectionConfigurationHandlers, MeasurementChannelCommandHandlers
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
from .draft import DraftNetwork, DraftNetworkCollection, DraftCommandHandlers, CommitNetworkHandler
from .creation_schema import creation_contracts_from_definitions
from .services.motor_dynamics_service import MotorDynamicsComposition
from .recovery import ApplicationRecoveryComposition


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
        register_handlers(
            handlers,
            MeasurementChannelCommandHandlers(measurement_channel_service).handlers(),
            "measurement channel",
        )
        # Control editing commands are composed into the same authoritative
        # Application command registry as model and protection commands.
        register_handlers(handlers, ControlCommandHandlers(
            control_service,
            signal_mapping_getter=lambda: application.control_signal_mapping,
            signal_mapping_setter=lambda mapping: application.configure_control_signal_mapping(mapping),
        ).handlers(), "control")
        command_manager = CommandManager(context=context, handlers=handlers)

        def execute_selection_delete_batch(command: Any, _context: Any, transaction: Any) -> Any:
            """Execute supported semantic deletes under one shared transaction/history record."""
            children = tuple(command.payload.get("commands", ()))
            document_id = str(command.payload.get("document_id", "")).strip()
            identity_fields = {
                "model.delete_bus": "bus_id",
                "model.delete_transformer": "transformer_id",
                "model.delete_breaker": "breaker_id",
                "connectivity.remove_simple_wire": "connection_id",
                "draft.remove_equipment": "draft_id",
                "draft.remove_connection": "connection_id",
                "sld.remove_node": "node_id",
                "sld.remove_connection": "connection_id",
            }
            if not children or not document_id:
                raise ValueError("Selection delete batch requires at least one command and one active SLD document.")
            active_presentation = getattr(application, "presentation", None)
            if active_presentation is None or getattr(active_presentation, "document_id", None) != document_id:
                raise ValueError("Selection delete batch does not target the active SLD document.")
            identities: set[tuple[str, str]] = set()
            draft_commands = {"draft.remove_equipment", "draft.remove_connection"}
            for child in children:
                if not isinstance(child, Command) or child.command_type not in identity_fields:
                    raise ValueError("Selection delete batch contains an unsupported semantic delete command.")
                field = identity_fields[child.command_type]
                identity = str(child.payload.get(field, "")).strip()
                if not identity or (child.command_type, identity) in identities:
                    raise ValueError("Selection delete batch contains an empty or duplicate object identity.")
                identities.add((child.command_type, identity))
                if child.command_type in draft_commands and str(child.payload.get("document_id", "")).strip() != document_id:
                    raise ValueError("Draft delete command does not belong to the active SLD document.")

            # Preflight every selected identity against the authoritative
            # Application read boundaries before the first child can mutate.
            # The child handlers retain their domain/dependency checks inside
            # the shared transaction; this pass rejects stale and forged IDs
            # for the complete selection before execution begins.
            network_snapshot = application.read_network()
            elements_by_id = {
                str(item.object_id): item for item in network_snapshot.elements
            }
            wires_by_id = {
                str(item.connection_id): item for item in network_snapshot.simple_wires
            }
            draft_snapshot = application.read_draft_network(document_id)
            draft_snapshot = draft_snapshot if isinstance(draft_snapshot, Mapping) else {}
            draft_equipment_by_id = {
                str(item.get("draft_id", "")): item
                for item in draft_snapshot.get("equipment", ())
                if isinstance(item, Mapping)
            }
            draft_connections_by_id = {
                str(item.get("connection_id", "")): item
                for item in draft_snapshot.get("connections", ())
                if isinstance(item, Mapping)
            }
            sld_document = application.sld_service.document
            expected_element_types = {
                "model.delete_bus": "BUS",
                "model.delete_transformer": "TRANSFORMER",
                "model.delete_breaker": "BREAKER",
            }
            for child in children:
                field = identity_fields[child.command_type]
                identity = str(child.payload.get(field, "")).strip()
                if child.command_type in expected_element_types:
                    element = elements_by_id.get(identity)
                    actual_type = (
                        str(element.element_type).strip().upper()
                        if element is not None else None
                    )
                    if actual_type != expected_element_types[child.command_type]:
                        raise ValueError(
                            f"Selection delete preflight could not resolve {child.command_type} identity {identity!r}."
                        )
                elif child.command_type == "connectivity.remove_simple_wire":
                    if identity not in wires_by_id:
                        raise ValueError(
                            f"Selection delete preflight could not resolve Simple Wire {identity!r}."
                        )
                elif child.command_type == "draft.remove_equipment":
                    if identity not in draft_equipment_by_id:
                        raise ValueError(
                            f"Selection delete preflight could not resolve DraftNetwork equipment {identity!r}."
                        )
                elif child.command_type == "draft.remove_connection":
                    if identity not in draft_connections_by_id:
                        raise ValueError(
                            f"Selection delete preflight could not resolve DraftNetwork connection {identity!r}."
                        )
                elif child.command_type == "sld.remove_node":
                    node = sld_document.model.get_node_optional(identity)
                    if node is None or getattr(node, "equipment_id", None) is not None:
                        raise ValueError(
                            f"Selection delete preflight could not resolve an engineer-owned SLD-only node {identity!r}."
                        )
                    node_properties = getattr(node, "properties", {})
                    if (
                        node_properties.get("projection_source") is not None
                        or node_properties.get("presentation_owner") not in (None, "engineer")
                    ):
                        raise ValueError(f"SLD node {identity!r} is not engineer-owned.")
                    layer = next((item for item in sld_document.layers if item.layer_id == node.layer_id), None)
                    if layer is None or layer.locked:
                        raise ValueError(f"SLD node {identity!r} belongs to a missing or locked layer.")
                    for attached in sld_document.model.connections:
                        if attached.source_node_id != identity and attached.target_node_id != identity:
                            continue
                        attached_properties = attached.properties
                        if (
                            attached_properties.get("core_connection_id")
                            or attached_properties.get("projection_source") is not None
                            or attached_properties.get("connection_kind") == "SIMPLE_WIRE"
                            or attached_properties.get("orphaned_from_core_connection_id")
                            or attached_properties.get("presentation_owner") != "engineer"
                        ):
                            raise ValueError(
                                f"SLD node {identity!r} has a Core-backed, projection-owned, orphaned, or non-engineer connection {attached.connection_id!r}."
                            )
                        attached_layer = next(
                            (item for item in sld_document.layers if item.layer_id == attached.layer_id),
                            None,
                        )
                        if attached_layer is None or attached_layer.locked:
                            raise ValueError(
                                f"Attached SLD connection {attached.connection_id!r} belongs to a missing or locked layer."
                            )
                elif child.command_type == "sld.remove_connection":
                    connection = sld_document.model.get_connection_optional(identity)
                    if connection is None:
                        raise ValueError(
                            f"Selection delete preflight could not resolve engineer-owned SLD connection {identity!r}."
                        )
                    properties = connection.properties
                    if (
                        properties.get("core_connection_id")
                        or properties.get("projection_source") is not None
                        or properties.get("connection_kind") == "SIMPLE_WIRE"
                        or properties.get("orphaned_from_core_connection_id")
                        or properties.get("presentation_owner") != "engineer"
                    ):
                        raise ValueError(
                            f"SLD connection {identity!r} is Core-backed, projection-owned, orphaned, or not engineer-owned."
                        )
                    layer = next((item for item in sld_document.layers if item.layer_id == connection.layer_id), None)
                    if layer is None or layer.locked:
                        raise ValueError(f"SLD connection {identity!r} belongs to a missing or locked layer.")

            # Dependency closure is checked against the complete immutable snapshots
            # before the first child mutation. Core Simple Wires are explicit batch
            # children; DraftNetwork removal otherwise cascades attached draft wires.
            selected_core_equipment_ids = {
                str(child.payload.get(identity_fields[child.command_type], "")).strip()
                for child in children
                if child.command_type in expected_element_types
            }
            selected_simple_wire_ids = {
                str(child.payload.get("connection_id", "")).strip()
                for child in children
                if child.command_type == "connectivity.remove_simple_wire"
            }

            def endpoint_equipment_id(endpoint: Any) -> str | None:
                if not isinstance(endpoint, Mapping):
                    return None
                endpoint_kind = str(endpoint.get("kind", "")).strip().lower()
                if endpoint_kind not in {"terminal", "bus"}:
                    return None
                reference = endpoint.get("endpoint", endpoint)
                if not isinstance(reference, Mapping):
                    return None
                identity = str(reference.get("object_id", "")).strip()
                return identity or None

            selected_bus_ids = {
                str(child.payload.get("bus_id", "")).strip()
                for child in children
                if child.command_type == "model.delete_bus"
            }
            for bus_id in selected_bus_ids:
                dependent_equipment_ids: set[str] = set()
                for element in network_snapshot.elements:
                    if str(element.object_id) == bus_id:
                        continue
                    attributes = element.attributes
                    terminal_connectivity = attributes.get("terminal_connectivity", ())
                    attached_by_terminal = any(
                        isinstance(item, (tuple, list))
                        and len(item) == 2
                        and item[1] is not None
                        and str(item[1]) == bus_id
                        for item in terminal_connectivity
                    )
                    attached_by_branch_endpoint = any(
                        attributes.get(field) is not None
                        and str(attributes.get(field)) == bus_id
                        for field in ("from_endpoint", "to_endpoint")
                    )
                    if attached_by_terminal or attached_by_branch_endpoint:
                        dependent_equipment_ids.add(str(element.object_id))
                omitted_equipment_ids = dependent_equipment_ids - selected_core_equipment_ids
                if omitted_equipment_ids:
                    raise ValueError(
                        "Selection delete preflight is not dependency-closed: Bus "
                        f"{bus_id!r} still has physically attached equipment "
                        f"{sorted(omitted_equipment_ids)!r}. Include supported dependent "
                        "equipment in the same batch; unsupported equipment remains fail-closed."
                    )

            for equipment_id in selected_core_equipment_ids:
                incident_wire_ids = {
                    str(wire.connection_id)
                    for wire in network_snapshot.simple_wires
                    if equipment_id in {
                        endpoint_equipment_id(wire.endpoint_a),
                        endpoint_equipment_id(wire.endpoint_b),
                    }
                }
                omitted_wire_ids = incident_wire_ids - selected_simple_wire_ids
                if omitted_wire_ids:
                    raise ValueError(
                        "Selection delete preflight is not dependency-closed: Core equipment "
                        f"{equipment_id!r} still has unselected Simple Wires "
                        f"{sorted(omitted_wire_ids)!r}. Include every incident Simple Wire "
                        "in the same batch or leave the equipment selected for a later operation."
                    )

            selected_draft_equipment_ids = {
                str(child.payload.get("draft_id", "")).strip()
                for child in children
                if child.command_type == "draft.remove_equipment"
            }
            selected_draft_connection_ids = {
                str(child.payload.get("connection_id", "")).strip()
                for child in children
                if child.command_type == "draft.remove_connection"
            }
            for draft_id in selected_draft_equipment_ids:
                dependent_connection_ids = {
                    str(connection.get("connection_id", "")).strip()
                    for connection in draft_snapshot.get("connections", ())
                    if isinstance(connection, Mapping)
                    and any(
                        isinstance(connection.get(endpoint_name), Mapping)
                        and connection[endpoint_name].get("scope") == "draft"
                        and str(connection[endpoint_name].get("object_id", "")).strip() == draft_id
                        for endpoint_name in ("source", "target")
                    )
                }
                omitted_connection_ids = dependent_connection_ids - selected_draft_connection_ids
                if omitted_connection_ids:
                    raise ValueError(
                        "Selection delete preflight is not dependency-closed: DraftNetwork "
                        f"equipment {draft_id!r} still has unselected connections "
                        f"{sorted(omitted_connection_ids)!r}. Include every attached draft "
                        "connection in the same batch."
                    )

            # Remove connections before their endpoint equipment. The shared
            # transaction journal restores all earlier children if any later
            # child fails, while the outer command creates one history record.
            rank = {
                "connectivity.remove_simple_wire": 0,
                "draft.remove_connection": 0,
                "model.delete_bus": 1,
                "model.delete_transformer": 1,
                "model.delete_breaker": 1,
                "draft.remove_equipment": 1,
                "sld.remove_node": 2,
                "sld.remove_connection": 0,
            }
            ordered = tuple(sorted(children, key=lambda child: rank[child.command_type]))
            for child in ordered:
                command_manager.execute_in_transaction(child, transaction)
            return ApplicationResult.success_result(
                message=f"Atomically deleted {len(ordered)} selected SLD objects.",
                metadata={
                    "document_id": document_id,
                    "child_command_ids": tuple(str(child.command_id) for child in ordered),
                    "atomic": True,
                },
            )

        command_manager.register_handler(
            DELETE_SELECTION_BATCH,
            execute_selection_delete_batch,
        )

        def execute_engineering_update_batch(command: Any, _context: Any, transaction: Any) -> Any:
            """Execute canonical per-equipment updates in one shared transaction."""
            children = tuple(command.payload.get("commands", ()))
            if len(children) < 2:
                raise ValueError("Engineering update batch requires at least two equipment updates.")
            identities: set[tuple[str, str]] = set()
            excluded = {
                "model.update_current_transformer",
                "model.update_potential_transformer",
                "model.update_capacitive_voltage_transformer",
            }
            for child in children:
                if (
                    not isinstance(child, Command)
                    or not child.command_type.startswith("model.update_")
                    or child.command_type in excluded
                    or not type(child).__name__.startswith("Update")
                ):
                    raise ValueError("Engineering update batch accepts only supported canonical model update commands.")
                identity_fields = tuple(
                    key for key in child.payload
                    if key.endswith("_id") and key not in {"document_id"}
                )
                if len(identity_fields) != 1:
                    raise ValueError("Engineering update batch child must identify exactly one equipment target.")
                identity = str(child.payload.get(identity_fields[0], "")).strip()
                key = (child.command_type, identity)
                if not identity or key in identities:
                    raise ValueError("Engineering update batch contains an empty or duplicate equipment identity.")
                identities.add(key)

            # All child commands have already been prepared and canonically
            # validated by EngineeringUpdatePreparer before this handler runs.
            # The child handlers now share one transaction and one history entry.
            for child in children:
                command_manager.execute_in_transaction(child, transaction)
            return ApplicationResult.success_result(
                message=f"Atomically updated {len(children)} selected equipment items.",
                metadata={
                    "child_command_ids": tuple(str(child.command_id) for child in children),
                    "equipment_count": len(children),
                    "atomic": True,
                },
            )

        command_manager.register_handler(
            ENGINEERING_UPDATE_BATCH,
            execute_engineering_update_batch,
        )

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
        def draft_provider(document_id=None):
            if application is None:
                return None
            return application._draft_network_for_command(document_id)
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
    protection_execution_audit_store = ProtectionExecutionAuditStore(
        Path.home() / ".gridforge" / "audit" / "protection-execution.sqlite3"
    )
    application = Application(
        command_manager=command_manager,
        read_service=read_service,
        validation_service=validation_service,
        measurement_channel_service=measurement_channel_service,
        control_service=control_service,
        protection_execution_audit_store=protection_execution_audit_store,
    )
    application.instrumentation_collection = ProjectInstrumentationCollection(initial_context.project_id)
    measurement_channel_service.bind_execution_lock(application._execution_lock)

    def measurement_channel_is_referenced(channel_id: str) -> bool:
        if protection_configuration_service.references_measurement_channel(channel_id):
            return True
        active_context = application.project_lifecycle.context
        mappings = [application.control_signal_mapping]
        if (
            active_context is not None
            and application._dynamic_control_signal_mapping_project_id == active_context.project_id
        ):
            mappings.append(application._dynamic_control_signal_mapping)
        return any(
            mapping is not None
            and any(
                binding.source.domain == "measurement"
                and binding.source.object_id == channel_id
                for binding in mapping.bindings
            )
            for mapping in mappings
        )

    measurement_channel_service.bind_channel_usage_validator(
        measurement_channel_is_referenced
    )
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
        previous_draft_collection = application.draft_network_collection
        previous_draft = application.draft_network
        previous_study_cases = dict(application._study_cases)
        previous_instrumentation_collection = application.instrumentation_collection

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
            application.set_draft_network_collection(previous_draft_collection)
            if previous_draft_collection is None:
                application.set_draft_network(previous_draft)
            application._study_cases = dict(previous_study_cases)
            application.instrumentation_collection = previous_instrumentation_collection
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
                application.set_draft_network_collection(None)
                control_service.activate(ControlConfiguration.empty("closed-project"))
                application.control_engine.deactivate()
                application.configure_control_signal_mapping(None)
                protection_configuration_service.deactivate()
                application.protection_runtime = None
                application.protection_presentation = None
                dynamic_models.replace(())
                application.set_draft_network(DraftNetwork.empty(context.project_id if context is not None else "closed-project", generation))
                application._study_cases = {}
                application.instrumentation_collection = None
            else:
                application.instrumentation_collection = (
                    loaded.instrumentation if loaded is not None and loaded.instrumentation is not None
                    else ProjectInstrumentationCollection(context.project_id)
                )
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
                # Draft authoring state is activated as one document-keyed
                # collection, never as a project-wide mutable graph.
                application.set_draft_network_collection(None)
                loaded_collection = getattr(loaded, "draft_network_collection", None) if loaded is not None else None
                if loaded_collection is not None:
                    application.set_draft_network_collection(
                        DraftNetworkCollection.from_dict(
                            loaded_collection.to_dict(),
                            project_id=context.project_id,
                            activation_generation=generation,
                        )
                    )
                else:
                    presentation_data = getattr(loaded, "presentation_collection", None) if loaded is not None else None
                    documents = presentation_data.get("documents", ()) if isinstance(presentation_data, Mapping) else ()
                    document_ids = tuple(
                        str(item.get("document_id", "")).strip()
                        for item in documents
                        if isinstance(item, Mapping) and item.get("document_id")
                    )
                    active_document_id = (
                        presentation_data.get("active_document_id")
                        if isinstance(presentation_data, Mapping) else None
                    )
                    loaded_draft = getattr(loaded, "draft_network", None) if loaded is not None else None
                    legacy_data = loaded_draft.to_dict() if loaded_draft is not None else None
                    collection = DraftNetworkCollection.from_dict(
                        legacy_data,
                        project_id=context.project_id,
                        activation_generation=generation,
                        document_ids=document_ids,
                        legacy_document_id=active_document_id or (f"{context.project_id}:sld" if not document_ids else None),
                    )
                    application.set_draft_network_collection(collection)
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
            instrumentation = loaded.instrumentation
            if instrumentation is not None:
                if instrumentation.project_id != context.project_id:
                    raise ValueError("Instrumentation collection project_id does not match the candidate project.")
                for installation in instrumentation.installations:
                    try:
                        monitored = candidate_network.get_by_identity(installation.monitored_equipment_id)
                    except KeyError as exc:
                        raise ValueError(f"Instrumentation installation {installation.installation_id!r} references missing monitored equipment {installation.monitored_equipment_id!r}.") from exc
                    expected_type = installation.monitored_equipment_type.replace("-", "_").upper()
                    actual_type = str(getattr(monitored, "element_type", "")).upper()
                    if expected_type != actual_type:
                        raise ValueError(f"Instrumentation installation {installation.installation_id!r} expects {expected_type!r}, found {actual_type!r}.")
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

    def save_project(context, active_network, presentation, path, presentation_collection=None, protection_snapshot=None):
        persistence.save(
            context,
            active_network,
            presentation,
            path,
            dynamic_models=dynamic_models.all(),
            protection_configuration=protection_snapshot,
            measurement_definitions=measurement_channel_service.serialize_definitions(),
            control_configuration=control_service.configuration,
            control_signal_mapping=application.control_signal_mapping,
            draft_network_collection=application.draft_network_collection,
            presentation_collection=presentation_collection,
            study_cases=application.study_cases,
            instrumentation=application.instrumentation_collection,
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
    def protection_snapshot_for_project(project_id: str, generation: int):
        configuration = protection_configuration_service.configuration
        if configuration is None:
            raise RuntimeError("Active project Protection configuration is unavailable.")
        if configuration.project_id != project_id:
            raise RuntimeError("Active Protection configuration belongs to a different project.")
        return configuration.create_snapshot(generation)

    lifecycle.configure_protection_snapshot_provider(protection_snapshot_for_project)
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
                breaker_id = event["breaker_id"]
                affected_equipment_ids = tuple(event.get("affected_equipment_ids", ()))
                if not affected_equipment_ids:
                    raise ValueError(
                        f"Transient event {event_id!r} ({kind}) must provide "
                        "affected_equipment_ids to change the detached network."
                    )
                breaker_id, affected_equipment_ids = event_state.validate_switch_target(
                    breaker_id, affected_equipment_ids
                )
                scheduler = schedule_breaker_open if kind == "breaker_open" else schedule_breaker_close
                scheduler(
                    event_manager, event_state, time, breaker_id,
                    event_id=event_id,
                    affected_equipment_ids=affected_equipment_ids,
                    priority=int(event.get("priority", 0)),
                )
            elif kind == "equipment_state":
                equipment_id = event_state.validate_equipment_target(event["equipment_id"])
                conducting = event["conducting"]
                if not isinstance(conducting, bool):
                    raise TypeError("Transient equipment_state event conducting must be an explicit bool.")
                schedule_equipment_state(
                    event_manager, event_state, time, equipment_id, conducting,
                    event_id=event_id, priority=int(event.get("priority", 0))
                )
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
            # PreparedTransientStability is the authoritative source for
            # detached model/state descriptors and execution provenance.
            provenance=prepared.provenance,
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


def recover_application(
    degraded_application: Application,
    project_path: str | Path,
    *,
    measurement_source_verifier: Callable[[MeasurementSampleAttestation, Any, float | complex], bool] | None = None,
    measurement_clock: Callable[[], float] | None = None,
    measurement_clock_domain: str | None = None,
    max_measurement_source_clock_skew_seconds: float = 2.0,
    synchronism_bypass_provider: Callable[[Breaker], BypassAuthorization | None] | None = None,
    recovery_composition: ApplicationRecoveryComposition | None = None,
) -> Application:
    """Compose a replacement Application from the last canonical saved package.

    The supplied Application must already report recovery_required. It is never
    reset, reopened, or otherwise mutated. A new Network, CommandManager,
    CommandHistory, services, and Application are composed first; the canonical
    package is then loaded and activated through the ordinary transactional
    project lifecycle. The replacement is returned only after its project
    identity, lifecycle state, complete presentation collection, and workspace
    adoption have been verified. A composition owner must provide
    `recovery_composition`; its configuration callback runs on the replacement
    before package activation, and its validation callback runs before return.
    Missing presentation or workspace restoration is a hard failure, not a
    successful Core-only recovery.

    UI/application-shell owners must swap to the returned instance only after
    this function succeeds. On any failure the old instance remains the
    authoritative read-only diagnostic object and no replacement is returned.
    Unsaved recovery artifacts are deliberately not replayed.
    """
    if not isinstance(degraded_application, Application):
        raise TypeError("degraded_application must be an Application instance.")
    before = degraded_application.recovery_status
    if not before.recovery_required:
        raise ValueError("The supplied Application does not require recovery.")
    if before.project_id is None:
        raise RuntimeError(
            "Recovery requires an identified active project; refusing to activate an unrelated package."
        )
    if not isinstance(recovery_composition, ApplicationRecoveryComposition):
        raise TypeError(
            "recovery_composition must provide pre-activation configuration and post-activation validation."
        )
    target = normalize_package_path(project_path)
    if not target.is_dir():
        raise FileNotFoundError(f"Canonical GridForge project package does not exist: {target}")

    replacement = create_application(
        Network(),
        measurement_source_verifier=measurement_source_verifier,
        measurement_clock=measurement_clock,
        measurement_clock_domain=measurement_clock_domain,
        max_measurement_source_clock_skew_seconds=max_measurement_source_clock_skew_seconds,
        synchronism_bypass_provider=synchronism_bypass_provider,
    )
    try:
        # Install the complete presentation and fresh workspace activation
        # contracts on this exact candidate before any package state is loaded.
        recovery_composition.configure_replacement(replacement)
        presentation_contract = replacement.project_lifecycle.capture_presentation_configuration()
        required_contracts = (
            "factory",
            "serializer",
            "deserializer",
            "activator",
            "collection_serializer",
            "collection_deserializer",
            "collection_activator",
            "collection_snapshot_provider",
        )
        missing_contracts = tuple(
            name for name in required_contracts
            if getattr(presentation_contract, name, None) is None
        )
        if missing_contracts:
            raise RuntimeError(
                "Recovery composition did not configure the complete presentation contract: "
                + ", ".join(missing_contracts)
            )

        # ProjectPersistenceService.load() performs manifest/schema/migration,
        # Core deserialization, SLD association, and cross-domain validation.
        context = replacement.open_project(str(target), decision="discard")
        # Application activation commits the project and presentation lifecycle,
        # but direct Application.open_project() does not publish the UI adapter's
        # ProjectWorkspaceChanged notification. Synchronize the hidden candidate
        # shell before validating its projections and before publishing the shell.
        recovery_composition.synchronize_replacement(replacement, context)
        # A clean Core command boundary is not sufficient for a usable GridForge
        # project: the canonical package also carries the engineer's SLD
        # presentation. The headless bootstrap must not silently accept a load
        # that skipped presentation deserialization because no presentation
        # contract was configured by the composition owner.
        presentation = replacement.presentation
        if presentation is None:
            raise RuntimeError(
                "Replacement Application did not restore the project presentation; "
                "recovery requires a presentation-aware composition contract."
            )
        if getattr(presentation, "project_id", None) != context.project_id:
            raise RuntimeError(
                "Replacement project presentation does not belong to the canonical project identity."
            )

        # The Application's active document must be the active member of the
        # restored canonical collection, not a newly generated default SLD.
        collection = presentation_contract.collection_snapshot_provider()
        documents = getattr(collection, "documents", None)
        active_document = getattr(collection, "active_document", None)
        active_document_id = getattr(collection, "active_document_id", None)
        if not documents or active_document is not presentation:
            raise RuntimeError(
                "Replacement presentation collection is empty or its active document "
                "does not match Application.presentation."
            )
        if active_document_id != getattr(presentation, "document_id", None):
            raise RuntimeError(
                "Replacement presentation collection active_document_id does not match "
                "the Application-active document."
            )
        document_ids = tuple(getattr(item, "document_id", None) for item in documents)
        if len(set(document_ids)) != len(document_ids) or any(
            getattr(item, "project_id", None) != context.project_id for item in documents
        ):
            raise RuntimeError(
                "Replacement presentation collection contains duplicate IDs or "
                "documents bound to another project."
            )

        # This callback validates UI-owned adoption (DocumentManager, logical
        # SLD views, active tab, and projection bindings) without importing UI
        # types into the headless Application/Core packages.
        recovery_composition.validate_replacement(replacement, context)

        status = replacement.recovery_status
        if status.recovery_required or status.command_integrity_state != "CLEAN":
            raise RuntimeError("Replacement Application did not establish a clean command boundary.")
        if status.project_lifecycle_state != "ACTIVE":
            raise RuntimeError(
                f"Replacement project lifecycle is not ACTIVE: {status.project_lifecycle_state!r}."
            )
        if context.project_id != status.project_id:
            raise RuntimeError("Replacement project identity does not match its recovery status.")
        if context.project_id != before.project_id:
            raise RuntimeError(
                "Canonical package project identity does not match the degraded Application's project."
            )
        if context.path is None or Path(context.path).resolve() != target.resolve():
            raise RuntimeError("Replacement Application activated a different project package.")
        after = degraded_application.recovery_status
        if (
            after.command_integrity_state != before.command_integrity_state
            or after.project_lifecycle_state != before.project_lifecycle_state
            or after.project_id != before.project_id
            or after.activation_generation != before.activation_generation
        ):
            raise RuntimeError("The degraded Application changed during replacement composition.")

        # Publish the staged shell only after every Core, presentation,
        # workspace, and degraded-instance integrity check has passed.
        recovery_composition.commit_replacement(replacement)
        return replacement
    except BaseException:
        # The composition owner holds UI/workspace resources that are outside
        # the headless Application's ownership. Dispose that staged graph before
        # releasing the candidate's executor; disposal is required to be safe
        # even when configuration or activation failed partway through.
        try:
            recovery_composition.dispose_replacement(replacement)
        finally:
            replacement._commit_executor.shutdown(wait=False, cancel_futures=True)
        raise


__all__ = ["create_application", "recover_application"]
