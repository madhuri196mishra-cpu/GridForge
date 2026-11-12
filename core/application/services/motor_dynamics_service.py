"""Canonical Application-owned draft service for the optional motor extension.

Author: Subhendu Mishra

This module has no Qt dependency. Motor placement and Bus connectivity are
recorded in DraftNetwork and pass through the existing Application transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
import re
from typing import Any, Mapping
from uuid import uuid4

from core.application.draft.network import DraftConnection, DraftEndpointReference, DraftEquipment
from core.application.results import ApplicationResult

ADD_DRAFT_MOTOR_PLACEMENT = "motor.add_draft_placement"
CONNECT_MOTOR_TO_BUS = "motor.connect_to_bus"


class MotorDraftService:
    def __init__(self, application: Any) -> None:
        if application is None:
            raise ValueError("Application is required.")
        self._application = application

    def _draft(self):
        draft = self._application.draft_network
        if draft is None:
            raise RuntimeError("Motor placement requires an active Application DraftNetwork.")
        return draft

    @staticmethod
    def _record_undo(draft: Any, transaction: Any) -> None:
        snapshot = draft.to_dict()
        transaction.record_undo(
            lambda snapshot=snapshot, draft=draft: draft.restore_snapshot(snapshot)
        )

    def add_draft_motor(self, *, command: Any, context: Any, transaction: Any) -> ApplicationResult[Any]:
        draft = self._draft()
        payload = command.payload
        motor_id = payload.get("motor_id")
        if not isinstance(motor_id, str) or not motor_id.strip():
            raise ValueError("Motor draft identity must be a non-empty string.")
        motor_id = motor_id.strip()
        coordinates = []
        for field in ("x", "y"):
            value = payload.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"Motor placement {field} coordinate must be numeric.")
            if not isfinite(float(value)):
                raise ValueError(f"Motor placement {field} coordinate must be finite.")
            coordinates.append(float(value))

        contract = self._application.creation_contract_registry.resolve("motor")
        parameters = payload.get("parameters", {})
        if not isinstance(parameters, Mapping):
            raise TypeError("Motor parameters must be a mapping.")
        if any(not isinstance(key, str) or not key.strip() for key in parameters):
            raise TypeError("Motor parameter names must be non-empty strings.")
        if not isinstance(contract, Mapping):
            raise TypeError("Resolved motor creation contract must be a mapping.")
        parameter_contract = contract.get("parameter_contract")
        if not isinstance(parameter_contract, (list, tuple)):
            raise TypeError("Motor parameter_contract must be a list or tuple.")
        allowed_parameters = set()
        for item in parameter_contract:
            if not isinstance(item, Mapping):
                raise TypeError("Each motor parameter contract entry must be a mapping.")
            parameter_id = item.get("parameter_id")
            if (
                not isinstance(parameter_id, str)
                or not parameter_id
                or parameter_id != parameter_id.strip()
            ):
                raise ValueError(
                    "Each motor parameter contract entry requires a canonical non-empty parameter_id."
                )
            if parameter_id in allowed_parameters:
                raise ValueError(
                    f"Duplicate motor parameter contract ID: {parameter_id!r}."
                )
            allowed_parameters.add(parameter_id)
        unknown = set(parameters) - allowed_parameters
        if unknown:
            raise ValueError("Unsupported motor engineering parameters: " + ", ".join(sorted(unknown)))
        equipment = DraftEquipment(
            draft_id=motor_id,
            equipment_type="motor",
            display_name="Motor",
            terminal_contract=tuple(contract.get("terminal_contract", ())),
            engineering_data=dict(parameters),
            placement=(coordinates[0], coordinates[1]),
            presentation={"symbol_type": "motor"},
            command_type=str(contract["command_type"]),
            id_field=str(contract["id_field"]),
            parameter_mapping=dict(contract.get("parameter_mapping", {})),
            endpoint_mapping=dict(contract.get("endpoint_mapping", {})),
            required_connection_terminals=tuple(contract.get("required_connection_terminals", ())),
            parameter_contract=tuple(contract.get("parameter_contract", ())),
            conditional_requirements=tuple(contract.get("conditional_requirements", ())),
        )
        self._record_undo(draft, transaction)
        draft.add_equipment(equipment)
        return ApplicationResult.success_result(value={"draft_id": motor_id, "equipment_type": "motor"})

    def connect_motor_to_bus(self, *, command: Any, context: Any, transaction: Any) -> ApplicationResult[Any]:
        draft = self._draft()
        payload = command.payload
        identifiers = {}
        for field in ("motor_id", "bus_id", "attachment_id"):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Motor connection {field} must be a non-empty string.")
            identifiers[field] = value.strip()
        motor_id = identifiers["motor_id"]
        bus_id = identifiers["bus_id"]
        attachment_id = identifiers["attachment_id"]
        if not re.fullmatch(r"attachment-(?:[0-8])", attachment_id):
            raise ValueError(
                "attachment_id must use canonical 'attachment-N' form "
                "with an index in the supported range 0..8."
            )
        motor = draft.require_equipment(motor_id)
        if motor.equipment_type != "motor":
            raise ValueError(f"Draft equipment {motor_id!r} is not a motor.")
        draft_bus = next((item for item in draft.equipment if item.draft_id == bus_id), None)
        if draft_bus is not None:
            if draft_bus.equipment_type != "bus":
                raise ValueError(f"Draft endpoint {bus_id!r} is not a Bus.")
            bus_endpoint = DraftEndpointReference.bus(
                bus_id=bus_id, attachment_id=attachment_id, scope="draft"
            )
        else:
            # Resolve committed Core objects only through the immutable
            # Application read boundary. Never accept a guessed Core-scoped ID
            # and defer its existence failure until draft commit.
            try:
                core_bus = self._application.read_element("bus", bus_id)
            except KeyError as exc:
                raise ValueError(
                    f"Committed Core Bus {bus_id!r} does not exist in the active project."
                ) from exc
            if str(getattr(core_bus, "element_type", "") or "").strip().lower() != "bus":
                raise ValueError(
                    f"Committed Core endpoint {bus_id!r} is not a Bus."
                )
            bus_endpoint = DraftEndpointReference.bus(
                bus_id=bus_id, attachment_id=attachment_id, scope="core"
            )
        connection = DraftConnection(
            connection_id="motor-wire-" + uuid4().hex,
            source=DraftEndpointReference.terminal(
                draft_id=motor_id, equipment_type="motor", terminal_role="terminal"
            ),
            target=bus_endpoint,
            connection_kind="simple_wire",
        )
        draft.validate_connection_candidate(connection)
        self._record_undo(draft, transaction)
        draft.add_connection(connection)
        return ApplicationResult.success_result(
            value={"connection_id": connection.connection_id, "motor_id": motor_id, "bus_id": bus_id}
        )


class MotorDynamicsComposition:
    """Application-owned adapter for optional plugin command registration."""

    def __init__(self, application: Any) -> None:
        self.application = application
        self.motor_service = MotorDraftService(application)

    def register_motor_extension(self, *, plugin: Any, command_handlers: Mapping[str, Any]):
        plugin_id = getattr(plugin, "plugin_id", None)
        if (
            not isinstance(plugin_id, str)
            or not plugin_id.strip()
            or plugin_id != plugin_id.strip()
        ):
            raise ValueError(
                "Motor extension plugin must expose a stable, canonical plugin_id."
            )
        if not isinstance(command_handlers, Mapping):
            raise TypeError("Motor extension command_handlers must be a mapping.")
        expected = {ADD_DRAFT_MOTOR_PLACEMENT, CONNECT_MOTOR_TO_BUS}
        if set(command_handlers) != expected:
            raise ValueError("Motor extension must register exactly its two motor command handlers.")
        if any(not callable(handler) for handler in command_handlers.values()):
            raise TypeError("Motor extension handlers must be callable.")

        # Capture a stable snapshot; caller mutation must not change the handler set.
        handlers = dict(command_handlers)
        factory = lambda _command_manager, handlers=handlers: dict(handlers)
        self.application.register_extension_handler_factory(plugin_id, factory)
        registered = True

        def teardown() -> None:
            nonlocal registered
            if not registered:
                return
            # Keep this callback retryable if Application teardown raises before
            # ownership removal completes. A False result means this factory no
            # longer owns the registration (for example, stale teardown).
            self.application.unregister_extension_handler_factory(
                plugin_id,
                expected_factory=factory,
            )
            registered = False

        return teardown


@dataclass(frozen=True, slots=True)
class ElectricalDynamicsCapability:
    """Truthful host capability state; registration never implies execution."""

    model_registered: bool
    solver_adapter_available: bool
    executable_study: bool
    fully_coupled_network_execution: bool
    measurement_point_normalization_available: bool
    measurement_channel_access_available: bool
    core_terminal_injection_contract_available: bool
    network_coupling_protocol_available: bool




class ElectricalDynamicsApplicationComposition(MotorDynamicsComposition):
    """Single Application-owned host composition shared by dynamics plugins.

    This first integration slice exposes only capabilities backed by current
    Application services. Generator/load execution and coupled transient
    execution remain unavailable until their host solvers and study loop exist.
    """

    def __init__(self, application: Any) -> None:
        super().__init__(application)
        from core.application.electrical_dynamics_read_models import MotorSimulationReadModelStore

        self.motor_read_models = MotorSimulationReadModelStore()
        self.generator_transient_solver = None
        self.generator_augmented_transient_solver = None
        self.load_transient_solver = None
        from core.application.events import ProjectClosed, ProjectLoaded

        self._project_events = (ProjectLoaded, ProjectClosed)
        self._project_event_callback = self._on_project_transition
        self._closed = False
        for event_type in self._project_events:
            application.event_bus.subscribe(event_type, self._project_event_callback)

    def _on_project_transition(self, _event: Any) -> None:
        """Invalidate project-scoped projections only after a committed transition."""
        if not self._closed:
            self.motor_read_models.clear()

    def close(self) -> None:
        """Release Application-owned lifecycle subscriptions at Application shutdown."""
        if self._closed:
            return
        errors: list[BaseException] = []
        for event_type in reversed(self._project_events):
            try:
                self.application.event_bus.unsubscribe(
                    event_type, self._project_event_callback
                )
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(
                "Electrical dynamics composition shutdown incomplete: "
                + "; ".join(f"{type(error).__name__}: {error}" for error in errors)
            ) from errors[0]
        self.motor_read_models.clear()
        self._closed = True

    @property
    def dynamic_component_contracts(self) -> Mapping[str, Any]:
        """Expose Core-owned dynamic contracts without creating plugin copies."""
        from types import MappingProxyType
        from core.solver.dynamics import (
            DynamicComponentBinding,
            DynamicComponentEvaluation,
            DynamicComponentInputs,
            DynamicStateComponent,
            DynamicTerminalInjection,
            aggregate_dynamic_terminal_injections,
        )

        return MappingProxyType({
            "component_binding": DynamicComponentBinding,
            "component_evaluation": DynamicComponentEvaluation,
            "component_inputs": DynamicComponentInputs,
            "state_component": DynamicStateComponent,
            "terminal_injection": DynamicTerminalInjection,
            "aggregate_terminal_injections": aggregate_dynamic_terminal_injections,
        })

    @property
    def network_coupling_contract(self) -> Any:
        """Expose the Core stage-solver protocol, not a fabricated solver adapter."""
        from core.solver.dynamics import DynamicNetworkSolver
        return DynamicNetworkSolver

    @property
    def measurement_channels(self) -> tuple[Any, ...]:
        """Read the active canonical Application measurement-channel snapshot."""
        return tuple(self.application.measurement_channel_service.channels)

    @property
    def capability_report(self) -> Mapping[str, ElectricalDynamicsCapability]:
        """Report host capabilities without equating catalogue registration to execution."""
        from types import MappingProxyType

        def report(*, registered: bool, solver: bool, study: bool) -> ElectricalDynamicsCapability:
            return ElectricalDynamicsCapability(
                model_registered=registered,
                solver_adapter_available=solver,
                executable_study=study,
                fully_coupled_network_execution=False,
                measurement_point_normalization_available=False,
                measurement_channel_access_available=True,
                core_terminal_injection_contract_available=True,
                network_coupling_protocol_available=True,
            )

        return MappingProxyType({
            "generator": report(registered=False, solver=False, study=False),
            "motor": report(registered=True, solver=True, study=True),
            "load": report(registered=False, solver=False, study=False),
        })


__all__ = [
    "ADD_DRAFT_MOTOR_PLACEMENT",
    "CONNECT_MOTOR_TO_BUS",
    "MotorDraftService",
    "MotorDynamicsComposition",
    "ElectricalDynamicsApplicationComposition",
    "ElectricalDynamicsCapability",
]
