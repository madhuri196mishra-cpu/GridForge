# ============================================================
# File: ui/projection/selection_projection_coordinator.py
# GridForge V2 — Selection Projection Coordinator
# Author: Subhendu Mishra
# ============================================================
"""Project transient UI selection into the PropertiesPanel through Application."""

from __future__ import annotations

from typing import Any, Mapping

from core.application.events import (
    ApplicationEvent,
    DraftChanged,
    ElementRemoved,
    ElementUpdated,
    NetworkChanged,
    NetworkCommitted,
    ProjectClosed,
    ProjectLoaded,
    ProtectionChanged,
    SimpleWireConnectionCreated,
    SimpleWireConnectionRemoved,
)

from .projection_state import EngineeringParameterState, ProjectionState
from ui.creation.creation_definition import creation_definition_for


class SelectionProjectionCoordinator:
    """Project selection state using Application read models.

    Application events arrive through ``UIProjectionCoordinator``. The only
    direct event source retained here is ``SelectionManager.selection_changed``
    because selection is transient UI interaction state rather than a domain
    event.
    """

    event_types = (
        ElementUpdated,
        ElementRemoved,
        NetworkChanged,
        NetworkCommitted,
        SimpleWireConnectionCreated,
        SimpleWireConnectionRemoved,
        DraftChanged,
        ProjectLoaded,
        ProjectClosed,
        ProtectionChanged,
    )

    def __init__(
        self,
        *,
        selection_manager: Any,
        application: Any = None,
        properties_panel: Any = None,
    ) -> None:
        if selection_manager is None:
            raise ValueError("selection_manager must not be None.")
        self.selection_manager = selection_manager
        self.application = None
        self.properties_panel = properties_panel
        self._disposed = False
        self._batch_projection_active = False

        signal = getattr(selection_manager, "selection_changed", None)
        if signal is None or not callable(getattr(signal, "connect", None)):
            raise TypeError("selection_manager must expose a selection_changed signal.")
        signal.connect(self._on_selection_changed)

        if application is not None:
            self.attach_application(application)

    def attach_application(self, application: Any) -> None:
        self._ensure_active()
        if application is None:
            raise ValueError("application must not be None.")
        if not callable(getattr(application, "read_element", None)):
            raise TypeError("application must provide read_element().")
        if not callable(getattr(application, "read_network", None)):
            raise TypeError("application must provide read_network().")
        if not callable(getattr(application, "read_simple_wire", None)):
            raise TypeError("application must provide read_simple_wire().")
        if not callable(getattr(application, "read_draft_network", None)):
            raise TypeError("application must provide read_draft_network().")
        self.application = application
        self.refresh()

    def detach_application(self) -> Any:
        application = self.application
        self.application = None
        self.clear_projection()
        return application

    def set_properties_panel(self, properties_panel: Any) -> None:
        self._ensure_active()
        self.properties_panel = properties_panel
        self.refresh()

    def refresh(self, event: ApplicationEvent | None = None) -> None:
        self._ensure_active()
        if isinstance(event, (ProjectLoaded, ProjectClosed)):
            self.selection_manager.clear()
            self.clear_projection()
            return

        # Only configuration events targeting the selected relay may refresh
        # its Properties projection. Runtime cycle telemetry is not a config
        # mutation and unrelated relay changes must not churn the inspector.
        if isinstance(event, ProtectionChanged) and not self._is_selected_protection_target(event):
            return

        transition_operation = None
        if isinstance(event, NetworkCommitted):
            transition_operation = "commit"
        elif isinstance(event, NetworkChanged):
            operation = event.payload.get("operation")
            # NetworkChanged is also emitted for unrelated topology edits. The
            # commit-specific created_elements mapping identifies this history
            # entry without relying on presentation-only metadata.
            if operation in {"undo", "redo"} and "created_elements" in event.payload:
                transition_operation = str(operation)

        if transition_operation is not None:
            # Draft IDs are intentionally distinct from deterministic Core IDs.
            # Reconcile transient selection across commit, undo and redo using
            # the identity mapping emitted by the authoritative commit handler.
            selected_before_transition = tuple(self.selection_manager.get_selected_ids())
            created_elements = event.payload.get("created_elements", ())
            core_ids_by_draft_id = {
                str(item.get("draft_id")): str(item.get("core_id"))
                for item in created_elements
                if isinstance(item, Mapping)
                and item.get("draft_id")
                and item.get("core_id")
            }
            draft_ids_by_core_id = {
                core_id: draft_id for draft_id, core_id in core_ids_by_draft_id.items()
            }
            draft_snapshot = self.application.read_draft_network() if self.application is not None else None
            existing_draft_ids = {
                str(item.get("draft_id"))
                for item in (draft_snapshot or {}).get("equipment", ())
                if item.get("draft_id")
            }
            for selected_id in selected_before_transition:
                selected_key = str(selected_id)
                if transition_operation in {"commit", "redo"}:
                    core_id = core_ids_by_draft_id.get(selected_key)
                    if core_id is not None:
                        self.selection_manager.select_single(core_id)
                        return
                elif transition_operation == "undo":
                    draft_id = draft_ids_by_core_id.get(selected_key)
                    if draft_id is not None and draft_id in existing_draft_ids:
                        self.selection_manager.select_single(draft_id)
                        return

        selected_ids = tuple(dict.fromkeys(self.selection_manager.get_selected_ids()))
        if self._batch_projection_active and len(selected_ids) > 1:
            return
        if not selected_ids or self.application is None:
            self.clear_projection()
            return

        removed_id = self._selected_event_id(event) if isinstance(
            event, (ElementRemoved, SimpleWireConnectionRemoved)
        ) else None
        if removed_id is not None and any(str(value) == str(removed_id) for value in selected_ids):
            self.selection_manager.clear()
            self.clear_projection()
            return
        if isinstance(event, ElementUpdated) and self._selected_event_id(event) not in selected_ids:
            return

        if len(selected_ids) > 1 and self.properties_panel is not None:
            temporary_selection = getattr(self.selection_manager, "temporary_single_selection", None)
            batch_setter = getattr(self.properties_panel, "set_batch_targets", None)
            if callable(temporary_selection) and callable(batch_setter):
                projected: list[ProjectionState] = []
                self._batch_projection_active = True
                try:
                    for selected_id in selected_ids:
                        with temporary_selection(selected_id):
                            self.refresh()
                            target = getattr(self.properties_panel, "target", None)
                            if isinstance(target, ProjectionState):
                                projected.append(target)
                finally:
                    self._batch_projection_active = False
                if (
                    len(projected) == len(selected_ids)
                    and all(state.identity_kind == "element" for state in projected)
                    and len({state.display_type.strip().lower() for state in projected}) == 1
                ):
                    batch_setter(tuple(projected))
                else:
                    self.clear_projection()
                return

        object_id = selected_ids[0]
        if removed_id is not None and any(
            str(selected_id) == str(removed_id) for selected_id in selected_ids
        ):
            # Additive selection may contain several objects. Reconcile against
            # the complete selection, not only its first entry, or a deleted
            # Core identity can remain selected after its SLD projection is
            # removed. SelectionManager is intentionally transient; clearing
            # the selection is safer than retaining a stale mixed selection.
            self.selection_manager.clear()
            self.clear_projection()
            return

        if isinstance(event, ElementUpdated) and self._selected_event_id(event) not in selected_ids:
            return

        if isinstance(event, NetworkChanged) and not self.selection_manager.has_selection():
            return

        draft = self._read_selected_draft(object_id)
        if draft is not None:
            self._set_panel_target(self._project_draft(draft))
            return

        wire = self._read_selected_simple_wire(object_id)
        if wire is not None:
            presentation = self._read_selected_wire_presentation(wire.connection_id)
            state = ProjectionState(
                object_id=wire.connection_id,
                display_type="SIMPLE_WIRE",
                labels=(wire.connection_id, wire.kind),
                connectivity_refs=(wire.connection_id,),
                status="committed",
                identity_kind="simple_wire",
                presentation_id=(presentation.get("presentation_id") if presentation else None),
                connection_kind=(presentation.get("connection_kind") if presentation else wire.kind),
                endpoint_a=wire.endpoint_a,
                endpoint_b=wire.endpoint_b,
                route_ownership=(presentation.get("route_ownership") if presentation else None),
                route_points=(presentation.get("route_points") if presentation else ()),
            )
            self._set_panel_target(state)
            return

        element = self._read_selected_element(object_id)
        if element is None:
            # A committed Core object can disappear during undo. Do not leave
            # a stale Core identity selected after the authoritative read model
            # has reconciled. During temporary multi-selection projection,
            # however, preserve the caller's original selection snapshot.
            if not self._batch_projection_active:
                self.selection_manager.clear()
            self.clear_projection()
            return

        creation_type_by_read_model_type = {
            "buses": "bus", "grids": "grid", "generators": "generator",
            "synchronous_machines": "synchronous_machine", "loads": "load",
            "motors": "motor", "shunts": "shunt", "capacitors": "capacitor",
            "reactors": "reactor", "solar": "solar", "batteries": "battery",
            "current_transformers": "current_transformer",
            "potential_transformers": "potential_transformer",
            "capacitive_voltage_transformers": "cvt", "lines": "line",
            "cables": "cable", "transformers": "transformer", "breakers": "breaker",
            "switches": "switch", "disconnectors": "disconnector", "fuses": "fuse",
        }
        creation_type = creation_type_by_read_model_type.get(
            str(element.element_type).strip().lower(), str(element.element_type).strip().lower()
        )
        try:
            catalogue_definition = creation_definition_for(creation_type)
            metadata_by_id = {item.parameter_id: item for item in catalogue_definition.parameter_definitions}
        except (KeyError, TypeError, ValueError):
            metadata_by_id = {}

        read_parameters = tuple(getattr(element, "engineering_parameters", ()))
        projected_parameter_ids = {item.parameter_id for item in read_parameters}
        unavailable_parameters = tuple(
            EngineeringParameterState(
                parameter_id=metadata.parameter_id,
                value=None,
                unit=metadata.unit,
                datatype=metadata.datatype,
                choices=tuple(str(choice) for choice in metadata.choices),
                editable=False,
                derived=metadata.derived,
                validation=metadata.validation,
                display_name=metadata.display_name,
                required_before_create=metadata.required_before_create,
                default_value=metadata.default_value,
                minimum=metadata.minimum,
                maximum=metadata.maximum,
                inspectable=False,
            )
            for parameter_id, metadata in metadata_by_id.items()
            if parameter_id not in projected_parameter_ids
        )

        state = ProjectionState(
            object_id=element.object_id,
            display_type=element.element_type,
            labels=tuple(str(value) for value in element.labels.values()),
            connectivity_refs=tuple(element.connectivity_refs),
            status=self._status(element.attributes),
            identity_kind="element",
            engineering_parameters=tuple(
                EngineeringParameterState(
                    parameter_id=item.parameter_id,
                    value=item.value,
                    unit=item.unit,
                    datatype=item.datatype,
                    choices=item.choices,
                    editable=item.editable,
                    derived=item.derived,
                    validation=item.validation,
                    coupling_group=item.coupling_group,
                    topology_impact=item.topology_impact,
                    study_impact=item.study_impact,
                    display_name=metadata_by_id[item.parameter_id].display_name if item.parameter_id in metadata_by_id else item.parameter_id,
                    required_before_create=metadata_by_id[item.parameter_id].required_before_create if item.parameter_id in metadata_by_id else False,
                    default_value=metadata_by_id[item.parameter_id].default_value if item.parameter_id in metadata_by_id else None,
                    minimum=metadata_by_id[item.parameter_id].minimum if item.parameter_id in metadata_by_id else None,
                    maximum=metadata_by_id[item.parameter_id].maximum if item.parameter_id in metadata_by_id else None,
                )
                for item in read_parameters
            ) + unavailable_parameters,
        )
        self._set_panel_target(state)

    def _is_selected_protection_target(self, event: ProtectionChanged) -> bool:
        """Match a config lifecycle event to its affected relay identity.

        The event carries affected relay IDs from the transaction result, so
        delete and update transitions remain attributable even when the
        post-command Protection read model no longer contains the old target.
        """
        if self.application is None:
            return False
        if event.payload.get("operation") not in {"execute", "undo", "redo"}:
            return False

        selected_ids = {str(value) for value in self.selection_manager.get_selected_ids()}
        if not selected_ids:
            return False

        lifecycle = getattr(self.application, "project_lifecycle", None)
        context = getattr(lifecycle, "context", None)
        if context is None:
            return False
        if str(event.payload.get("project_id") or "") != str(context.project_id):
            return False
        event_generation = event.payload.get("activation_generation")
        if event_generation is None:
            return False
        try:
            if int(event_generation) != int(lifecycle.activation_generation):
                return False
        except (AttributeError, TypeError, ValueError):
            return False

        affected = event.payload.get("affected_relay_ids")
        if not isinstance(affected, (tuple, list, set, frozenset)):
            relay_id = event.payload.get("relay_id")
            affected = (relay_id,) if relay_id is not None else ()
        affected_ids = {str(value) for value in affected if value is not None and str(value)}
        return bool(affected_ids.intersection(selected_ids))

    def clear_projection(self) -> None:
        panel = self.properties_panel
        if panel is None:
            return
        clear = getattr(panel, "clear_target", None)
        if callable(clear):
            clear()
            return
        setter = getattr(panel, "set_target", None)
        if callable(setter):
            setter(None)

    def dispose(self) -> None:
        if self._disposed:
            return
        signal = getattr(self.selection_manager, "selection_changed", None)
        disconnect = getattr(signal, "disconnect", None)
        if callable(disconnect):
            try:
                disconnect(self._on_selection_changed)
            except (RuntimeError, TypeError):
                pass
        self.clear_projection()
        self.application = None
        self.properties_panel = None
        self._disposed = True

    def _on_selection_changed(self, selected_ids: Any) -> None:
        del selected_ids
        if not self._disposed:
            self.refresh()

    def _read_selected_draft(self, object_id: Any) -> Mapping[str, Any] | None:
        if self.application is None or not isinstance(object_id, str):
            return None
        snapshot = self.application.read_draft_network()
        if not snapshot:
            return None
        for item in tuple(snapshot.get("equipment", ())):
            if str(item.get("draft_id", "")) == object_id:
                return item
        return None

    @staticmethod
    def _project_draft(item: Mapping[str, Any]) -> ProjectionState:
        equipment_type = str(item.get("equipment_type", "")).strip().lower()
        terminal_contract = tuple(str(value) for value in item.get("terminal_contract", ()))
        definition = creation_definition_for(equipment_type, terminal_contract)
        values = dict(item.get("engineering_data", {}))
        parameters = tuple(
            EngineeringParameterState(
                parameter_id=parameter.parameter_id,
                value=values.get(parameter.parameter_id),
                unit=parameter.unit,
                datatype=parameter.datatype,
                choices=tuple(str(choice) for choice in parameter.choices),
                editable=parameter.editable,
                derived=parameter.derived,
                validation=parameter.validation,
                display_name=parameter.display_name,
                required_before_create=parameter.required_before_create,
                default_value=parameter.default_value,
                minimum=parameter.minimum,
                maximum=parameter.maximum,
                inspectable=True,
            )
            for parameter in definition.parameter_definitions
        )
        return ProjectionState(
            object_id=str(item["draft_id"]),
            display_type=equipment_type,
            labels=(str(item.get("display_name") or equipment_type), "Draft"),
            # Later-configuration fields remain pending review, not universally
            # mandatory. Study applicability is resolved by owning workflows.
            status=(
                "configuration_review_required"
                if any(
                    disposition.get("kind") == "later_configuration"
                    for disposition in dict(item.get("command_field_dispositions", {})).values()
                )
                else "draft"
            ),
            engineering_parameters=parameters,
            identity_kind="draft",
            geometry=item.get("placement"),
            placement=(
                None if item.get("placement") is None
                else (float(item["placement"][0]), float(item["placement"][1]))
            ),
            terminal_contract=terminal_contract,
            endpoint_references=dict(item.get("endpoints", {})),
            validation_state=dict(item.get("validation_state", {})),
        )

    def _read_selected_simple_wire(self, object_id: Any) -> Any | None:
        if self.application is None or not isinstance(object_id, str):
            return None
        try:
            return self.application.read_simple_wire(object_id)
        except (KeyError, RuntimeError, ValueError):
            return None

    def _read_selected_wire_presentation(self, connection_id: str) -> dict[str, Any] | None:
        if self.application is None:
            return None
        document = getattr(getattr(self.application, "presentation", None), "model", None)
        if document is None:
            return None
        matches = tuple(
            connection
            for connection in getattr(document, "connections", ())
            if str(getattr(connection, "properties", {}).get("core_connection_id", "")) == connection_id
        )
        if len(matches) != 1:
            return None
        connection = matches[0]
        route = getattr(connection, "route", None)
        return {
            "presentation_id": str(connection.connection_id),
            "connection_kind": str(connection.properties.get("connection_kind", "SIMPLE_WIRE")),
            "route_ownership": getattr(route, "ownership", None),
            "route_points": tuple(getattr(route, "points", ()) or ()),
        }

    def _read_selected_element(self, object_id: Any) -> Any | None:
        if self.application is None:
            return None
        network = self.application.read_network()
        for element in getattr(network, "elements", ()):
            if getattr(element, "object_id", None) == object_id:
                element_type = getattr(element, "element_type", None)
                if not element_type:
                    return None
                return self.application.read_element(element_type, str(object_id))

        try:
            protection = self.application.read_protection()
        except (RuntimeError, KeyError):
            return None
        relay = next((item for item in protection.relays if item.object_id == str(object_id)), None)
        if relay is None:
            return None
        return type(
            "_RelayProjectionReadModel",
            (),
            {
                "object_id": relay.object_id,
                "element_type": "relay",
                "labels": {"name": relay.name, "function_type": relay.function_type},
                "connectivity_refs": (),
                "attributes": {
                    "relay_type": relay.relay_type,
                    "plugin_id": relay.plugin_id,
                    "in_service": relay.in_service,
                    "enabled": relay.enabled,
                    "blocked": relay.blocked,
                    "picked_up": relay.picked_up,
                    "tripped": relay.tripped,
                },
                "engineering_parameters": (),
            },
        )()

    def _set_panel_target(self, state: ProjectionState) -> None:
        panel = self.properties_panel
        if panel is None:
            return
        setter = getattr(panel, "set_target", None)
        if not callable(setter):
            raise TypeError("properties_panel must provide set_target().")
        setter(state)

    @staticmethod
    def _status(attributes: Any) -> str | None:
        if not hasattr(attributes, "get"):
            return None
        value = attributes.get("status")
        if value is not None:
            return str(value)
        if "in_service" in attributes:
            return "in_service" if attributes.get("in_service") else "out_of_service"
        return None

    @staticmethod
    def _selected_event_id(event: Any) -> Any:
        payload = getattr(event, "payload", {})
        return payload.get("element_id") or payload.get("connection_id")

    def _ensure_active(self) -> None:
        if self._disposed:
            raise RuntimeError("SelectionProjectionCoordinator has been disposed.")


__all__ = ["SelectionProjectionCoordinator"]
