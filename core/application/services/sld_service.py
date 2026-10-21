# ============================================================
# File: core/application/services/sld_service.py
# GridForge V2 — Application SLD presentation service
# Author: Subhendu Mishra
# ============================================================

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any
import math

from ..command import Command
from ..errors import ValidationError
from ..results import ApplicationResult
from ..transaction import Transaction


@dataclass(frozen=True, slots=True)
class SLDState:
    """Immutable snapshot of presentation-only SLD state."""
    nodes: tuple[dict[str, Any], ...] = ()
    connections: tuple[dict[str, Any], ...] = ()


class SLDService:
    """Application boundary for persistent SLD presentation state.

    The service performs presentation mutation only inside the Application
    transaction supplied by CommandManager. It never owns history or undo/redo.
    """

    INSERTION_POSITION_TOLERANCE = 5.0
    INSERTION_POSITION_TOLERANCE_SQ = INSERTION_POSITION_TOLERANCE ** 2

    COMMAND_TYPES = frozenset({
        "sld.set_node_position",
        "sld.set_node_presentation",
        "sld.add_node",
        "sld.remove_node",
        "sld.add_connection",
        "sld.remove_connection",
        "sld.set_connection_route",
        "sld.set_node_properties",
    })

    def __init__(
        self,
        document: Any,
        *,
        application: Any = None,
        symbol_presentation_factory: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self._document = None
        self._application = application
        self._symbol_presentation_factory = symbol_presentation_factory
        if application is not None:
            self.attach_application(application)
        self.bind_document(document)

    @property
    def document(self) -> Any:
        if self._document is None:
            raise RuntimeError("SLD service has no active document.")
        return self._document

    @property
    def is_bound(self) -> bool:
        return self._document is not None

    def bind_document(self, document: Any) -> None:
        """Bind only the Application-authoritative active presentation document."""
        if document is None:
            raise TypeError("SLDService requires an SLD document.")
        if self._application is not None:
            presentation = getattr(self._application, "presentation", None)
            if presentation is not document:
                raise RuntimeError(
                    "SLDService cannot bind a document that is not the "
                    "Application-authoritative active presentation."
                )
        self._document = document

    @staticmethod
    def presentation_connection_id(core_connection_id: str) -> str:
        """Return the deterministic persistent SLD presentation identity for a Core connection."""
        if not isinstance(core_connection_id, str) or not core_connection_id:
            raise ValueError("core_connection_id must be a non-empty string")
        return f"sld-wire-{core_connection_id}"

    def audit_simple_wire_projection(self, network: Any) -> tuple[dict[str, Any], ...]:
        """Audit Core/SLD Simple-Wire consistency without mutating the document.

        This method is the activation/read-side counterpart to the mutating
        reconcile_simple_wire_projection method. It never creates, removes, or
        rewrites persistent SLD state and never marks the document modified.
        """
        if network is None or not hasattr(network, "connectivity"):
            raise TypeError("network must expose the authoritative connectivity aggregate")

        core_connections = tuple(network.connectivity.connections)
        core_by_id = {str(connection.connection_id): connection for connection in core_connections}
        diagnostics: list[dict[str, Any]] = []
        companions: dict[str, Any] = {}

        for connection in self.document.model.connections:
            properties = connection.properties
            kind = str(properties.get("connection_kind", "")).upper()
            lifecycle = str(properties.get("lifecycle_state", "BOUND")).upper()
            core_id = properties.get("core_connection_id")
            owner = str(properties.get("presentation_owner", "")).lower()

            if kind != "SIMPLE_WIRE" and not core_id:
                continue
            if kind != "SIMPLE_WIRE":
                diagnostics.append({
                    "code": "ENDPOINT_MISMATCH",
                    "connection_id": str(connection.connection_id),
                    "reason": "core_connection_mapping_on_non_simple_wire",
                })
                continue
            if lifecycle == "ORPHANED":
                continue
            if lifecycle != "BOUND":
                diagnostics.append({
                    "code": "ENDPOINT_MISMATCH",
                    "connection_id": str(connection.connection_id),
                    "reason": f"unsupported_lifecycle_state:{lifecycle}",
                })
                continue
            if not core_id:
                diagnostics.append({
                    "code": "SLD_WIRE_WITHOUT_CORE",
                    "connection_id": str(connection.connection_id),
                })
                continue

            core_id = str(core_id)
            if core_id in companions:
                diagnostics.append({
                    "code": "DUPLICATE_PRESENTATION_COMPANION",
                    "connection_id": str(connection.connection_id),
                    "core_connection_id": core_id,
                })
                continue

            companions[core_id] = connection
            if core_id not in core_by_id:
                diagnostics.append({
                    "code": "SLD_WIRE_WITHOUT_CORE",
                    "connection_id": str(connection.connection_id),
                    "core_connection_id": core_id,
                    "presentation_owner": owner,
                })
                continue

            core = core_by_id[core_id]
            expected_a = self._sld_endpoint_from_mapping(core.endpoint_a.to_mapping())
            expected_b = self._sld_endpoint_from_mapping(core.endpoint_b.to_mapping())
            actual_a = connection.source_endpoint.to_dict() if connection.source_endpoint is not None else None
            actual_b = connection.target_endpoint.to_dict() if connection.target_endpoint is not None else None
            if actual_a != expected_a or actual_b != expected_b:
                diagnostics.append({
                    "code": "ENDPOINT_MISMATCH",
                    "connection_id": str(connection.connection_id),
                    "core_connection_id": core_id,
                    "presentation_owner": owner,
                })

        for core_id in core_by_id:
            if core_id not in companions:
                diagnostics.append({
                    "code": "CORE_WIRE_WITHOUT_SLD_COMPANION",
                    "connection_id": core_id,
                })
        return tuple(diagnostics)

    def reconcile_simple_wire_projection(self, network: Any) -> Callable[[], None]:
        """Reconcile Simple Wire projections with failure-safe partial-mutation recovery."""
        previous = tuple(connection.to_dict() for connection in self.document.model.connections)
        previous_by_id = {str(item["connection_id"]): item for item in previous}
        previous_modified = bool(self.document.modified)
        try:
            return self._reconcile_simple_wire_projection(network)
        except BaseException as primary:
            current_by_id = {
                str(connection.connection_id): connection.to_dict()
                for connection in self.document.model.connections
            }
            changed_ids = {
                connection_id
                for connection_id in set(previous_by_id) | set(current_by_id)
                if previous_by_id.get(connection_id) != current_by_id.get(connection_id)
            }
            recovery_errors: list[tuple[str, BaseException]] = []
            for connection_id in changed_ids:
                try:
                    if self.document.model.get_connection_optional(connection_id) is not None:
                        self.document.model.remove_connection(connection_id)
                except BaseException as recovery_error:
                    recovery_errors.append((f"remove partially reconciled connection {connection_id!r}", recovery_error))
            for connection_id in changed_ids:
                snapshot = previous_by_id.get(connection_id)
                if snapshot is None:
                    continue
                try:
                    if self.document.model.get_connection_optional(connection_id) is None:
                        self._restore_connection_snapshot(snapshot)
                except BaseException as recovery_error:
                    recovery_errors.append((f"restore prior connection {connection_id!r}", recovery_error))
            try:
                if previous_modified:
                    self.document.mark_modified()
                else:
                    self.document.mark_clean()
            except BaseException as recovery_error:
                recovery_errors.append(("restore document dirty marker", recovery_error))
            if recovery_errors:
                add_note = getattr(primary, "add_note", None)
                if callable(add_note):
                    for label, recovery_error in recovery_errors:
                        add_note(f"SLD reconciliation recovery failed ({label}): {recovery_error!r}")
            raise

    def _reconcile_simple_wire_projection(self, network: Any) -> Callable[[], None]:
        """Reconcile persistent projection companions against authoritative Core Simple Wires.
        
        This is a project-activation boundary operation. It does not create a
        command/history entry; the returned rollback restores the exact
        persistent SLD connection snapshot if activation fails.
        """
        if network is None or not hasattr(network, "connectivity"):
            raise TypeError("network must expose the authoritative connectivity aggregate")

        previous = tuple(connection.to_dict() for connection in self.document.model.connections)
        previous_by_id = {str(item["connection_id"]): item for item in previous}
        previous_modified = bool(self.document.modified)
        core_connections = tuple(network.connectivity.connections)
        core_by_id = {str(connection.connection_id): connection for connection in core_connections}

        companions = {}
        changed = False
        for connection in self.document.model.connections:
            kind = str(connection.properties.get("connection_kind", "")).upper()
            lifecycle = str(connection.properties.get("lifecycle_state", "BOUND")).upper()
            core_id = connection.properties.get("core_connection_id")

            if kind != "SIMPLE_WIRE" and not core_id:
                continue
            if kind != "SIMPLE_WIRE":
                raise ValueError(
                    f"SLD connection {connection.connection_id!r} has Core connection mapping but kind {kind!r}, expected SIMPLE_WIRE."
                )

            # An engineer-owned presentation intentionally survives Core
            # deletion as a document artifact. It is not an active companion
            # and therefore must never participate in Core-ID reconciliation.
            if lifecycle == "ORPHANED":
                if connection.properties.get("presentation_owner") != "engineer":
                    raise ValueError(
                        f"SLD Simple Wire {connection.connection_id!r} is ORPHANED "
                        "but is not engineer-owned."
                    )
                if core_id is not None:
                    raise ValueError(
                        f"ORPHANED SLD Simple Wire {connection.connection_id!r} "
                        "must not retain an active Core connection binding."
                    )
                continue

            if lifecycle != "BOUND":
                raise ValueError(
                    f"SLD Simple Wire {connection.connection_id!r} has unsupported lifecycle state {lifecycle!r}."
                )

            if not core_id and str(connection.connection_id).startswith("sld-wire-"):
                # Deterministic legacy migration is centralized here; UI never
                # reconstructs Core identity from a presentation string.
                core_id = str(connection.connection_id)[len("sld-wire-"):]
                connection.properties["core_connection_id"] = core_id
                changed = True
            if not core_id:
                raise ValueError(
                    f"SLD Simple Wire {connection.connection_id!r} has no Core connection mapping."
                )
            core_id = str(core_id)
            if core_id in companions:
                raise ValueError(
                    f"Multiple SLD Simple Wire companions map to Core connection {core_id!r}."
                )
            companions[core_id] = connection

        for core_id, core in core_by_id.items():
            if core_id in companions:
                connection = companions[core_id]
                expected_a = self._sld_endpoint_from_mapping(core.endpoint_a.to_mapping())
                expected_b = self._sld_endpoint_from_mapping(core.endpoint_b.to_mapping())
                actual_a = connection.source_endpoint.to_dict() if connection.source_endpoint is not None else None
                actual_b = connection.target_endpoint.to_dict() if connection.target_endpoint is not None else None
                if connection.properties.get("connection_kind") != "SIMPLE_WIRE":
                    raise ValueError(f"SLD connection {connection.connection_id!r} has an invalid connection kind.")
                if actual_a != expected_a or actual_b != expected_b:
                    if connection.properties.get("presentation_owner") != "projection":
                        raise ValueError(
                            f"Engineer-owned SLD Simple Wire {connection.connection_id!r} conflicts with Core endpoints."
                        )
                    snapshot = connection.to_dict()
                    self.document.model.remove_connection(connection.connection_id)
                    self.document.model.create_connection(
                        connection_id=snapshot["connection_id"],
                        source_node_id=snapshot["source_node_id"],
                        target_node_id=snapshot["target_node_id"],
                        source_endpoint=expected_a,
                        target_endpoint=expected_b,
                        route=snapshot.get("route"),
                        properties=dict(snapshot.get("properties", {}), core_connection_id=core_id),
                    )
                    companions[core_id] = self.document.model.get_connection(snapshot["connection_id"])
                    changed = True
                continue

            presentation_id = self.presentation_connection_id(core_id)
            if self.document.model.get_connection_optional(presentation_id) is not None:
                raise ValueError(
                    f"SLD connection identity collision for Core Simple Wire {core_id!r}: {presentation_id!r}."
                )
            source = self._sld_node_for_endpoint_mapping(core.endpoint_a.to_mapping())
            target = self._sld_node_for_endpoint_mapping(core.endpoint_b.to_mapping())
            self.document.model.create_connection(
                connection_id=presentation_id,
                source_node_id=source,
                target_node_id=target,
                source_endpoint=self._sld_endpoint_from_mapping(core.endpoint_a.to_mapping()),
                target_endpoint=self._sld_endpoint_from_mapping(core.endpoint_b.to_mapping()),
                properties={
                    "connection_kind": "SIMPLE_WIRE",
                    "presentation_owner": "projection",
                    "projection_source": "core_reconciliation",
                    "core_connection_id": core_id,
                },
            )
            companions[core_id] = self.document.model.get_connection(presentation_id)
            changed = True

        for core_id, connection in companions.items():
            if core_id not in core_by_id:
                raise ValueError(
                    f"SLD Simple Wire {connection.connection_id!r} has no authoritative Core connection {core_id!r}."
                )

        if changed:
            self.document.mark_modified()

        current_by_id = {
            str(connection.connection_id): connection.to_dict()
            for connection in self.document.model.connections
        }
        changed_ids = {
            connection_id
            for connection_id in set(previous_by_id) | set(current_by_id)
            if previous_by_id.get(connection_id) != current_by_id.get(connection_id)
        }

        def rollback() -> None:
            # Restore only connection identities touched by this reconciliation.
            # Unrelated engineer-owned connections must not be removed/recreated
            # as a side effect of activation compensation.
            for connection_id in changed_ids:
                if self.document.model.get_connection_optional(connection_id) is not None:
                    self.document.model.remove_connection(connection_id)
            for connection_id in changed_ids:
                snapshot = previous_by_id.get(connection_id)
                if snapshot is not None:
                    self._restore_connection_snapshot(snapshot)
            if previous_modified:
                self.document.mark_modified()
            else:
                self.document.mark_clean()

        return rollback

    def _sld_node_for_endpoint_mapping(self, mapping: Mapping[str, Any]) -> str:
        object_id = str(mapping.get("object_id") or "")
        if not object_id:
            raise ValueError("Simple Wire endpoint mapping requires object_id.")
        nodes = tuple(
            node for node in self.document.model.nodes
            if str(getattr(node, "equipment_id", "") or "") == object_id
        )
        if len(nodes) != 1:
            raise ValueError(
                f"Expected exactly one SLD node for Core endpoint object {object_id!r}; found {len(nodes)}."
            )
        return str(nodes[0].node_id)

    @staticmethod
    def _sld_endpoint_from_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "kind": "bus" if mapping.get("kind") == "bus" else "equipment",
            "node_id": str(mapping.get("object_id") or ""),
            "equipment_id": mapping.get("object_id") if mapping.get("kind") != "bus" else None,
            "terminal_role": mapping.get("terminal_role") if mapping.get("kind") != "bus" else None,
            "bus_id": mapping.get("object_id") if mapping.get("kind") == "bus" else None,
            "attachment_id": mapping.get("attachment_id") if mapping.get("kind") == "bus" else None,
        }

    def attach_application(self, application: Any) -> None:
        """Attach the Application whose presentation state is authoritative."""
        if application is None:
            raise TypeError("application must not be None")
        self._application = application
        if self._document is not None:
            presentation = getattr(application, "presentation", None)
            # Permit composition before the project-lifecycle presentation
            # contract is wired. Once an Application presentation exists, the
            # service remains bound to that exact authoritative document.
            if presentation is not None and presentation is not self._document:
                raise RuntimeError(
                    "Existing SLD document does not match the Application presentation."
                )

    @property
    def application(self) -> Any:
        return self._application

    def detach_document(self) -> Any:
        """Detach the active document so closed projects cannot be mutated."""
        document = self._document
        self._document = None
        return document

    def supports(self, command: Command) -> bool:
        return command.command_type in self.COMMAND_TYPES

    def execute(
        self,
        command: Command,
        transaction: Transaction,
        *,
        context: Any = None,
        authoritative_value: Any = None,
    ) -> ApplicationResult:
        """Apply one SLD command inside the canonical Application transaction.

        authoritative_value is the transaction-visible result of an
        originating Core mutation when SLD binding occurs before commit.
        """
        if not isinstance(command, Command):
            raise TypeError("command must be a Command")
        if not isinstance(transaction, Transaction):
            raise TypeError("transaction must be a Transaction")
        if not self.supports(command):
            raise ValueError(f"Unsupported SLD command: {command.command_type}")
        handler = {
            "sld.set_node_position": self._set_node_position,
            "sld.set_node_presentation": self._set_node_presentation,
            "sld.add_node": lambda cmd, tx: self._add_node(
                cmd, tx, authoritative_value=authoritative_value
            ),
            "sld.remove_node": self._remove_node,
            "sld.add_connection": self._add_connection,
            "sld.remove_connection": lambda cmd, tx: self._remove_connection(cmd, tx, context=context),
            "sld.set_connection_route": self._set_connection_route,
            "sld.set_node_properties": self._set_node_properties,
        }[command.command_type]
        document = self.document
        was_modified = bool(document.modified)

        def restore_dirty_state() -> None:
            if was_modified:
                document.mark_modified()
            else:
                document.mark_clean()

        # This finalizer belongs to immediate transaction rollback only. The
        # committed inverse journal still marks the document dirty on undo.
        transaction.record_rollback_finalizer(restore_dirty_state)
        return handler(command, transaction)

    def _set_node_position(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        node = self.document.model.get_node(p["node_id"])
        previous = tuple(node.position)
        previous_properties = deepcopy(node.properties)
        updated = (float(p["x"]), float(p["y"]))
        updated_properties = deepcopy(previous_properties)
        updated_properties["position_owner"] = "engineer"
        if tuple(float(value) for value in previous) == updated and updated_properties == previous_properties:
            return ApplicationResult.success_result(
                message="SLD node position is unchanged.",
                metadata={"presentation_operation": "set_node_position", "node_id": p["node_id"]},
            )
        self.document.set_node_position(p["node_id"], *updated)
        node.properties.clear()
        node.properties.update(updated_properties)
        self.document.mark_modified()

        def restore() -> None:
            target = self.document.model.get_node(p["node_id"])
            self.document.set_node_position(p["node_id"], *previous)
            target.properties.clear()
            target.properties.update(deepcopy(previous_properties))
            self.document.mark_modified()

        transaction.record_undo(restore)
        return ApplicationResult.success_result(
            message="SLD node position updated.",
            metadata={"presentation_operation": "set_node_position", "node_id": p["node_id"]},
        )

    def _set_node_presentation(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        node = self.document.model.get_node(p["node_id"])
        previous = None if node.presentation is None else node.presentation.to_dict()
        previous_properties = deepcopy(node.properties)
        requested = deepcopy(dict(p["presentation"])) if isinstance(p["presentation"], Mapping) else p["presentation"]
        updated_properties = deepcopy(previous_properties)
        updated_properties["symbol_owner"] = "engineer"
        updated_properties.setdefault(
            "presentation_owner",
            "projection" if updated_properties.get("projection_source") else "engineer",
        )
        if previous is not None and previous == requested and updated_properties == previous_properties:
            return ApplicationResult.success_result(
                message="SLD node presentation is unchanged.",
                metadata={"presentation_operation": "set_node_presentation", "node_id": p["node_id"]},
            )
        node.set_presentation(p["presentation"])
        node.properties.clear()
        node.properties.update(updated_properties)
        self.document.mark_modified()
        def restore() -> None:
            target = self.document.model.get_node(p["node_id"])
            if previous is None:
                target.clear_presentation()
            else:
                target.set_presentation(previous)
            target.properties.clear()
            target.properties.update(deepcopy(previous_properties))
            self.document.mark_modified()
        transaction.record_undo(restore)
        return ApplicationResult.success_result(
            message="SLD node presentation updated.",
            metadata={"presentation_operation": "set_node_presentation", "node_id": p["node_id"]},
        )

    def _add_node(
        self,
        command: Command,
        transaction: Transaction,
        *,
        authoritative_value: Any = None,
    ) -> ApplicationResult:
        p = command.payload
        equipment_id = p.get("equipment_id")
        if equipment_id is not None:
            self._validate_equipment_reference(
                str(equipment_id),
                authoritative_value=authoritative_value,
            )
        presentation_owner = str(p.get("presentation_owner", "engineer"))
        projection_source = p.get("projection_source")
        if presentation_owner not in {"engineer", "projection"}:
            raise ValueError("presentation_owner must be 'engineer' or 'projection'.")
        if projection_source is not None and presentation_owner != "projection":
            raise ValueError("projection_source requires presentation_owner='projection'.")
        properties = {"presentation_owner": presentation_owner}
        element_type = p.get("element_type")
        if element_type is not None:
            properties["element_type"] = str(element_type)
        if projection_source is not None:
            properties["projection_source"] = str(projection_source)
        if equipment_id is not None:
            properties["lifecycle_state"] = "BOUND"
        presentation_properties = p.get("presentation_properties", {})
        if not isinstance(presentation_properties, Mapping):
            raise TypeError("presentation_properties must be a mapping")
        properties.update(dict(presentation_properties))

        presentation = p.get("presentation")
        if presentation is None and equipment_id is not None:
            if self._symbol_presentation_factory is None:
                raise ValueError(
                    f"SLD equipment node {p['node_id']!r} has no symbol presentation "
                    "and no symbol presentation factory is configured."
                )
            if not isinstance(element_type, str) or not element_type.strip():
                raise ValueError(
                    "SLD equipment node creation requires its canonical element_type "
                    "when a default symbol presentation factory is configured."
                )
            presentation = self._symbol_presentation_factory(element_type)
        if presentation is not None and not isinstance(presentation, Mapping):
            raise TypeError(
                f"SLD symbol presentation for node {p['node_id']!r} must be a mapping "
                "accepted by SymbolBase.from_dict()."
            )
        if presentation is not None:
            # Copy the complete renderer-neutral representation without
            # rewriting symbol identity, rotation, geometry, or properties.
            presentation = dict(presentation)

        node_id = p["node_id"]
        # Reject identity collisions before registering compensation. In
        # particular, never let a failed create roll back a pre-existing node.
        if self.document.model.get_node_optional(node_id) is not None:
            raise ValueError(f"SLD node identity collision: {node_id!r}.")

        # Register compensation before entering model code. A malformed
        # SymbolBase payload can raise while create_node is constructing the
        # persistent presentation; rollback must still remove a partially
        # inserted node. The inverse is deliberately idempotent because the
        # model may have failed before insertion.
        def undo_added_node() -> None:
            if self.document.model.get_node_optional(node_id) is not None:
                self.document.model.remove_node(node_id)
                self.document.mark_modified()

        transaction.record_undo(undo_added_node)
        try:
            self.document.model.create_node(
                node_id=node_id,
                equipment_id=equipment_id,
                x=float(p["x"]),
                y=float(p["y"]),
                presentation=presentation,
                properties=properties,
            )
        except Exception as exc:
            raise ValueError(
                f"Could not create SLD node {node_id!r} with its symbol presentation: {exc}"
            ) from exc
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD node added.",
            metadata={"presentation_operation": "add_node", "node_id": p["node_id"]},
        )

    def _validate_equipment_reference(
        self,
        equipment_id: str,
        *,
        authoritative_value: Any = None,
    ) -> None:
        """Validate an SLD equipment association at the Application boundary.

        For an in-flight Core mutation, authoritative_value is the
        transaction-visible Core result. ReadModels remain post-commit
        projections and are not used for that binding.
        """
        if authoritative_value is not None:
            authoritative_id = getattr(authoritative_value, "id", None)
            if authoritative_id is None or str(authoritative_id) != equipment_id:
                raise ValueError(
                    f"SLD node equipment reference {equipment_id!r} does not "
                    "match the transaction-visible Core identity."
                )
            return

        if self._application is None:
            raise RuntimeError("SLDService requires an Application to validate equipment references.")
        read_model = self._application.read_network()
        if any(element.object_id == equipment_id for element in read_model.elements):
            return
        protection = self._application.read_protection()
        protection_elements = getattr(protection, "elements", None)
        if protection_elements is None:
            protection_elements = getattr(protection, "relays", ())
        if any(element.object_id == equipment_id for element in protection_elements):
            return
        raise ValueError(
            f"SLD node equipment reference {equipment_id!r} does not resolve to current Application read state."
        )

    @staticmethod
    def has_engineer_presentation_overrides(node: Any) -> bool:
        """Return whether a node contains authored presentation state."""
        properties = getattr(node, "properties", {}) or {}
        if properties.get("position_owner") == "engineer": return True
        if properties.get("symbol_owner") == "engineer": return True
        for key in ("labels_owner", "visual_properties_owner", "manual_geometry_owner", "presentation_fields_owner"):
            if properties.get(key) == "engineer": return True
        overrides = properties.get("engineer_presentation_overrides")
        if isinstance(overrides, Mapping) and bool(overrides): return True
        if properties.get("presentation_owner") == "engineer": return True
        return False

    @staticmethod
    def has_engineer_presentation_overrides_for_connection(connection: Any) -> bool:
        """Return whether a connection contains authored route/presentation state."""
        properties = getattr(connection, "properties", {}) or {}
        if properties.get("route_owner") == "engineer": return True
        route = getattr(connection, "route", None)
        if route is not None and getattr(route, "ownership", None) == "engineer": return True
        for key in ("labels_owner", "visual_properties_owner", "manual_geometry_owner", "presentation_fields_owner"):
            if properties.get(key) == "engineer": return True
        overrides = properties.get("engineer_presentation_overrides")
        if isinstance(overrides, Mapping) and bool(overrides): return True
        if properties.get("presentation_owner") == "engineer": return True
        return False

    @staticmethod
    def _set_node_lifecycle_state(node: Any, state: str) -> None:
        if state not in {"BOUND", "ORPHANED", "REMOVED"}: raise ValueError(f"Unsupported SLD node lifecycle state: {state!r}")
        node.properties["lifecycle_state"] = state

    @staticmethod
    def _set_connection_lifecycle_state(connection: Any, state: str) -> None:
        if state not in {"BOUND", "ORPHANED", "REMOVED"}: raise ValueError(f"Unsupported SLD connection lifecycle state: {state!r}")
        connection.properties["lifecycle_state"] = state
    def reconcile_element_update(
        self,
        *,
        equipment_id: str,
        element_type: str,
        core_object: Any,
        transaction: Transaction,
    ) -> None:
        """Reconcile presentation from the authoritative transaction-visible Core object.

        This is intentionally a service operation, not a second command/history
        mechanism. It is invoked by the Application pre-commit hook inside the
        transaction opened for the originating Core command. The Core object is
        the mutation result; ReadModels are never queried to establish the
        authoritative updated state.
        """
        if not isinstance(equipment_id, str) or not equipment_id:
            raise ValueError("equipment_id must be a non-empty string")
        node = self.document.model.get_node_by_equipment_id_optional(equipment_id)
        if node is None:
            return
        previous = node.to_dict()
        # Convert the already-mutated Core object into the existing
        # presentation projection shape without querying Application
        # ReadService. The Core object remains the authoritative source.
        from ..read_service import NetworkReadService
        projected = NetworkReadService._to_read_model(element_type, core_object)
        attributes = dict(projected.attributes)
        labels = dict(projected.labels)
        connectivity = tuple(projected.connectivity_refs)
        node.properties.update({
            "element_type": str(element_type),
            "labels": labels,
            "attributes": attributes,
            "terminal_ids": connectivity,
            "terminal_connectivity": tuple(attributes.get("terminal_connectivity", ())),
        })
        self._set_node_lifecycle_state(node, "BOUND")
        node.equipment_id = equipment_id
        transaction.record_undo(
            lambda snapshot=previous: self._restore_node_snapshot(snapshot)
        )
        self.document.mark_modified()

    @classmethod
    def _validate_insertion_geometry(
        cls,
        *,
        connection_id: str,
        old_connection: Any,
        source_node: Any,
        target_node: Any,
        insertion_position: tuple[float, float] | list[float],
        segment_index: int,
    ) -> tuple[float, float]:
        """Validate insertion geometry against exactly the selected SLD segment.

        The SLD document is authoritative for presentation route geometry.
        Core topology geometry is intentionally not consulted here.
        """
        if str(old_connection.properties.get("connection_kind", "")).upper() != "SIMPLE_WIRE":
            raise ValidationError(
                code="INVALID_INSERTION_CONNECTION",
                message="Electrical insertion requires a Simple Wire SLD connection.",
                details={"connection_id": str(connection_id), "reason": "not_simple_wire"},
            )

        if not isinstance(segment_index, int) or isinstance(segment_index, bool):
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion segment index must be an integer.",
                details={"connection_id": str(connection_id), "segment_index": segment_index, "reason": "invalid_segment_index_type"},
            )

        if not isinstance(insertion_position, (tuple, list)) or len(insertion_position) != 2:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion position must contain exactly two coordinates.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": insertion_position,
                    "reason": "invalid_coordinate_count",
                },
            )

        try:
            px, py = float(insertion_position[0]), float(insertion_position[1])
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion position coordinates must be numeric.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": tuple(insertion_position),
                    "reason": "non_numeric_coordinate",
                },
            ) from exc

        if not math.isfinite(px) or not math.isfinite(py):
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="Insertion position coordinates must be finite.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "reason": "non_finite_coordinate",
                },
            )

        try:
            source = (float(source_node.x), float(source_node.y))
            target = (float(target_node.x), float(target_node.y))
            route_points = tuple(
                (float(point[0]), float(point[1]))
                for point in old_connection.route.points
            )
        except (AttributeError, TypeError, ValueError, IndexError, OverflowError) as exc:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="The selected SLD connection has invalid route geometry.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "reason": "invalid_route_geometry",
                },
            ) from exc

        polyline = (source, *route_points, target)
        segment_count = len(polyline) - 1
        if segment_index < 0 or segment_index >= segment_count:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message=(
                    f"Insertion segment index {segment_index} is outside the "
                    f"route range for connection {connection_id}."
                ),
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "segment_count": segment_count,
                    "supplied_position": (px, py),
                    "reason": "segment_index_out_of_range",
                },
            )

        ax, ay = polyline[segment_index]
        bx, by = polyline[segment_index + 1]
        if not all(math.isfinite(value) for value in (ax, ay, bx, by)):
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message="The selected SLD route segment contains non-finite coordinates.",
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "reason": "non_finite_segment",
                },
            )

        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        if length_sq <= 0.0:
            closest_dx = px - ax
            closest_dy = py - ay
            distance_sq = closest_dx * closest_dx + closest_dy * closest_dy
        else:
            projection = ((px - ax) * dx + (py - ay) * dy) / length_sq
            projection = max(0.0, min(1.0, projection))
            closest_x = ax + projection * dx
            closest_y = ay + projection * dy
            closest_dx = px - closest_x
            closest_dy = py - closest_y
            distance_sq = closest_dx * closest_dx + closest_dy * closest_dy

        if not math.isfinite(distance_sq) or distance_sq > cls.INSERTION_POSITION_TOLERANCE_SQ:
            raise ValidationError(
                code="INVALID_INSERTION_POSITION",
                message=(
                    f"Insertion position is not on selected SLD route segment "
                    f"{segment_index} within the allowed tolerance."
                ),
                details={
                    "connection_id": str(connection_id),
                    "segment_index": int(segment_index),
                    "supplied_position": (px, py),
                    "distance_squared": float(distance_sq),
                    "tolerance": float(cls.INSERTION_POSITION_TOLERANCE),
                    "tolerance_squared": float(cls.INSERTION_POSITION_TOLERANCE_SQ),
                    "reason": "position_outside_selected_segment",
                },
            )

        return (px, py)

    def reconcile_insertion(
        self,
        *,
        connection_id: str,
        equipment_id: str,
        equipment_type: str,
        insertion_position: tuple[float, float],
        orientation: float,
        segment_index: int,
        input_endpoint: Mapping[str, Any],
        output_endpoint: Mapping[str, Any],
        replacement_connection_ids: tuple[str, str],
        transaction: Transaction,
        terminal_anchors: Mapping[str, tuple[float, float]] | None = None,
    ) -> None:
        """Atomically replace one persistent SLD wire with an inserted node and two wires.

        The operation mutates only the persistent SLD presentation companion
        inside the originating Application transaction. The old connection
        snapshot is retained exactly for undo.
        """
        old_id = self.presentation_connection_id(connection_id)
        old_connection = self.document.model.get_connection_optional(old_id)
        if old_connection is None:
            candidates = tuple(
                item
                for item in self.document.model.connections
                if str(item.properties.get("core_connection_id", "")) == str(connection_id)
            )
            if len(candidates) == 1:
                old_connection = candidates[0]
                old_id = old_connection.connection_id
            elif len(candidates) > 1:
                raise ValueError(
                    f"Multiple persistent SLD companions exist for Core connection {connection_id!r}."
                )
            else:
                raise ValidationError(
                    code="INVALID_INSERTION_CONNECTION",
                    message=(
                        f"No persistent SLD connection companion exists for "
                        f"Core connection {connection_id!r}."
                    ),
                    details={
                        "connection_id": str(connection_id),
                        "reason": "sld_connection_not_found",
                    },
                )

        first_core_id, second_core_id = replacement_connection_ids
        first_id = self.presentation_connection_id(first_core_id)
        second_id = self.presentation_connection_id(second_core_id)
        existing_node = self.document.model.get_node_by_equipment_id_optional(equipment_id)
        node_id = str(existing_node.node_id) if existing_node is not None else f"sld-core-{equipment_id}"
        if existing_node is not None:
            attached = tuple(
                item for item in self.document.model.connections
                if item.source_node_id == existing_node.node_id or item.target_node_id == existing_node.node_id
            )
            if attached:
                raise ValueError(
                    f"Existing equipment SLD node {node_id!r} already has persistent connection companions."
                )
        elif self.document.model.get_node_optional(node_id) is not None:
            raise ValueError(f"SLD node {node_id!r} already exists.")
        if self.document.model.get_connection_optional(first_id) is not None:
            raise ValueError(f"SLD connection {first_id!r} already exists.")
        if self.document.model.get_connection_optional(second_id) is not None:
            raise ValueError(f"SLD connection {second_id!r} already exists.")

        if str(old_connection.properties.get("connection_kind", "")).upper() != "SIMPLE_WIRE":
            raise ValueError("Electrical insertion requires a Simple Wire SLD companion.")
        snapshot = old_connection.to_dict()
        node_snapshot = existing_node.to_dict() if existing_node is not None else None
        previous_document_modified = bool(getattr(self.document, "modified", False))
        source_node = self.document.model.get_node(old_connection.source_node_id)
        target_node = self.document.model.get_node(old_connection.target_node_id)
        position = self._validate_insertion_geometry(
            connection_id=connection_id,
            old_connection=old_connection,
            source_node=source_node,
            target_node=target_node,
            insertion_position=insertion_position,
            segment_index=segment_index,
        )

        # Capture ownership/provenance before replacing the persistent
        # connection. These values belong to the authored SLD presentation and
        # must not be inferred from the insertion command itself.
        route = old_connection.route.to_dict()
        original_route_ownership = str(route.get("ownership", "auto")).lower()
        original_route_owner = old_connection.properties.get("route_owner")
        original_presentation_owner = old_connection.properties.get("presentation_owner")
        original_projection_source = old_connection.properties.get("projection_source")
        if original_presentation_owner not in (None, "engineer", "projection"):
            raise ValueError("SLD connection presentation ownership is unknown; insertion is rejected.")
        polyline = [
            (float(source_node.x), float(source_node.y)),
            *tuple(tuple(point) for point in old_connection.route.points),
            (float(target_node.x), float(target_node.y)),
        ]
        if segment_index < 0 or segment_index >= len(polyline) - 1:
            raise ValueError(
                f"Invalid insertion route segment index {segment_index} for connection {connection_id!r}."
            )
        anchors = dict(terminal_anchors or {})
        input_role = str(input_endpoint.get("terminal_role") or "")
        output_role = str(output_endpoint.get("terminal_role") or "")

        def rotated_anchor(role: str) -> tuple[float, float]:
            local = anchors.get(role)
            if local is None:
                return position
            lx, ly = float(local[0]), float(local[1])
            radians = math.radians(float(orientation))
            return (
                position[0] + lx * math.cos(radians) - ly * math.sin(radians),
                position[1] + lx * math.sin(radians) + ly * math.cos(radians),
            )

        input_anchor = rotated_anchor(input_role)
        output_anchor = rotated_anchor(output_role)
        first_polyline = [*polyline[: segment_index + 1], position, input_anchor]
        second_polyline = [output_anchor, position, *polyline[segment_index + 1 :]]

        def normalize_route(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
            normalized: list[tuple[float, float]] = []
            for point in points:
                value = (float(point[0]), float(point[1]))
                if normalized and value == normalized[-1]:
                    continue
                normalized.append(value)
            if str(route.get("ownership", "auto")).lower() != "engineer":
                compact: list[tuple[float, float]] = []
                for point in normalized:
                    if len(compact) >= 2:
                        ax, ay = compact[-2]
                        bx, by = compact[-1]
                        cx, cy = point
                        if abs((bx - ax) * (cy - by) - (by - ay) * (cx - bx)) <= 1e-9:
                            compact[-1] = point
                            continue
                    compact.append(point)
                normalized = compact
            return normalized

        def route_for(points: list[tuple[float, float]]) -> dict[str, Any]:
            normalized = normalize_route(points)
            return {
                "routing_mode": route["routing_mode"],
                "ownership": route["ownership"],
                "points": [list(point) for point in normalized[1:-1]],
            }

        def presentation_endpoint(reference: Mapping[str, Any]) -> dict[str, str]:
            kind = str(reference.get("kind", "")).lower()
            if kind == "terminal":
                if str(reference.get("object_id")) != str(equipment_id):
                    raise ValueError("Insertion terminal endpoint belongs to a different equipment identity.")
                return {
                    "kind": "equipment",
                    "node_id": node_id,
                    "equipment_id": str(equipment_id),
                    "terminal_role": str(reference["terminal_role"]),
                }
            raise ValueError("Inserted equipment Simple Wire endpoints must be terminal references.")

        input_presentation_endpoint = presentation_endpoint(input_endpoint)
        output_presentation_endpoint = presentation_endpoint(output_endpoint)
        properties = dict(old_connection.properties)
        properties["connection_kind"] = "SIMPLE_WIRE"
        if original_presentation_owner is None:
            properties["presentation_owner"] = "projection"
        # Preserve the original presentation/provenance contract. In
        # particular, an engineer-owned route must never become projection
        # owned merely because insertion changed the Core connectivity.
        if original_projection_source is None:
            properties.pop("projection_source", None)
        else:
            properties["projection_source"] = original_projection_source
        if original_route_ownership == "engineer":
            # Engineer-authored routing explicitly carries engineer ownership
            # on both replacement companions, even if an older persisted
            # document omitted the redundant route_owner property.
            properties["route_owner"] = original_route_owner or "engineer"
        elif original_route_owner is None:
            properties.pop("route_owner", None)
        else:
            properties["route_owner"] = original_route_owner
        properties["lifecycle_state"] = "BOUND"
        properties["insertion_parent_connection_id"] = connection_id

        if existing_node is not None:
            # Start from the exact authored node metadata and change only the
            # fields explicitly owned by this insertion: current Core binding,
            # insertion provenance, position, and the explicitly supplied
            # rotation. Ownership fields such as symbol/labels/manual geometry
            # remain untouched.
            node_properties = dict(existing_node.properties)
            node_properties.update({
                "lifecycle_state": "BOUND",
                "element_type": equipment_type,
                "rotation": float(orientation),
                "insertion_parent_connection_id": connection_id,
            })
        else:
            node_properties = {
                "presentation_owner": "engineer",
                "lifecycle_state": "BOUND",
                "element_type": equipment_type,
                "position_owner": "engineer",
                "symbol_owner": "engineer",
                "rotation": float(orientation),
                "insertion_parent_connection_id": connection_id,
            }
        presentation = None
        if existing_node is None and self._symbol_presentation_factory is not None:
            presentation = self._symbol_presentation_factory(equipment_type)

        def restore() -> None:
            for connection_id_to_remove in (first_id, second_id):
                if self.document.model.get_connection_optional(connection_id_to_remove) is not None:
                    self.document.model.remove_connection(connection_id_to_remove)
            if existing_node is None:
                if self.document.model.get_node_optional(node_id) is not None:
                    self.document.model.remove_node(node_id)
            else:
                node = self.document.model.get_node_optional(node_id)
                if node is None:
                    self._restore_node_snapshot(node_snapshot or {})
                else:
                    node.set_position(float(node_snapshot["x"]), float(node_snapshot["y"]))
                    if node_snapshot.get("presentation") is None:
                        node.clear_presentation()
                    else:
                        node.set_presentation(node_snapshot["presentation"])
                    node.properties.clear()
                    node.properties.update(dict(node_snapshot.get("properties", {})))
                    node.equipment_id = node_snapshot.get("equipment_id")
            if self.document.model.get_connection_optional(old_id) is None:
                self._restore_connection_snapshot(snapshot)
            if previous_document_modified:
                self.document.mark_modified()
            else:
                self.document.mark_clean()

        # Register the inverse before mutating the presentation so any failure
        # during the multi-object replacement is rollback-safe as well.
        transaction.record_undo(restore)

        self.document.model.remove_connection(old_id)
        if existing_node is None:
            self.document.model.create_node(
                node_id=node_id,
                equipment_id=equipment_id,
                x=position[0],
                y=position[1],
                presentation=presentation,
                properties=node_properties,
            )
        else:
            existing_node.set_position(position[0], position[1])
            existing_node.properties.update(node_properties)
        self.document.model.create_connection(
            connection_id=first_id,
            source_node_id=old_connection.source_node_id,
            target_node_id=node_id,
            source_endpoint=old_connection.source_endpoint.to_dict() if old_connection.source_endpoint is not None else None,
            target_endpoint=input_presentation_endpoint,
            route=route_for(first_polyline),
            properties=dict(properties, core_connection_id=first_core_id),
        )
        self.document.model.create_connection(
            connection_id=second_id,
            source_node_id=node_id,
            target_node_id=old_connection.target_node_id,
            source_endpoint=output_presentation_endpoint,
            target_endpoint=old_connection.target_endpoint.to_dict() if old_connection.target_endpoint is not None else None,
            route=route_for(second_polyline),
            properties=dict(properties, core_connection_id=second_core_id),
        )
        self.document.mark_modified()

    def reconcile_element_delete(
        self,
        *,
        equipment_id: str,
        transaction: Transaction,
    ) -> str:
        """Remove or orphan a Core-bound SLD node using field-level ownership."""
        node = self.document.model.get_node_by_equipment_id_optional(equipment_id)
        if node is None:
            return "REMOVED"
        snapshot = node.to_dict()
        attached = tuple(
            connection for connection in self.document.model.connections
            if connection.source_node_id == node.node_id or connection.target_node_id == node.node_id
        )
        connection_snapshots = tuple(connection.to_dict() for connection in attached)

        if not self.has_engineer_presentation_overrides(node):
            self.document.model.remove_node(node.node_id)
            self.document.mark_modified()
            def restore(snapshot=snapshot, connection_snapshots=connection_snapshots) -> None:
                self._restore_node_snapshot(snapshot)
                for item in connection_snapshots:
                    if self.document.model.get_connection_optional(item["connection_id"]) is None:
                        self._restore_connection_snapshot(item)
            transaction.record_undo(restore)
            return "REMOVED"

        node.equipment_id = None
        node.properties.pop("projection_source", None)
        node.properties["lifecycle_state"] = "ORPHANED"
        node.properties["orphaned_equipment_id"] = equipment_id

        for connection in attached:
            if self.has_engineer_presentation_overrides_for_connection(connection):
                if connection.source_node_id == node.node_id:
                    connection.source_endpoint = None
                if connection.target_node_id == node.node_id:
                    connection.target_endpoint = None
                connection.properties.pop("projection_source", None)
                self._set_connection_lifecycle_state(connection, "ORPHANED")
            else:
                self.document.model.remove_connection(connection.connection_id)

        self.document.mark_modified()
        def restore(snapshot=snapshot, connection_snapshots=connection_snapshots) -> None:
            self._restore_node_snapshot(snapshot)
            for connection in tuple(self.document.model.connections):
                if connection.connection_id in {item["connection_id"] for item in connection_snapshots}:
                    self.document.model.remove_connection(connection.connection_id)
            for item in connection_snapshots:
                self._restore_connection_snapshot(item)
        transaction.record_undo(restore)
        return "ORPHANED"

    def reconcile_connection_delete(
        self,
        *,
        connection_id: str,
        transaction: Transaction,
    ) -> str:
        """Remove or orphan a Core-bound SLD connection by route ownership."""
        connection = self.document.model.get_connection_optional(connection_id)
        if connection is None:
            # Core Simple Wire identity and persistent SLD presentation identity
            # are distinct. Resolve the active companion through its persisted
            # Core mapping, or resolve an already-orphaned authored presentation
            # through its non-authoritative deletion provenance so redo can record
            # the same SLD inverse without resurrecting a Core binding.
            candidates = tuple(
                item
                for item in self.document.model.connections
                if item.properties.get("core_connection_id") == connection_id
                or (
                    str(item.properties.get("lifecycle_state", "")).upper() == "ORPHANED"
                    and item.properties.get("orphaned_from_core_connection_id") == connection_id
                )
            )
            if len(candidates) > 1:
                raise ValueError(
                    f"Multiple SLD connection companions map to Core connection {connection_id!r}."
                )
            connection = candidates[0] if candidates else None
        if connection is None:
            return "REMOVED"
        presentation_id = connection.connection_id
        snapshot = connection.to_dict()
        if not self.has_engineer_presentation_overrides_for_connection(connection):
            self.document.model.remove_connection(presentation_id)
            self.document.mark_modified()
            transaction.record_undo(lambda snapshot=snapshot: self._restore_connection_snapshot(snapshot))
            return "REMOVED"
        connection.source_endpoint = None
        connection.target_endpoint = None
        connection.properties.pop("projection_source", None)
        # Core deletion severs the binding. The authored SLD presentation
        # remains, but its old Core ID is no longer authoritative identity.
        connection.properties.pop("core_connection_id", None)
        connection.properties["orphaned_from_core_connection_id"] = connection_id
        connection.properties["lifecycle_state"] = "ORPHANED"
        self.document.mark_modified()
        transaction.record_undo(lambda snapshot=snapshot: self._restore_connection_snapshot(snapshot))
        return "ORPHANED"

    def _restore_node_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self.document.model.create_node(
            node_id=snapshot["node_id"],
            equipment_id=snapshot.get("equipment_id"),
            x=snapshot.get("x", 0.0),
            y=snapshot.get("y", 0.0),
            presentation=snapshot.get("presentation"),
            properties=snapshot.get("properties", {}),
        )
        for item in snapshot.get("connections", ()):
            self._restore_connection_snapshot(item)
        self.document.mark_modified()

    def _restore_connection_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self.document.model.create_connection(
            connection_id=snapshot["connection_id"],
            source_node_id=snapshot["source_node_id"],
            target_node_id=snapshot["target_node_id"],
            source_endpoint=snapshot.get("source_endpoint"),
            target_endpoint=snapshot.get("target_endpoint"),
            route=snapshot.get("route"),
            properties=snapshot.get("properties", {}),
        )
        self.document.mark_modified()

    def _remove_node(self, command: Command, transaction: Transaction) -> ApplicationResult:
        node_id = command.payload["node_id"]
        node = self.document.model.get_node(node_id)
        projection_source = command.payload.get("projection_source")
        if projection_source is None:
            self._require_engineer_owned_node(node)
        else:
            if node.properties.get("projection_source") != projection_source:
                raise ValueError("SLD node projection ownership does not match the removal command.")
        node_snapshot = node.to_dict()
        connection_snapshots = tuple(
            connection.to_dict()
            for connection in self.document.model.connections
            if connection.source_node_id == node_id or connection.target_node_id == node_id
        )
        self.document.model.remove_node(node_id)
        self.document.mark_modified()

        def restore() -> None:
            # Refuse to overwrite identities that may have been created by an
            # independent mutation after this removal. A failed restoration
            # must surface through Transaction.rollback()/CommandManager.undo().
            if self.document.model.get_node_optional(node_snapshot["node_id"]) is not None:
                raise ValueError(
                    f"Cannot restore removed SLD node; identity already exists: "
                    f"{node_snapshot['node_id']!r}."
                )
            existing_connections = {
                item["connection_id"]
                for item in connection_snapshots
                if self.document.model.get_connection_optional(item["connection_id"]) is not None
            }
            if existing_connections:
                raise ValueError(
                    "Cannot restore removed SLD node; connection identities already "
                    f"exist: {sorted(existing_connections)!r}."
                )
            self.document.model.create_node(
                node_id=node_snapshot["node_id"],
                equipment_id=node_snapshot.get("equipment_id"),
                x=node_snapshot.get("x", 0.0),
                y=node_snapshot.get("y", 0.0),
                presentation=node_snapshot.get("presentation"),
                properties=node_snapshot.get("properties", {}),
            )
            for snapshot in connection_snapshots:
                self.document.model.create_connection(
                    connection_id=snapshot["connection_id"],
                    source_node_id=snapshot["source_node_id"],
                    target_node_id=snapshot["target_node_id"],
                    source_endpoint=snapshot.get("source_endpoint"),
                    target_endpoint=snapshot.get("target_endpoint"),
                    route=snapshot.get("route"),
                    properties=snapshot.get("properties", {}),
                )
            self.document.mark_modified()

        transaction.record_undo(restore)
        return ApplicationResult.success_result(
            message="SLD node removed.",
            metadata={"presentation_operation": "remove_node", "node_id": node_id},
        )

    def _add_connection(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        source_endpoint = p.get("source_endpoint")
        target_endpoint = p.get("target_endpoint")
        route = p.get("route")
        presentation_owner = str(p.get("presentation_owner", "engineer"))
        projection_source = p.get("projection_source")
        if presentation_owner not in {"engineer", "projection"}:
            raise ValueError("presentation_owner must be 'engineer' or 'projection'.")
        if projection_source is not None and presentation_owner != "projection":
            raise ValueError("projection_source requires presentation_owner='projection'.")
        properties = {"presentation_owner": presentation_owner}
        connection_kind = p.get("connection_kind")
        if connection_kind is not None:
            properties["connection_kind"] = str(connection_kind)
        connection_id = p["connection_id"]
        if self.document.model.get_connection_optional(connection_id) is not None:
            raise ValueError(f"SLD connection identity collision: {connection_id!r}.")
        connection_id = p["connection_id"]
        if self.document.model.get_connection_optional(connection_id) is not None:
            raise ValueError(f"SLD connection identity collision: {connection_id!r}.")
        core_connection_id = p.get("core_connection_id")
        if core_connection_id is not None:
            core_connection_id = str(core_connection_id).strip()
            if not core_connection_id:
                raise ValueError("core_connection_id must be a non-empty string when provided.")
            properties["core_connection_id"] = core_connection_id
        if projection_source is not None:
            properties["projection_source"] = str(projection_source)
        if connection_kind is not None:
            properties["lifecycle_state"] = "BOUND"
        if isinstance(route, Mapping) and route.get("ownership") == "engineer":
            properties["route_owner"] = "engineer"
        def undo_added_connection() -> None:
            # Register compensation before model mutation, and undo only the
            # identity owned by this command. A pre-existing identity was
            # rejected above, so a partial create can be safely compensated.
            if self.document.model.get_connection_optional(connection_id) is not None:
                self.document.model.remove_connection(connection_id)
                self.document.mark_modified()

        transaction.record_undo(undo_added_connection)
        self.document.model.create_connection(
            connection_id=connection_id,
            source_node_id=p["source_node_id"],
            target_node_id=p["target_node_id"],
            source_endpoint=source_endpoint,
            target_endpoint=target_endpoint,
            route=route,
            properties=properties,
        )
        self.document.mark_modified()
        return ApplicationResult.success_result(
            message="SLD connection added.",
            metadata={"presentation_operation": "add_connection", "connection_id": p["connection_id"]},
        )

    def _set_node_properties(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        node = self.document.model.get_node(p["node_id"])
        previous = deepcopy(node.properties)
        properties = p["properties"]
        if not isinstance(properties, Mapping):
            raise TypeError("properties must be a mapping")
        semantic_keys = {
            "equipment_id", "element_type", "terminal_ids", "terminal_connectivity",
            "attributes", "projection_source", "lifecycle_state",
        }
        if semantic_keys.intersection(properties):
            raise ValueError("SLD semantic binding fields are Application-owned and cannot be edited as presentation properties.")
        updated_properties = deepcopy(previous)
        updated_properties.update(deepcopy(dict(properties)))
        if "labels" in properties and "labels_owner" not in properties:
            updated_properties["labels_owner"] = "engineer"
        if "visual_properties" in properties and "visual_properties_owner" not in properties:
            updated_properties["visual_properties_owner"] = "engineer"
        if "manual_geometry" in properties and "manual_geometry_owner" not in properties:
            updated_properties["manual_geometry_owner"] = "engineer"
        if updated_properties == previous:
            return ApplicationResult.success_result(
                message="SLD node presentation properties are unchanged.",
                metadata={"presentation_operation": "set_node_properties", "node_id": p["node_id"]},
            )
        node.properties.clear()
        node.properties.update(updated_properties)
        self.document.mark_modified()
        def restore_properties() -> None:
            node.properties.clear()
            node.properties.update(deepcopy(previous))
            self.document.mark_modified()

        transaction.record_undo(restore_properties)
        return ApplicationResult.success_result(
            message="SLD node presentation properties updated.",
            metadata={"presentation_operation": "set_node_properties", "node_id": p["node_id"]},
        )

    def _set_connection_route(self, command: Command, transaction: Transaction) -> ApplicationResult:
        p = command.payload
        connection = self.document.model.get_connection(p["connection_id"])
        owner = connection.properties.get("presentation_owner")
        if owner not in (None, "engineer", "projection"):
            raise ValueError("SLD connection ownership is unknown; route edit is rejected.")
        previous = connection.route
        previous_properties = deepcopy(connection.properties)
        updated = self.document.model.get_connection(p["connection_id"]).route.__class__.from_dict(p["route"])
        if updated.ownership != "engineer":
            updated = updated.__class__(routing_mode=updated.routing_mode, ownership="engineer", points=updated.points)
        updated_properties = deepcopy(previous_properties)
        updated_properties["route_owner"] = "engineer"
        updated_properties.setdefault("presentation_owner", "projection" if updated_properties.get("projection_source") else "engineer")
        if updated == previous and updated_properties == previous_properties:
            return ApplicationResult.success_result(
                message="SLD connection route is unchanged.",
                metadata={"presentation_operation": "set_connection_route", "connection_id": p["connection_id"]},
            )
        connection.route = updated
        connection.properties.clear()
        connection.properties.update(updated_properties)
        self.document.mark_modified()
        def restore_route() -> None:
            connection.route = previous
            connection.properties.clear()
            connection.properties.update(deepcopy(previous_properties))
            self.document.mark_modified()

        transaction.record_undo(restore_route)
        return ApplicationResult.success_result(
            message="SLD connection route updated.",
            metadata={"presentation_operation": "set_connection_route", "connection_id": p["connection_id"]},
        )

    @staticmethod
    def _require_engineer_owned_node(node: Any) -> None:
        source = node.properties.get("projection_source")
        owner = node.properties.get("presentation_owner")
        if source in {"application_read_model", "protection_read_model"}:
            raise ValueError(
                "Projection-owned SLD nodes cannot be removed through "
                "engineer-owned presentation commands."
            )
        if source is not None or owner not in (None, "engineer"):
            raise ValueError(
                "SLD node ownership is unknown or invalid; removal is rejected."
            )

    @staticmethod
    def _require_engineer_owned_connection(connection: Any) -> None:
        source = connection.properties.get("projection_source")
        owner = connection.properties.get("presentation_owner")
        if source in {"application_read_model", "protection_read_model"}:
            raise ValueError(
                "Projection-owned SLD connections cannot be removed through "
                "engineer-owned presentation commands."
            )
        if owner != "engineer":
            raise ValueError(
                "SLD connection ownership is unknown; removal is rejected."
            )

    def _remove_connection(self, command: Command, transaction: Transaction, *, context: Any = None) -> ApplicationResult:
        connection_id = command.payload["connection_id"]
        connection = self.document.model.get_connection(connection_id)

        # Projection-owned Simple Wire presentation is not an independent
        # electrical authority. Electrical deletion must originate from the
        # Core SimpleWire command; Application pre-commit then removes the
        # persistent SLD companion in the same transaction.
        if (
            connection.properties.get("projection_source") == "application_read_model"
            and connection.properties.get("connection_kind") == "SIMPLE_WIRE"
        ):
            raise ValueError(
                "Projection-owned Simple Wire SLD connections cannot be removed "
                "through an SLD-only command; use the Application Simple Wire "
                "deletion command."
            )

        self._require_engineer_owned_connection(connection)
        snapshot = connection.to_dict()
        self.document.model.remove_connection(connection_id)
        self.document.mark_modified()

        def restore() -> None:
            restored_id = snapshot["connection_id"]
            if self.document.model.get_connection_optional(restored_id) is not None:
                raise ValueError(
                    f"Cannot restore removed SLD connection; identity already exists: {restored_id!r}."
                )
            self.document.model.create_connection(
                connection_id=restored_id,
                source_node_id=snapshot["source_node_id"],
                target_node_id=snapshot["target_node_id"],
                source_endpoint=snapshot.get("source_endpoint"),
                target_endpoint=snapshot.get("target_endpoint"),
                route=snapshot.get("route"),
                properties=deepcopy(snapshot.get("properties", {})),
            )
            self.document.mark_modified()

        transaction.record_undo(restore)
        return ApplicationResult.success_result(
            message="SLD connection removed.",
            metadata={"presentation_operation": "remove_connection", "connection_id": connection_id},
        )


__all__ = ["SLDService", "SLDState"]
